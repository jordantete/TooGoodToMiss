import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from freezegun import freeze_time

from app.core.scheduler import Scheduler
from app.core.state import StateStore


class TestScheduler(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.state = StateStore(Path(self._tmp.name) / "state.json")
        self.scheduler = Scheduler(self.state)

    def tearDown(self):
        self._tmp.cleanup()

    @freeze_time("2026-07-22 10:30:00")  # Wednesday, morning window
    def test_morning_delay_between_10_and_20_minutes(self):
        for _ in range(50):
            delay = self.scheduler.next_delay_seconds()
            self.assertGreaterEqual(delay, 10 * 60)
            self.assertLessEqual(delay, 20 * 60)

    @freeze_time("2026-07-22 14:00:00")  # Wednesday, afternoon window
    def test_afternoon_delay_between_2_and_5_minutes(self):
        for _ in range(50):
            delay = self.scheduler.next_delay_seconds()
            self.assertGreaterEqual(delay, 2 * 60)
            self.assertLessEqual(delay, 5 * 60)

    @freeze_time("2026-07-26 14:00:00")  # Sunday
    def test_sunday_returns_retry_delay_not_none(self):
        delay = self.scheduler.next_delay_seconds()
        self.assertEqual(delay, Scheduler.OFF_WINDOW_RETRY_MINUTES * 60)

    @freeze_time("2026-07-22 03:00:00")  # outside any window
    def test_outside_window_returns_retry_delay(self):
        delay = self.scheduler.next_delay_seconds()
        self.assertEqual(delay, Scheduler.OFF_WINDOW_RETRY_MINUTES * 60)

    def test_next_delay_never_returns_none(self):
        """Guardrail: returning None would stop the monitoring loop for good."""
        for hour in range(24):
            for day in ("2026-07-22", "2026-07-26"):  # Wednesday and Sunday
                with freeze_time(f"{day} {hour:02d}:00:00"):
                    delay = self.scheduler.next_delay_seconds()
                    self.assertIsNotNone(delay)
                    self.assertGreater(delay, 0)

    @freeze_time("2026-07-22 10:30:00")
    def test_cooldown_delays_until_cooldown_expiry(self):
        self.scheduler.activate_cooldown(30)
        self.assertTrue(self.scheduler.is_bot_paused())
        delay = self.scheduler.next_delay_seconds()
        self.assertGreaterEqual(delay, 30 * 60)

    @freeze_time("2026-07-22 10:30:00")
    def test_remove_cooldown_resumes_normal_window(self):
        self.scheduler.activate_cooldown(30)
        self.scheduler.remove_cooldown()
        self.assertFalse(self.scheduler.is_bot_paused())
        self.assertLessEqual(self.scheduler.next_delay_seconds(), 20 * 60)

    @freeze_time("2026-07-22 10:30:00")
    def test_cooldown_survives_a_new_scheduler_instance(self):
        self.scheduler.activate_cooldown(30)
        self.assertTrue(Scheduler(self.state).is_bot_paused())

    @freeze_time("2026-07-22 03:00:00")  # outside any window
    def test_should_monitor_now_false_outside_window(self):
        self.assertFalse(self.scheduler.should_monitor_now())

    @freeze_time("2026-07-22 10:30:00")  # Wednesday, morning window
    def test_should_monitor_now_true_in_window(self):
        self.assertTrue(self.scheduler.should_monitor_now())


if __name__ == "__main__":
    unittest.main()
