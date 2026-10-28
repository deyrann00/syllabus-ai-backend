import uuid

from app.workers import runtime
from app.workers.celery_app import celery_app


@celery_app.task(name="syllabus.check_run")
def check_run(run_id: str) -> None:
    from app.services.checks import execute_run

    runtime.run(execute_run, uuid.UUID(run_id))


@celery_app.task(name="syllabus.precheck")
def precheck(job_id: str) -> None:
    from app.services.precheck import run_job

    runtime.run(run_job, job_id)


@celery_app.task(name="syllabus.start_due_sessions")
def start_due_sessions() -> None:
    from app.services.sessions import start_due_sessions as start_due

    runtime.run(start_due)


@celery_app.task(name="syllabus.send_pending_emails")
def send_pending_emails() -> None:
    from app.services.notifications import send_pending_emails as send

    runtime.run(send)
