"""Enqueue background work. With TASKS_EAGER the work is awaited in-process instead (tests, quick local runs).

Callers must commit their transaction first: the job reads state in its own session.
"""

import asyncio
import uuid

from app.config import get_settings


async def _send(task: str, *args: str) -> None:
    from app.workers.celery_app import celery_app

    await asyncio.to_thread(celery_app.send_task, task, args=list(args))


async def check_run(run_id: uuid.UUID) -> None:
    if get_settings().tasks_eager:
        from app.services.checks import execute_run

        await execute_run(run_id)
        return
    await _send("syllabus.check_run", str(run_id))


async def check_runs(run_ids: list[uuid.UUID]) -> None:
    for run_id in run_ids:
        await check_run(run_id)


async def precheck(job_id: str) -> None:
    if get_settings().tasks_eager:
        from app.services.precheck import run_job

        await run_job(job_id)
        return
    await _send("syllabus.precheck", job_id)
