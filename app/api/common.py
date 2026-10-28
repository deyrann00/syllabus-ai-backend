import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AssessmentRun, RubricVersion, SyllabusVersion, User
from app.schemas.syllabus import RunOut, VersionOut


async def run_out(session: AsyncSession, run: AssessmentRun) -> RunOut:
    code = await session.scalar(
        sa.select(RubricVersion.code).where(RubricVersion.id == run.rubric_version_id)
    )
    return RunOut.model_validate(run).model_copy(update={"rubric_version": code})


async def version_out(session: AsyncSession, version: SyllabusVersion) -> VersionOut:
    name = await session.scalar(sa.select(User.name).where(User.id == version.created_by))
    return VersionOut.model_validate(version).model_copy(update={"created_by_name": name})
