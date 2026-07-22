import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun import freeze_time

from app.core.scheduler import Scheduler
from app.core.state import StateStore
from app.main import monitor_job


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


if __name__ == "__main__":
    unittest.main()
