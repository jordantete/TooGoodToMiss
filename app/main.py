import asyncio
from dotenv import load_dotenv
from telegram.ext import Application, ContextTypes
from app.common.logger import LOGGER
from app.core.scheduler import Scheduler
from app.core.state import StateStore, DEFAULT_STATE_PATH
from app.core.telegram_bot_handler import TelegramBotHandler
from app.services.tgtg_service_monitor import TgtgServiceMonitor

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
        try:
            delay = scheduler.next_delay_seconds()
            context.job_queue.run_once(
                monitor_job,
                when=delay,
                name="monitoring",
                job_kwargs={"misfire_grace_time": None}
            )
            LOGGER.info(f"Next monitoring pass scheduled in {delay:.0f}s.")

        except Exception as e:
            fallback_delay = Scheduler.OFF_WINDOW_RETRY_MINUTES * 60
            LOGGER.error(f"Failed to compute/schedule next monitoring pass: {e}. Falling back to {fallback_delay}s.")
            context.job_queue.run_once(
                monitor_job,
                when=fallback_delay,
                name="monitoring",
                job_kwargs={"misfire_grace_time": None}
            )

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
    application.job_queue.run_once(
        monitor_job,
        when=0,
        name="monitoring",
        job_kwargs={"misfire_grace_time": None}
    )
    LOGGER.info("Monitoring loop armed.")

    return application

def main() -> None:
    load_dotenv()
    LOGGER.info("Starting TooGoodToMiss.")
    build_application().run_polling()

if __name__ == "__main__":
    main()
