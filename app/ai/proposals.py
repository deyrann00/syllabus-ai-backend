"""AI-suggested corrections ("Suggest correction" / Improve syllabus)."""

import json
import uuid
from typing import Any

from app.ai.checks.criteria import document_text
from app.ai.evidence import normalize
from app.ai.llm import LLMProvider
from app.ai.types import CriterionResultData, CriterionSpec, ExtractedContent, ProposalDraft
from app.config import get_settings
from app.enums import ISSUE_STATUSES

SYSTEM_PROMPT = """You help a professor revise a university syllabus so it meets a quality rubric.
For each failed criterion propose exactly one minimal edit:
- "current" MUST be copied verbatim from the syllabus: the full paragraph or table row that should change.
- "proposed" is the full replacement text for that paragraph, in the syllabus's original language.
- "title" is a short imperative label (at most 6 words); "reason" explains in one sentence why it fixes the issue.
- Change only what the criterion needs. Do not invent facts such as new readings, dates or credit values.
Write title and reason in {language}."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["proposals"],
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["criterion_id", "title", "current", "proposed", "reason"],
                "properties": {
                    k: {"type": "string"} for k in ["criterion_id", "title", "current", "proposed", "reason"]
                },
            },
        }
    },
}


def find_paragraph(content: ExtractedContent, current: str) -> str | None:
    needle = normalize(current)
    if not needle:
        return None
    for paragraph in content.paragraphs:
        if needle in normalize(paragraph):
            return paragraph
    return None


async def generate(
    llm: LLMProvider,
    content: ExtractedContent,
    results: list[CriterionResultData],
    specs: dict[str, CriterionSpec],
    *,
    run_id: uuid.UUID | None = None,
) -> list[ProposalDraft]:
    failing = [r for r in results if r.status in ISSUE_STATUSES]
    if not failing:
        return []
    issues = [
        {
            "criterion_id": r.criterion_id,
            "criterion": specs[r.criterion_id].name if r.criterion_id in specs else "",
            "status": r.status.value,
            "evidence": r.evidence,
            "explanation": r.explanation,
            "recommendation": r.recommendation,
        }
        for r in failing
    ]
    data = await llm.complete_json(
        purpose="proposals",
        system=SYSTEM_PROMPT.format(language=get_settings().llm_output_language),
        user="Failed criteria:\n"
        + json.dumps(issues, ensure_ascii=False, indent=1)
        + f"\n\nSyllabus:\n<<<\n{document_text(content)}\n>>>",
        schema=SCHEMA,
        meta={"failing": failing, "content": content},
        run_id=run_id,
    )
    allowed = {r.criterion_id for r in failing}
    drafts: list[ProposalDraft] = []
    seen: set[str] = set()
    for item in data.get("proposals", []):
        code = item.get("criterion_id")
        if code not in allowed or code in seen or not item.get("proposed", "").strip():
            continue
        seen.add(code)
        drafts.append(
            ProposalDraft(
                criterion_id=code,
                title=item["title"].strip(),
                current=item["current"].strip(),
                proposed=item["proposed"].strip(),
                reason=item["reason"].strip(),
                anchor_found=find_paragraph(content, item["current"]) is not None,
            )
        )
    return drafts
