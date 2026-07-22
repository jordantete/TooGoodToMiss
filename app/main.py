import asyncio
from dotenv import load_dotenv
from telegram.error import InvalidToken
from telegram.ext import Application, ContextTypes
from app.common.logger import LOGGER
from app.core.scheduler import Scheduler
from app.core.state import StateStore, DEFAULT_STATE_PATH
from app.core.telegram_bot_handler import TelegramBotHandler
from app.services.tgtg_service_monitor import TgtgServiceMonitor

def _arm_monitoring(
    job_queue,
    delay: float
) -> None:
    """
    Cancel every pending "monitoring" job, then arm exactly one.

    APScheduler removes a `date` job from the jobstore at submission time, not
    at completion, so a caller racing with a monitoring pass already in flight
    (e.g. /wakeup firing mid-pass) could otherwise find no pending job to
    cancel and end up arming a second chain alongside the one the in-flight
    pass arms in its own finally block. This is the ONLY place allowed to call
    run_once(name="monitoring", ...) - every caller must go through it so the
    "at most one monitoring job" invariant holds regardless of the path taken.
    """
    for job in job_queue.get_jobs_by_name("monitoring"):
        job.schedule_removal()
    job_queue.run_once(
        monitor_job,
        when=delay,
        name="monitoring",
        job_kwargs={"misfire_grace_time": None}
    )

async def monitor_job(
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    One monitoring pass, then reschedule itself.

    The reschedule is in a finally block on purpose: this job is the ONLY
    thing that re-arms monitoring. If it ever returns without scheduling a
    successor, monitoring stops until the process restarts.
    """
    try:
        state: StateStore = context.bot_data["state"]
        scheduler: Scheduler = context.bot_data["scheduler"]

        if not scheduler.should_monitor_now():
            LOGGER.info("Monitoring skipped - bot paused, outside window, or Sunday.")
        else:
            monitor = TgtgServiceMonitor(state)
            await asyncio.to_thread(monitor.start_monitoring, scheduler)

    except Exception as e:
        LOGGER.error(f"Monitoring pass failed: {e}")

    finally:
        armed = False
        try:
            delay = scheduler.next_delay_seconds()
            _arm_monitoring(context.job_queue, delay)
            armed = True
            LOGGER.info(f"Next monitoring pass scheduled in {delay:.0f}s.")

        except Exception as e:
            if not armed:
                fallback_delay = Scheduler.OFF_WINDOW_RETRY_MINUTES * 60
                LOGGER.error(f"Failed to compute/schedule next monitoring pass: {e}. Falling back to {fallback_delay}s.")
                _arm_monitoring(context.job_queue, fallback_delay)

def build_application() -> Application:
    """Wire the state store, scheduler, Telegram handlers and monitoring job."""
    state = StateStore(DEFAULT_STATE_PATH)
    scheduler = Scheduler(state)
    bot_handler = TelegramBotHandler(scheduler, state)

    application = bot_handler.application

    if application.job_queue is None:
        raise RuntimeError(
            "application.job_queue is None - install python-telegram-bot[job-queue] "
            "(the APScheduler extra), otherwise monitoring never runs."
        )

    application.bot_data["state"] = state
    application.bot_data["scheduler"] = scheduler
    _arm_monitoring(application.job_queue, 0)
    LOGGER.info("Monitoring loop armed.")

    return application

def main() -> None:
    load_dotenv()
    LOGGER.info("Starting TooGoodToMiss.")
    try:
        build_application().run_polling()
    except InvalidToken:
        LOGGER.error("Telegram token rejected by the server - check TELEGRAM_BOT_TOKEN.")
        raise SystemExit(1)

if __name__ == "__main__":
    main()
