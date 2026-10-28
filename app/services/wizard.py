"""Create Syllabus wizard: drafts in MongoDB until generated into a .docx version of a syllabus."""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import assistant
from app.ai.llm import get_llm
from app.ai.render_docx import render_wizard
from app.auth.rbac import Principal, ensure_course_lead
from app.documents import AssistantMessage, AssistantThread, WizardDraft
from app.enums import Trigger, VersionSource
from app.models import AssessmentRun, Syllabus, SyllabusVersion
from app.services import checks, syllabi
from app.storage.files import safe_filename

ALLOWED_FIELDS = {f for fields in assistant.WIZARD_SECTIONS.values() for f in fields}


async def create(session: AsyncSession, principal: Principal, data: dict) -> WizardDraft:
    if data.get("course_id"):
        await ensure_course_lead(session, principal, data["course_id"])
    draft = WizardDraft(owner_id=principal.id, **data)
    await draft.insert()
    return draft


async def get(principal: Principal, draft_id: str) -> WizardDraft:
    try:
        draft = await WizardDraft.get(draft_id)
    except Exception:
        draft = None
    if draft is None or draft.owner_id != principal.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft not found")
    return draft


async def list_for(principal: Principal) -> list[WizardDraft]:
    return await WizardDraft.find(WizardDraft.owner_id == principal.id).sort("-updated_at").to_list()


async def update(
    session: AsyncSession, principal: Principal, draft: WizardDraft, changes: dict
) -> WizardDraft:
    fields = changes.pop("fields", None) or {}
    unknown = set(fields) - ALLOWED_FIELDS
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown fields: {', '.join(sorted(unknown))}"
        )
    if changes.get("course_id"):
        await ensure_course_lead(session, principal, changes["course_id"])
    for key, value in changes.items():
        setattr(draft, key, value)
    draft.fields.update(fields)
    draft.updated_at = datetime.now(UTC)
    await draft.save()
    return draft


async def ask(draft: WizardDraft, *, section: str, question: str) -> str:
    if section not in assistant.WIZARD_SECTIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown section '{section}'")
    reply = await assistant.answer(
        get_llm(), section=section, question=question, fields=draft.fields, language=draft.language
    )
    thread = await AssistantThread.find_one(AssistantThread.draft_id == str(draft.id))
    thread = thread or AssistantThread(draft_id=str(draft.id))
    thread.messages += [
        AssistantMessage(role="user", section=section, content=question),
        AssistantMessage(role="assistant", section=section, content=reply),
    ]
    await thread.save()
    return reply


async def history(draft: WizardDraft) -> list[AssistantMessage]:
    thread = await AssistantThread.find_one(AssistantThread.draft_id == str(draft.id))
    return thread.messages if thread else []


async def generate(
    session: AsyncSession, principal: Principal, draft: WizardDraft
) -> tuple[Syllabus, SyllabusVersion, AssessmentRun]:
    """Render the draft in university format, store it as a new version and queue its check.

    The caller commits, then sets draft.syllabus_id.
    """
    if draft.course_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose the course before generating")
    course = await ensure_course_lead(session, principal, draft.course_id)
    syllabus = await session.scalar(
        sa.select(Syllabus)
        .where(Syllabus.course_id == course.id, Syllabus.semester == draft.semester)
        .with_for_update()
    )
    if syllabus is None:
        syllabus = await syllabi.create_syllabus(
            session,
            course=course,
            professor_id=principal.id,
            language=draft.language,
            semester=draft.semester,
        )
    data = render_wizard({**draft.fields, "title": draft.title or course.name}, course_name=course.name)
    version = await syllabi.add_version(
        session,
        syllabus,
        filename=f"{safe_filename(course.name).replace(' ', '_').lower()}_v{syllabus.current_version + 1}.docx",
        data=data,
        user_id=principal.id,
        source=VersionSource.wizard,
    )
    run = await checks.create_run(session, version, trigger=Trigger.professor_manual, user_id=principal.id)
    return syllabus, version, run


async def mark_generated(draft: WizardDraft, syllabus_id: uuid.UUID) -> None:
    draft.syllabus_id = syllabus_id
    draft.updated_at = datetime.now(UTC)
    await draft.save()
