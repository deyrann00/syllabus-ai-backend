from fastapi import APIRouter, status

from app.api.common import run_out, version_out
from app.deps import CurrentUser, DbSession
from app.documents import AssistantMessage, WizardDraft
from app.schemas.work import AssistantIn, AssistantOut, DraftIn, DraftOut, DraftPatch, GenerateOut
from app.services import wizard
from app.workers import dispatch

router = APIRouter(tags=["wizard"])


def _out(draft: WizardDraft) -> DraftOut:
    return DraftOut(id=str(draft.id), **draft.model_dump(exclude={"id", "owner_id", "created_at"}))


@router.get("/drafts", response_model=list[DraftOut])
async def list_drafts(principal: CurrentUser):
    return [_out(d) for d in await wizard.list_for(principal)]


@router.post("/drafts", response_model=DraftOut, status_code=status.HTTP_201_CREATED)
async def create(body: DraftIn, principal: CurrentUser, session: DbSession):
    return _out(await wizard.create(session, principal, body.model_dump()))


@router.get("/drafts/{draft_id}", response_model=DraftOut)
async def get(draft_id: str, principal: CurrentUser):
    return _out(await wizard.get(principal, draft_id))


@router.patch("/drafts/{draft_id}", response_model=DraftOut)
async def update(draft_id: str, body: DraftPatch, principal: CurrentUser, session: DbSession):
    draft = await wizard.get(principal, draft_id)
    return _out(await wizard.update(session, principal, draft, body.model_dump(exclude_none=True)))


@router.post("/drafts/{draft_id}/assistant", response_model=AssistantOut)
async def ask(draft_id: str, body: AssistantIn, principal: CurrentUser):
    draft = await wizard.get(principal, draft_id)
    return AssistantOut(
        section=body.section, answer=await wizard.ask(draft, section=body.section, question=body.question)
    )


@router.get("/drafts/{draft_id}/assistant", response_model=list[AssistantMessage])
async def assistant_history(draft_id: str, principal: CurrentUser):
    return await wizard.history(await wizard.get(principal, draft_id))


@router.post("/drafts/{draft_id}/generate", response_model=GenerateOut, status_code=status.HTTP_201_CREATED)
async def generate(draft_id: str, principal: CurrentUser, session: DbSession):
    """Render the draft as a university-format .docx, add it as a syllabus version and queue its AI check."""
    draft = await wizard.get(principal, draft_id)
    syllabus, version, run = await wizard.generate(session, principal, draft)
    await session.commit()
    await wizard.mark_generated(draft, syllabus.id)
    await dispatch.check_run(run.id)
    await session.refresh(run)
    await session.refresh(version)
    return GenerateOut(
        syllabus_id=syllabus.id, version=await version_out(session, version), run=await run_out(session, run)
    )
