"""LLM evaluation of rubric criteria, one call per principle."""

import json
import uuid
from typing import Any

from app.ai.llm import LLMProvider
from app.ai.types import CriterionResultData, CriterionSpec, ExtractedContent, TechnicalReport
from app.config import get_settings
from app.enums import ResultStatus

MAX_DOCUMENT_CHARS = 60_000

SYSTEM_PROMPT = """You review university course syllabi for Narxoz University against a quality rubric.
For every criterion you are given, decide one status:
- "Compliant": the syllabus clearly satisfies the criterion.
- "Partially compliant": it is addressed, but incompletely or ambiguously.
- "Not compliant": it is missing or contradicted.
- "Cannot verify": the syllabus text alone is not enough to decide.
Rules:
- "evidence" MUST be copied verbatim from the syllabus (one passage, at most 300 characters). Never paraphrase.
  When something is missing, quote the closest related passage (for example the outcome that is not assessed).
- "explanation" says why the evidence leads to the status, in one or two sentences.
- "recommendation" is one concrete edit the professor can make, or "No change suggested for this criterion."
- "confidence" is your confidence in the status between 0 and 1.
- Write explanation and recommendation in {language}. Keep evidence in the syllabus's original language.
- Judge only what is written. Do not assume content that is not in the document."""


def response_schema(codes: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["results"],
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "criterion_id",
                        "status",
                        "evidence",
                        "explanation",
                        "recommendation",
                        "confidence",
                    ],
                    "properties": {
                        "criterion_id": {"type": "string", "enum": codes},
                        "status": {"type": "string", "enum": [s.value for s in ResultStatus]},
                        "evidence": {"type": "string"},
                        "explanation": {"type": "string"},
                        "recommendation": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
            }
        },
    }


def document_text(content: ExtractedContent) -> str:
    text = content.raw_text
    return text if len(text) <= MAX_DOCUMENT_CHARS else text[:MAX_DOCUMENT_CHARS] + "\n[document truncated]"


def build_user_prompt(
    specs: list[CriterionSpec], content: ExtractedContent, report: TechnicalReport, context: dict[str, str]
) -> str:
    criteria = [
        {"criterion_id": s.code, "criterion": s.name, **({"guidance": s.guidance} if s.guidance else {})}
        for s in specs
    ]
    parts = [
        f"Principle {specs[0].principle}: {specs[0].principle_name}",
        "Criteria:\n" + json.dumps(criteria, ensure_ascii=False, indent=1),
    ]
    if report.issues:
        parts.append(
            "Automated structure findings (reliable, use them):\n"
            + "\n".join(f"- {i.message}" for i in report.issues)
        )
    for name in {s.required_context for s in specs if s.required_context and s.required_context in context}:
        parts.append(f"{name}:\n{context[name]}")
    parts.append(f"Syllabus:\n<<<\n{document_text(content)}\n>>>")
    return "\n\n".join(parts)


async def evaluate_principle(
    llm: LLMProvider,
    specs: list[CriterionSpec],
    content: ExtractedContent,
    report: TechnicalReport,
    context: dict[str, str],
    *,
    run_id: uuid.UUID | None = None,
    trace: bool = True,
) -> list[CriterionResultData]:
    codes = [s.code for s in specs]
    data = await llm.complete_json(
        purpose="criteria",
        system=SYSTEM_PROMPT.format(language=get_settings().llm_output_language),
        user=build_user_prompt(specs, content, report, context),
        schema=response_schema(codes),
        meta={"criteria": specs, "content": content, "report": report, "context": context},
        run_id=run_id,
        trace=trace,
    )
    by_code: dict[str, CriterionResultData] = {}
    for item in data.get("results", []):
        code = item.get("criterion_id")
        if code in codes and code not in by_code:
            by_code[code] = CriterionResultData(
                criterion_id=code,
                status=ResultStatus(item["status"]),
                evidence=item.get("evidence", "").strip(),
                explanation=item.get("explanation", "").strip(),
                recommendation=item.get("recommendation", "").strip(),
                confidence=item.get("confidence"),
            )
    for spec in specs:
        if spec.code not in by_code:
            by_code[spec.code] = CriterionResultData(
                criterion_id=spec.code,
                status=ResultStatus.cannot_verify,
                evidence="",
                explanation="The evaluator returned no result for this criterion.",
                recommendation="Run the check again.",
                confidence=None,
            )
    return [by_code[c] for c in codes]
