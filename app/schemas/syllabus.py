import uuid
from datetime import datetime

from pydantic import Field

from app.ai.types import CriterionResultData, TechnicalReport
from app.enums import AiStatus, ApprovalStage, CheckKind, RunStatus, Trigger, VersionSource
from app.schemas.common import Schema


class SyllabusOut(Schema):
    id: uuid.UUID
    course_id: uuid.UUID
    course_name: str
    course_slug: str
    course_lead_id: uuid.UUID
    program_id: uuid.UUID
    program_name: str
    school_id: uuid.UUID
    school_name: str
    professor_id: uuid.UUID
    professor_name: str
    language: str
    semester: str
    current_version: int
    ai_status: AiStatus
    approval_stage: ApprovalStage
    issues: int | None
    folder_path: str | None
    updated_at: datetime


class TransitionOption(Schema):
    to_stage: ApprovalStage
    label: str
    comment_required: bool


class SyllabusDetail(SyllabusOut):
    is_course_lead: bool
    available_transitions: list[TransitionOption]
    latest_run_id: uuid.UUID | None


class SyllabusCreate(Schema):
    course_id: uuid.UUID
    language: str = "English"
    semester: str
    folder_id: uuid.UUID | None = None


class VersionOut(Schema):
    id: uuid.UUID
    syllabus_id: uuid.UUID
    version: int
    filename: str
    content_type: str
    size: int
    created_at: datetime
    created_by: uuid.UUID
    created_by_name: str | None = None
    issue_count: int | None
    source: VersionSource
    drive_synced_at: datetime | None


class RunOut(Schema):
    id: uuid.UUID
    version_id: uuid.UUID
    rubric_version: str | None = None
    model_version: str | None
    checker_version: str | None
    trigger: Trigger
    session_id: uuid.UUID | None
    checks: list[CheckKind]
    status: RunStatus
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None


class CriterionResultOut(CriterionResultData):
    name: str
    principle: int


class RunResultsOut(RunOut):
    issues: int | None = None
    technical: TechnicalReport | None = None
    results: list[CriterionResultOut] = Field(default_factory=list)


class UploadOut(Schema):
    version: VersionOut
    run: RunOut


class TransitionIn(Schema):
    to_stage: ApprovalStage
    comment: str | None = None


class CommentIn(Schema):
    text: str = Field(min_length=1, max_length=5000)


class ActivityOut(Schema):
    id: uuid.UUID
    kind: str
    from_stage: ApprovalStage | None
    to_stage: ApprovalStage | None
    version: int | None
    actor_id: uuid.UUID | None
    actor_name: str
    comment: str | None
    created_at: datetime


class StatusChange(Schema):
    criterion_id: str
    before: str | None
    after: str | None


class CompareOut(Schema):
    a: VersionOut
    b: VersionOut
    issues_before: int | None
    issues_after: int | None
    criterion_changes: list[StatusChange]
    text_diff: list[str]
