"""Guard against invented quotes: evidence must actually appear in the document."""

import re

from rapidfuzz import fuzz

from app.ai.types import CriterionResultData
from app.enums import ResultStatus

_QUOTES = str.maketrans("", "", "\"'«»“”„‘’`")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_QUOTES)).strip().lower()


def evidence_found(evidence: str, document: str, threshold: int) -> bool:
    needle = normalize(evidence).rstrip(".…")
    if len(needle) < 8:
        return False
    haystack = normalize(document)
    return needle in haystack or fuzz.partial_ratio(needle, haystack) >= threshold


def verify(result: CriterionResultData, document: str, threshold: int) -> CriterionResultData:
    """Downgrade a decided status to "Cannot verify" when its evidence is not in the document."""
    if result.status == ResultStatus.cannot_verify or not result.evidence:
        return result
    if evidence_found(result.evidence, document, threshold):
        return result.model_copy(update={"evidence_verified": True})
    return result.model_copy(
        update={
            "status": ResultStatus.cannot_verify,
            "evidence_verified": False,
            "confidence": None,
            "explanation": "The cited evidence could not be found in the document, so the evaluator's "
            f"status ({result.status.value}) was not accepted. {result.explanation}",
        }
    )
