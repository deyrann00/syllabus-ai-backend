import uuid
from datetime import datetime

from app.enums import EvaluationType, Role
from app.schemas.common import Schema


class SchoolOut(Schema):
    id: uuid.UUID
    slug: str
    name: str


class ProgramOut(Schema):
    id: uuid.UUID
    slug: str
    name: str
    school_id: uuid.UUID
    degree_level: str
    has_passport: bool


class CourseOut(Schema):
    id: uuid.UUID
    slug: str
    name: str
    program_id: uuid.UUID
    credits: int
    lead_id: uuid.UUID
    instructor_ids: list[uuid.UUID]


class ProfessorOut(Schema):
    id: uuid.UUID
    name: str
    title: str | None


class RoleOut(Schema):
    role: Role
    school_id: uuid.UUID | None
    program_id: uuid.UUID | None


class MeOut(Schema):
    id: uuid.UUID
    email: str
    name: str
    title: str | None
    roles: list[RoleOut]
    led_course_ids: list[uuid.UUID]
    taught_course_ids: list[uuid.UUID]


class CriterionOut(Schema):
    id: uuid.UUID
    code: str
    name: str
    evaluation_type: EvaluationType
    required_context: str | None
    guidance: str | None


class PrincipleOut(Schema):
    number: int
    name: str
    criteria: list[CriterionOut]


class RubricOut(Schema):
    id: uuid.UUID
    code: str
    title: str
    imported_at: datetime
    principles: list[PrincipleOut]


class FolderIn(Schema):
    path: str
    semester: str


class FolderPatch(Schema):
    path: str | None = None
    semester: str | None = None


class FolderOut(Schema):
    id: uuid.UUID
    school_id: uuid.UUID
    path: str
    semester: str
    files: int
    checked: int
