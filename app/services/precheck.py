"""Professor's private pre-check: nothing is written to PostgreSQL or MongoDB.

The upload lives in TMP_ROOT only until the job finishes; job state and result live in Redis for PRECHECK_TTL_S.
"""

import asyncio
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import pipeline
from app.ai.extraction import ExtractionError, extract
from app.ai.llm import get_llm
from app.auth.rbac import Principal, ensure_course_lead
from app.config import get_settings
from app.db.pg import session_scope
from app.models import Course, Program
from app.services.rubric import load_rubric
from app.services.syllabi import validate_upload
from app.storage import cache, files
from app.workers import dispatch


def _key(job_id: str) -> str:
    return f"precheck:{job_id}"


async def _update(job_id: str, **changes: Any) -> dict:
    state = await cache.get_json(_key(job_id)) or {}
    state.update(changes)
    await cache.put_json(_key(job_id), state, get_settings().precheck_ttl_s)
    return state


async def start(
    session: AsyncSession, principal: Principal, course_id: uuid.UUID, filename: str, data: bytes
) -> str:
    course = await ensure_course_lead(session, principal, course_id)
    validate_upload(filename, data)
    job_id = uuid.uuid4().hex
    path = files.tmp_dir() / f"precheck-{job_id}{Path(filename).suffix.lower()}"
    path.write_bytes(data)
    await _update(
        job_id,
        owner_id=str(principal.id),
        course_id=str(course_id),
        course_name=course.name,
        filename=files.safe_filename(filename),
        path=str(path),
        status="Queued",
        phase=None,
        result=None,
        error=None,
    )
    await dispatch.precheck(job_id)
    return job_id


async def run_job(job_id: str) -> None:
    state = await cache.get_json(_key(job_id))
    if not state or state.get("status") != "Queued":
        return
    path = Path(state["path"])
    try:
        await _update(job_id, status="Processing", phase="Reading document")
        async with session_scope() as session:
            rubric = await load_rubric(session)
            passport = await session.scalar(
                sa.select(Program.passport_text)
                .join(Course, Course.program_id == Program.id)
                .where(Course.id == uuid.UUID(state["course_id"]))
            )
        await _update(job_id, phase="Extracting syllabus structure")
        content = await asyncio.to_thread(extract, path)

        async def on_phase(name: str) -> None:
            await _update(job_id, phase=name)

        output = await pipeline.evaluate(
            content,
            rubric.specs,
            get_llm(),
            context={"Educational Program Passport": passport} if passport else {},
            trace=False,
            on_phase=on_phase,
        )
        await _update(
            job_id,
            status="Completed",
            phase=None,
            result={
                "rubric_version": rubric.version.code,
                "issues": pipeline.count_issues(output.results),
                **output.model_dump(mode="json"),
            },
        )
    except ExtractionError as exc:
        await _update(job_id, status="Failed", phase=None, error=f"Cannot open: {exc}")
    except Exception as exc:
        await _update(job_id, status="Failed", phase=None, error=f"{type(exc).__name__}: {exc}")
    finally:
        await asyncio.to_thread(path.unlink, missing_ok=True)


async def get(principal: Principal, job_id: str) -> dict:
    state = await cache.get_json(_key(job_id))
    if not state or state.get("owner_id") != str(principal.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pre-check not found or expired")
    state.pop("path", None)
    state.pop("owner_id", None)
    return {"job_id": job_id, **state}


def report_text(state: dict) -> str:
    result = state.get("result") or {}
    lines = [
        "Narxoz Syllabus AI preliminary review",
        f"Course: {state.get('course_name')}",
        f"File: {state.get('filename')}",
        f"Rubric: {result.get('rubric_version')}  Model: {result.get('model_version')}",
        "This private check is not stored and is not part of the academic approval workflow.",
        "",
        f"Issues: {result.get('issues')}",
        "",
    ]
    for r in result.get("results", []):
        if r["status"] == "Compliant":
            continue
        lines += [
            f"[{r['criterion_id']}] {r['status']}",
            f"  Evidence: {r['evidence']}",
            f"  Why: {r['explanation']}",
            f"  Suggestion: {r['recommendation']}",
            "",
        ]
    return "\n".join(lines)
