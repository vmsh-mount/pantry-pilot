from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init, task_prerun
from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "pantrypilot",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.planning",
        "app.tasks.pantry",
        "app.tasks.whatsapp",
        "app.tasks.maintenance",
        "app.tasks.routines",
        "app.tasks.flow",
    ]
)

def _dispose_engine_pool():
    """
    Synchronously dispose the SQLAlchemy async engine pool.
    Must be called before running any async DB work in a new event loop,
    otherwise asyncpg connections from a previous loop cause
    'Future attached to a different loop' RuntimeErrors.
    """
    from app.database import engine
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(engine.dispose())
    finally:
        loop.close()


@worker_process_init.connect
def reset_db_pool_on_fork(**kwargs):
    """Dispose pool once right after Celery forks this worker process."""
    _dispose_engine_pool()


@task_prerun.connect
def reset_db_pool_before_task(task_id, task, **kwargs):
    """
    Dispose pool before EVERY task execution.
    Required because each task creates+destroys its own event loop,
    leaving stale asyncpg connections in the pool for the next task.
    """
    _dispose_engine_pool()


celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_routes={
        "app.tasks.planning.*":    {"queue": "planning"},
        "app.tasks.pantry.*":      {"queue": "pantry"},
        "app.tasks.whatsapp.*":    {"queue": "whatsapp"},
        "app.tasks.maintenance.*": {"queue": "maintenance"},
        "app.tasks.routines.*":    {"queue": "planning"},
        "app.tasks.flow.*":        {"queue": "planning"},
    },
    beat_schedule={
        "daily-token-expiry-check": {
            "task": "app.tasks.maintenance.check_token_expiry",
            "schedule": crontab(hour=9, minute=0),  # 9 AM IST daily
        },
        "hourly-missed-run-catchup": {
            "task": "app.tasks.maintenance.catchup_missed_runs",
            "schedule": crontab(minute=5),           # :05 every hour
        },
        "routine-due-check": {
            "task": "app.tasks.routines.check_due_routines",
            "schedule": crontab(minute="*/15"),       # every 15 minutes
        },
        "evaluate-flow-signals": {
            "task": "app.tasks.flow.evaluate_flow_signals",
            "schedule": crontab(hour="*/4"),          # every 4 hours
        },
    }
)
