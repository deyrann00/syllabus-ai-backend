from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery("syllabus", broker=settings.redis_url, include=["app.workers.tasks"])
celery_app.conf.update(
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    # LLM-bound work gets its own queue so its worker concurrency can match vLLM capacity.
    task_routes={"syllabus.check_run": {"queue": "ai"}, "syllabus.precheck": {"queue": "ai"}},
    beat_schedule={
        "start-due-sessions": {"task": "syllabus.start_due_sessions", "schedule": 60.0},
        "send-pending-emails": {"task": "syllabus.send_pending_emails", "schedule": 60.0},
        "sync-drive-files": {"task": "syllabus.sync_drive_files", "schedule": 60.0},
    },
    timezone="UTC",
)
