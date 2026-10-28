"""AI pipeline without databases: extraction, deterministic rules, fake evaluator, evidence guard, apply."""

from pathlib import Path

from app.ai import pipeline
from app.ai.apply import Edit, apply_docx
from app.ai.evidence import verify
from app.ai.extraction import extract, parse_lines
from app.ai.fake import FakeProvider
from app.ai.types import CriterionResultData, CriterionSpec
from app.enums import CheckKind, EvaluationType, ResultStatus
from seed.samples import sample_syllabus

PROTOTYPE_ISSUES = {"1.2", "3.5", "4.3", "6.1", "6.4", "9.2"}


def _write(tmp_path: Path, data: bytes, name: str = "s.docx") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _specs() -> list[CriterionSpec]:
    import yaml

    raw = yaml.safe_load(Path("rubric/2026.1.yaml").read_text())
    types = {
        "ai": EvaluationType.ai_semantic,
        "deterministic": EvaluationType.deterministic,
        "external": EvaluationType.external_context,
    }
    return [
        CriterionSpec(
            code=f"{p['number']}.{i}",
            name=c["name"],
            principle=p["number"],
            principle_name=p["name"],
            evaluation_type=types[c.get("evaluation_type", "ai")],
            rule=c.get("rule"),
            required_context=c.get("required_context"),
        )
        for p in raw["principles"]
        for i, c in enumerate(p["criteria"], start=1)
    ]


def test_extraction_reads_structure(tmp_path):
    content = extract(_write(tmp_path, sample_syllabus("Artificial Intelligence", with_issues=True)))
    assert content.language == "English"
    assert {"outcomes", "content", "assessment", "resources", "policy"} <= set(content.sections)
    assert [o.code for o in content.outcomes] == ["LO1", "LO2", "LO3", "LO4", "LO5"]
    assert len(content.weeks) == 12
    assert [a.weight for a in content.assessments if a.weight] == [30, 30, 40]


def test_breakdown_percentages_are_not_task_weights(tmp_path):
    content = extract(_write(tmp_path, sample_syllabus("AI", with_issues=False)))
    assert sum(a.weight or 0 for a in content.assessments) == 100


def test_language_detection():
    assert parse_lines([("Оқыту нәтижелері", True), ("ОН1. Талдау жасау", False)]).language == "Kazakh"
    assert (
        parse_lines([("Результаты обучения", True), ("РО1. Сравнивать методы", False)]).language == "Russian"
    )


async def test_sample_with_issues_reproduces_prototype_review(tmp_path):
    content = extract(_write(tmp_path, sample_syllabus("Artificial Intelligence", with_issues=True)))
    output = await pipeline.evaluate(content, _specs(), FakeProvider(), trace=False)
    assert len(output.results) == 49
    by_code = {r.criterion_id: r for r in output.results}
    issues = {c for c, r in by_code.items() if r.status in (ResultStatus.partial, ResultStatus.not_compliant)}
    assert issues == PROTOTYPE_ISSUES
    assert by_code["6.1"].status == ResultStatus.not_compliant
    for code in ("3.4", "7.2"):
        assert by_code[code].status == ResultStatus.cannot_verify
        assert by_code[code].required_context
    assert all(r.evidence_verified for r in output.results if r.status != ResultStatus.cannot_verify)


async def test_clean_sample_has_no_issues(tmp_path):
    content = extract(_write(tmp_path, sample_syllabus("AI", with_issues=False)))
    output = await pipeline.evaluate(content, _specs(), FakeProvider(), trace=False)
    assert pipeline.count_issues(output.results) == 0
    assert output.technical.issues == []


async def test_passport_enables_program_alignment(tmp_path):
    content = extract(_write(tmp_path, sample_syllabus("AI", with_issues=False)))
    output = await pipeline.evaluate(
        content, _specs(), FakeProvider(), trace=False, context={"Educational Program Passport": "PO1 ..."}
    )
    assert next(r for r in output.results if r.criterion_id == "3.4").status == ResultStatus.compliant


async def test_unchecked_criteria_are_skipped(tmp_path):
    content = extract(_write(tmp_path, sample_syllabus("AI", with_issues=True)))
    output = await pipeline.evaluate(
        content, _specs(), FakeProvider(), trace=False, checks={CheckKind.technical}
    )
    assert {r.criterion_id for r in output.results} == {"3.2", "3.4", "6.8", "7.2", "8.2"}


def test_invented_evidence_is_downgraded():
    result = CriterionResultData(
        criterion_id="3.5",
        status=ResultStatus.compliant,
        evidence="Students will design a compiler.",
        explanation="ok",
        recommendation="",
        confidence=0.9,
    )
    checked = verify(result, "LO1. Explain methods.\nLO2. Compare methods.", threshold=85)
    assert checked.status == ResultStatus.cannot_verify
    assert checked.evidence_verified is False
    assert checked.confidence is None


def test_apply_edits_paragraphs_and_table_rows(tmp_path):
    path = _write(tmp_path, sample_syllabus("AI", with_issues=True))
    result = apply_docx(
        path,
        [
            Edit(
                "LO2. Students will understand the fundamental principles of machine learning.",
                "LO2. Compare machine-learning methods and justify their selection.",
            ),
            Edit("Final project | 40% | LO3, LO4", "Final project | 40% | LO3, LO4, LO5"),
            Edit("Text that is not in the document", "New paragraph"),
        ],
    )
    assert (result.applied, result.appended) == (2, 1)
    content = extract(_write(tmp_path, result.data, "v2.docx"))
    assert "LO2. Compare machine-learning methods and justify their selection." in content.paragraphs
    assert content.assessments[-1].outcomes == ["LO3", "LO4", "LO5"]
    assert content.paragraphs[-1] == "New paragraph"
