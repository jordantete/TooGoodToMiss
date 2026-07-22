import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from freezegun import freeze_time

from app.core.scheduler import Scheduler
from app.core.state import StateStore
from app.main import build_application, monitor_job


class TestMonitorJob(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.state = StateStore(Path(self._tmp.name) / "state.json")
        self.scheduler = Scheduler(self.state)

        self.context = MagicMock()
        self.context.job_queue.run_once = MagicMock()
        self.context.bot_data = {"state": self.state, "scheduler": self.scheduler}

    def tearDown(self):
        self._tmp.cleanup()

    @freeze_time("2026-07-22 10:30:00")
    async def test_runs_monitoring_and_reschedules_itself(self):
        with patch("app.main.TgtgServiceMonitor") as monitor_cls:
            await monitor_job(self.context)
            monitor_cls.return_value.start_monitoring.assert_called_once()
        self.context.job_queue.run_once.assert_called_once()

    @freeze_time("2026-07-22 10:30:00")
    async def test_skips_monitoring_while_paused_but_still_reschedules(self):
        self.scheduler.activate_cooldown(30)
        with patch("app.main.TgtgServiceMonitor") as monitor_cls:
            await monitor_job(self.context)
            monitor_cls.return_value.start_monitoring.assert_not_called()
        self.context.job_queue.run_once.assert_called_once()

    @freeze_time("2026-07-26 14:00:00")  # dimanche
    async def test_reschedules_on_sunday_without_monitoring(self):
        with patch("app.main.TgtgServiceMonitor") as monitor_cls:
            await monitor_job(self.context)
            monitor_cls.return_value.start_monitoring.assert_not_called()
        self.context.job_queue.run_once.assert_called_once()

    @freeze_time("2026-07-22 10:30:00")
    async def test_reschedules_even_when_monitoring_raises(self):
        """Garde-fou: une exception ne doit jamais tuer la boucle."""
        with patch("app.main.TgtgServiceMonitor") as monitor_cls:
            monitor_cls.return_value.start_monitoring.side_effect = RuntimeError("boom")
            await monitor_job(self.context)
        self.context.job_queue.run_once.assert_called_once()

    @freeze_time("2026-07-22 10:30:00")
    async def test_reschedule_from_monitor_job_uses_misfire_grace_time_none(self):
        """Finding 1 regression: without this, a late-firing job is silently dropped by APScheduler."""
        with patch("app.main.TgtgServiceMonitor"):
            await monitor_job(self.context)
        _, kwargs = self.context.job_queue.run_once.call_args
        self.assertEqual(kwargs.get("job_kwargs"), {"misfire_grace_time": None})

    def test_build_application_arms_monitoring_with_misfire_grace_time_none(self):
        """Finding 1 regression: the initial run_once (when=0) must survive a slow startup
        (getMe/post_init/set_my_commands/start_polling), or monitoring never begins."""
        with patch("app.main.TelegramBotHandler") as handler_cls, \
             patch("app.main.StateStore"), \
             patch("app.main.Scheduler"):
            application = MagicMock()
            handler_cls.return_value.application = application

            build_application()

        _, kwargs = application.job_queue.run_once.call_args
        self.assertEqual(kwargs.get("job_kwargs"), {"misfire_grace_time": None})

    @freeze_time("2026-07-22 10:30:00")
    async def test_fallback_reschedule_when_next_delay_seconds_raises(self):
        """Finding 2 regression: state.json can be hand-edited on the VPS into a shape that
        makes next_delay_seconds() raise. That must not skip the reschedule."""
        self.scheduler.next_delay_seconds = MagicMock(side_effect=ValueError("bad cooldown_end_time"))
        with patch("app.main.TgtgServiceMonitor"):
            await monitor_job(self.context)

        self.context.job_queue.run_once.assert_called_once()
        _, kwargs = self.context.job_queue.run_once.call_args
        self.assertEqual(kwargs.get("when"), Scheduler.OFF_WINDOW_RETRY_MINUTES * 60)
        self.assertEqual(kwargs.get("job_kwargs"), {"misfire_grace_time": None})


if __name__ == "__main__":
    unittest.main()
