import json
import os
import pytz
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from app.common.logger import LOGGER
from app.common.utils import Utils

DEFAULT_STATE_PATH = Path(os.getenv("STATE_FILE", "state.json"))


@dataclass
class TgtgCredentials:
    access_token: Optional[str]
    refresh_token: Optional[str]
    cookie: Optional[str]
    last_time_token_refreshed: Optional[str]


class StateStore:
    """Persistent runtime state: TGTG session, cooldown, language, notification dedup."""

    def __init__(
        self,
        path: Path = DEFAULT_STATE_PATH
    ):
        self.path = Path(path)
        self._state = self._load()

    def _load(self) -> dict:
        """Load state.json, seeding it from the environment on first run."""
        if not self.path.exists():
            state = self._seed_from_env()
            self._write(state)
            return state

        try:
            with self.path.open("r", encoding="utf-8") as state_file:
                loaded = json.load(state_file)

            if not isinstance(loaded, dict):
                raise ValueError(f"State file root must be a JSON object, got {type(loaded).__name__}")

            return loaded

        except (json.JSONDecodeError, OSError, ValueError) as e:
            LOGGER.error(f"Unreadable state file at {self.path}: {e}. Re-seeding from environment.")
            state = self._seed_from_env()
            self._write(state)
            return state

    @staticmethod
    def _seed_from_env() -> dict:
        """Build the initial state from environment variables. Runs ONCE, at creation."""
        return {
            "tgtg": {
                "access_token": Utils.get_environment_variable("ACCESS_TOKEN"),
                "refresh_token": Utils.get_environment_variable("REFRESH_TOKEN"),
                "cookie": Utils.get_environment_variable("TGTG_COOKIE"),
                "last_time_token_refreshed": Utils.get_environment_variable("LAST_TIME_TOKEN_REFRESHED"),
            },
            "cooldown_end_time": None,
            "user_language": Utils.get_environment_variable("USER_LANGUAGE", default="en"),
            "notifications": {},
        }

    @staticmethod
    def _today() -> str:
        return datetime.now(pytz.utc).date().isoformat()

    def _prune_notifications(
        self,
        state: dict
    ) -> None:
        """Drop notification entries older than today, bounding the file size."""
        today = self._today()
        state["notifications"] = {
            store_id: notified_on
            for store_id, notified_on in state.get("notifications", {}).items()
            if notified_on == today
        }

    def _write(
        self,
        state: dict
    ) -> None:
        """Write atomically: temp file then os.replace, so a crash never truncates state.json."""
        self._prune_notifications(state)
        tmp_path = Path(str(self.path) + ".tmp")
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file, indent=2)
        os.replace(tmp_path, self.path)

    def _save(self) -> None:
        self._write(self._state)

    def get_tgtg_credentials(self) -> TgtgCredentials:
        """
        Return the current TGTG session.

        WARNING - the .env seed is ONE-WAY. Environment variables populate
        state.json only at its creation. Editing ACCESS_TOKEN in .env and
        redeploying has NO effect while state.json exists on the VPS. To
        force new tokens:

            ssh $VPS_USER@$VPS_HOST "rm $VPS_BOT_PATH/state.json"

        This is deliberate: deploy.sh must never overwrite a fresh session
        with a stale local copy, which would trigger a re-login and a CAPTCHA.
        """
        tgtg = self._state.get("tgtg", {})
        return TgtgCredentials(
            access_token=tgtg.get("access_token"),
            refresh_token=tgtg.get("refresh_token"),
            cookie=tgtg.get("cookie"),
            last_time_token_refreshed=tgtg.get("last_time_token_refreshed"),
        )

    def save_tgtg_credentials(
        self,
        credentials: TgtgCredentials
    ) -> None:
        self._state["tgtg"] = {
            "access_token": credentials.access_token,
            "refresh_token": credentials.refresh_token,
            "cookie": credentials.cookie,
            "last_time_token_refreshed": credentials.last_time_token_refreshed,
        }
        self._save()
        LOGGER.info("TGTG credentials refreshed and persisted.")

    def cooldown_remaining(self) -> Optional[float]:
        """Remaining cooldown in seconds, or None if no cooldown is active."""
        raw_end_time = self._state.get("cooldown_end_time")
        if not raw_end_time:
            return None

        end_time = datetime.fromisoformat(raw_end_time)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=pytz.utc)

        remaining = (end_time - datetime.now(pytz.utc)).total_seconds()
        return remaining if remaining > 0 else None

    def is_paused(self) -> bool:
        return self.cooldown_remaining() is not None

    def set_cooldown(
        self,
        minutes: int
    ) -> None:
        end_time = datetime.now(pytz.utc) + timedelta(minutes=minutes)
        self._state["cooldown_end_time"] = end_time.isoformat()
        self._save()
        LOGGER.info(f"Cooldown activated for {minutes} minutes.")

    def clear_cooldown(self) -> None:
        self._state["cooldown_end_time"] = None
        self._save()
        LOGGER.info("Cooldown cleared.")

    def get_language(self) -> str:
        return self._state.get("user_language", "en")

    def set_language(
        self,
        language: str
    ) -> None:
        self._state["user_language"] = language
        self._save()

    def was_notified_today(
        self,
        store_id: str
    ) -> bool:
        return self._state.get("notifications", {}).get(str(store_id)) == self._today()

    def mark_notified(
        self,
        store_id: str
    ) -> None:
        self._state.setdefault("notifications", {})[str(store_id)] = self._today()
        self._save()
