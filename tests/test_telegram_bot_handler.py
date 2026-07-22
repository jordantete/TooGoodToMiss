import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.scheduler import Scheduler
from app.core.state import StateStore
from app.core.telegram_bot_handler import TelegramBotHandler


class TestWakeUpBotHandler(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.state = StateStore(Path(self._tmp.name) / "state.json")
        self.scheduler = Scheduler(self.state)

        with patch.dict(
            "os.environ",
            {"TELEGRAM_BOT_TOKEN": "test_bot_token", "TELEGRAM_CHAT_ID": "test_chat_id"}
        ):
            self.handler = TelegramBotHandler(self.scheduler, self.state)

    def tearDown(self):
        self._tmp.cleanup()

    async def test_wake_up_cancels_pending_job_and_arms_exactly_one(self):
        """Finding 1 regression: /wakeup must cancel any pending "monitoring" job
        before arming a new one, so a second in-flight pass reaching its own
        finally block can never end up with two concurrent monitoring chains."""
        pending_job = MagicMock()
        context = MagicMock()
        context.job_queue.get_jobs_by_name.return_value = [pending_job]
        context.bot.send_message = AsyncMock()

        update = MagicMock()
        update.effective_chat.id = 42

        await self.handler._wake_up_bot_handler(update, context)

        pending_job.schedule_removal.assert_called_once()
        context.job_queue.get_jobs_by_name.assert_called_once_with("monitoring")
        context.job_queue.run_once.assert_called_once()
        _, kwargs = context.job_queue.run_once.call_args
        self.assertEqual(kwargs.get("name"), "monitoring")
        self.assertEqual(kwargs.get("when"), 0)
        self.assertEqual(kwargs.get("job_kwargs"), {"misfire_grace_time": None})


if __name__ == "__main__":
    unittest.main()
