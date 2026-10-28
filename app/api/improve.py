import uuid

from fastapi import APIRouter, status

from app.api.common import run_out, version_out
from app.deps import CurrentUser, DbSession
from app.documents import ChangeProposalDoc
from app.schemas.work import ApplyOut, ProposalDecision, ProposalsOut
from app.services import proposals
from app.workers import dispatch

router = APIRouter(tags=["improve"])


def _out(doc: ChangeProposalDoc) -> ProposalsOut:
    return ProposalsOut(id=str(doc.id), **doc.model_dump(exclude={"id", "created_by"}))


@router.post("/runs/{run_id}/proposals", response_model=ProposalsOut, status_code=status.HTTP_201_CREATED)
async def generate(run_id: uuid.UUID, principal: CurrentUser, session: DbSession):
    """Generate correction proposals for the failed criteria of a run (returns the open set if one exists)."""
    return _out(await proposals.generate(session, principal, run_id))


@router.get("/proposals/{proposal_id}", response_model=ProposalsOut)
async def get(proposal_id: str, principal: CurrentUser, session: DbSession):
    return _out(await proposals.get_doc(session, principal, proposal_id))


@router.patch("/proposals/{proposal_id}/items/{item_id}", response_model=ProposalsOut)
async def decide(
    proposal_id: str, item_id: str, body: ProposalDecision, principal: CurrentUser, session: DbSession
):
    doc = await proposals.get_doc(session, principal, proposal_id)
    return _out(await proposals.decide(doc, item_id, body.decision, body.proposed))


@router.post("/proposals/{proposal_id}/apply", response_model=ApplyOut, status_code=status.HTTP_201_CREATED)
async def apply(proposal_id: str, principal: CurrentUser, session: DbSession):
    """Create the next version from accepted proposals and queue its AI check."""
    doc = await proposals.get_doc(session, principal, proposal_id)
    version, run, appended = await proposals.apply(session, principal, doc)
    await session.commit()
    await proposals.mark_applied(doc, version.id)
    await dispatch.check_run(run.id)
    await session.refresh(run)
    await session.refresh(version)
    return ApplyOut(
        version=await version_out(session, version), run=await run_out(session, run), appended=appended
    )
