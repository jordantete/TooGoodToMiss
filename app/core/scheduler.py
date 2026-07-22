import pytz, random
from datetime import datetime
from typing import Optional, Tuple
from app.common.constants import WEEKDAY_MAP
from app.common.logger import LOGGER
from app.core.state import StateStore

class Scheduler:
    MORNING_WINDOW = ((10, 12), (10, 20))    # 10:00-12:00, delai 10-20 min
    AFTERNOON_WINDOW = ((12, 19), (2, 5))    # 12:00-19:00, delai 2-5 min
    OFF_WINDOW_RETRY_MINUTES = 30            # re-verification hors fenetre / dimanche
    COOLDOWN_MARGIN_SECONDS = 60             # marge pour ne pas se reveiller pile a l'expiration

    def __init__(
        self,
        state: StateStore
    ):
        self.state = state

    def _get_time_window(
        self,
        current_hour: int
    ) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        """Determine the active time window based on the current hour."""
        for window_name, ((start_hour, end_hour), delay_range) in {
            'morning': self.MORNING_WINDOW,
            'afternoon': self.AFTERNOON_WINDOW,
        }.items():
            if start_hour <= current_hour < end_hour:
                LOGGER.info(f"Current time falls within the {window_name} window.")
                return (start_hour, end_hour), delay_range
        return None

    def next_delay_seconds(self) -> float:
        """
        Seconds to wait before the next monitoring pass.

        NEVER returns None. On Lambda a fixed cron re-armed the machinery when
        no rule was scheduled; on the VPS the job is the only thing that
        reschedules itself, so returning None would stop monitoring for good.
        """
        cooldown_remaining = self.state.cooldown_remaining()
        if cooldown_remaining is not None:
            LOGGER.info(f"Cooldown active - next pass in {cooldown_remaining:.0f}s.")
            return cooldown_remaining + self.COOLDOWN_MARGIN_SECONDS

        now = datetime.now(pytz.utc)

        if WEEKDAY_MAP[now.weekday()] == 'Sunday':
            LOGGER.info("Today is Sunday - monitoring idle, will re-check later.")
            return self.OFF_WINDOW_RETRY_MINUTES * 60

        time_window = self._get_time_window(now.hour)
        if not time_window:
            LOGGER.info("Outside monitoring hours - will re-check later.")
            return self.OFF_WINDOW_RETRY_MINUTES * 60

        _, delay_range = time_window
        delay_minutes = random.randint(*delay_range)
        LOGGER.info(f"Next monitoring pass in {delay_minutes} minutes.")
        return delay_minutes * 60

    def activate_cooldown(
        self,
        cooldown_minutes: int = 30
    ) -> None:
        LOGGER.info("Triggering cooldown due to anti-bot detection.")
        self.state.set_cooldown(cooldown_minutes)

    def remove_cooldown(self) -> None:
        LOGGER.info("Removing cooldown and waking up the bot.")
        self.state.clear_cooldown()

    def is_bot_paused(self) -> bool:
        return self.state.is_paused()

    def cooldown_remaining(self) -> Optional[float]:
        return self.state.cooldown_remaining()

    def should_monitor_now(self) -> bool:
        """True when a monitoring pass should actually run right now."""
        if self.state.is_paused():
            return False

        now = datetime.now(pytz.utc)
        if WEEKDAY_MAP[now.weekday()] == 'Sunday':
            return False

        return self._get_time_window(now.hour) is not None
