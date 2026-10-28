import asyncio
import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import pipeline
from app.ai.extraction import ExtractionError, extract
from app.ai.llm import get_llm
from app.ai.types import EvaluationOutput, ExtractedContent
from app.db.pg import session_scope
from app.documents import AssessmentResultDoc, ExtractedDocument
from app.enums import AiStatus, ApprovalStage, CheckKind, RunStatus, SessionStatus, Trigger
from app.models import (
    ApprovalEvent,
    AssessmentRun,
    CheckSession,
    Course,
    CriterionOutcome,
    Program,
    RubricVersion,
    Syllabus,
    SyllabusVersion,
)
from app.services.notifications import notify
from app.services.rubric import load_rubric
from app.storage import files

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


async def create_run(
    session: AsyncSession,
    version: SyllabusVersion,
    *,
    trigger: Trigger,
    checks: Iterable[CheckKind] | None = None,
    session_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> AssessmentRun:
    rubric_id = await session.scalar(sa.select(RubricVersion.id).where(RubricVersion.is_active.is_(True)))
    if rubric_id is None:
        raise LookupError("no active rubric")
    run = AssessmentRun(
        version_id=version.id,
        rubric_version_id=rubric_id,
        trigger=trigger,
        checks=[c.value for c in (checks or pipeline.ALL_CHECKS)],
        status=RunStatus.queued,
        session_id=session_id,
        created_by=user_id,
    )
    session.add(run)
    syllabus = await session.get(Syllabus, version.syllabus_id)
    if syllabus and syllabus.current_version == version.version:
        syllabus.ai_status = AiStatus.processing
    await session.flush()
    return run


async def load_content(version: SyllabusVersion) -> ExtractedContent:
    """Extracted content is cached per version in Mongo; versions are immutable so it never goes stale."""
    cached = await ExtractedDocument.find_one(ExtractedDocument.version_id == version.id)
    if cached:
        return cached.content
    content = await asyncio.to_thread(extract, files.absolute(version.file_path))
    await ExtractedDocument(version_id=version.id, content=content).insert()
    return content


async def _context_for(session: AsyncSession, syllabus: Syllabus) -> dict[str, str]:
    passport = await session.scalar(
        sa.select(Program.passport_text)
        .join(Course, Course.program_id == Program.id)
        .where(Course.id == syllabus.course_id)
    )
    return {"Educational Program Passport": passport} if passport else {}


async def execute_run(run_id: uuid.UUID) -> None:
    """Background job. Idempotent: only a Queued run is executed; a run of a paused session stays Queued."""
    async with session_scope() as session:
        run = await session.scalar(
            sa.select(AssessmentRun).where(AssessmentRun.id == run_id).with_for_update()
        )
        if run is None or run.status != RunStatus.queued:
            return
        if run.session_id:
            cs = await session.get(CheckSession, run.session_id)
            if cs and cs.status == SessionStatus.paused:
                return
        run.status = RunStatus.processing
        run.started_at = _now()
        version = await session.get(SyllabusVersion, run.version_id)
        assert version is not None
        syllabus = await session.get(Syllabus, version.syllabus_id)
        assert syllabus is not None
        rubric = await load_rubric(session, run.rubric_version_id)
        context = await _context_for(session, syllabus)
        checks = frozenset(CheckKind(c) for c in run.checks)

    try:
        content = await load_content(version)
        output = await pipeline.evaluate(
            content, rubric.specs, get_llm(), checks=checks, context=context, run_id=run_id
        )
    except ExtractionError as exc:
        await _fail(run_id, f"Cannot open: {exc}", AiStatus.cannot_check)
        return
    except Exception as exc:
        log.exception("check run %s failed", run_id)
        await _fail(run_id, f"{type(exc).__name__}: {exc}", AiStatus.not_checked)
        return

    await AssessmentResultDoc.find(AssessmentResultDoc.run_id == run_id).delete()
    await AssessmentResultDoc(
        run_id=run_id, version_id=version.id, technical=output.technical, results=output.results
    ).insert()
    await _complete(run_id, output, rubric.criterion_ids)


async def _complete(run_id: uuid.UUID, output: EvaluationOutput, criterion_ids: dict[str, uuid.UUID]) -> None:
    from app.services.sessions import on_run_finished

    issues = pipeline.count_issues(output.results)
    async with session_scope() as session:
        run = await session.get(AssessmentRun, run_id)
        assert run is not None
        version = await session.get(SyllabusVersion, run.version_id)
        assert version is not None
        syllabus = await session.scalar(
            sa.select(Syllabus).where(Syllabus.id == version.syllabus_id).with_for_update()
        )
        assert syllabus is not None

        await session.execute(sa.delete(CriterionOutcome).where(CriterionOutcome.run_id == run_id))
        session.add_all(
            CriterionOutcome(run_id=run_id, criterion_id=criterion_ids[r.criterion_id], status=r.status)
            for r in output.results
        )
        run.status = RunStatus.completed
        run.completed_at = _now()
        run.model_version = output.model_version
        run.checker_version = output.checker_version
        run.error = None
        version.issue_count = issues

        if syllabus.current_version == version.version:
            syllabus.issues = issues
            syllabus.ai_status = AiStatus.needs_revision if issues else AiStatus.checked
            session.add(
                ApprovalEvent(
                    syllabus_id=syllabus.id,
                    kind="check",
                    version=version.version,
                    comment=f"AI check completed: {issues} issue(s)",
                )
            )
            if syllabus.approval_stage == ApprovalStage.draft:
                syllabus.approval_stage = ApprovalStage.ai_checked
                session.add(
                    ApprovalEvent(
                        syllabus_id=syllabus.id,
                        kind="transition",
                        version=version.version,
                        from_stage=ApprovalStage.draft,
                        to_stage=ApprovalStage.ai_checked,
                    )
                )
        if not run.session_id:
            lead_id = await session.scalar(sa.select(Course.lead_id).where(Course.id == syllabus.course_id))
            if lead_id:
                notify(
                    session,
                    [lead_id],
                    kind="check_completed",
                    title=f"AI check of v{version.version} completed with {issues} issue(s)",
                    payload={"syllabus_id": str(syllabus.id), "run_id": str(run_id)},
                    email=False,
                )
        await on_run_finished(session, run, ok=True)


async def _fail(run_id: uuid.UUID, error: str, ai_status: AiStatus) -> None:
    from app.services.sessions import on_run_finished

    async with session_scope() as session:
        run = await session.get(AssessmentRun, run_id)
        assert run is not None
        run.status = RunStatus.failed
        run.completed_at = _now()
        run.error = error[:2000]
        version = await session.get(SyllabusVersion, run.version_id)
        assert version is not None
        syllabus = await session.get(Syllabus, version.syllabus_id)
        if syllabus and syllabus.current_version == version.version:
            syllabus.ai_status = ai_status
        await on_run_finished(session, run, ok=False)


async def get_results(run_id: uuid.UUID) -> AssessmentResultDoc | None:
    return await AssessmentResultDoc.find_one(AssessmentResultDoc.run_id == run_id)


async def latest_run(
    session: AsyncSession, version_id: uuid.UUID, *, completed: bool = False
) -> AssessmentRun | None:
    query = sa.select(AssessmentRun).where(AssessmentRun.version_id == version_id)
    if completed:
        query = query.where(AssessmentRun.status == RunStatus.completed)
    return await session.scalar(query.order_by(AssessmentRun.created_at.desc()).limit(1))
