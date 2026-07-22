from app.common.logger import LOGGER
from app.common.utils import Utils
from app.core.scheduler import Scheduler
from app.core.state import StateStore, TgtgCredentials
from app.services.tgtg_service.exceptions import TgtgAPIConnectionError, TgtgAPIParsingError, ForbiddenError
from app.services.tgtg_service.tgtg_service import TgtgService

class TgtgServiceMonitor:
    def __init__(
        self,
        state: StateStore
    ):
        self.state = state
        self.user_email = Utils.get_environment_variable("USER_EMAIL")
        self.tgtg_service = TgtgService(state)

    def start_monitoring(
        self,
        scheduler: Scheduler
    ) -> None:
        """Run one monitoring pass if credentials look usable."""
        credentials = self.state.get_tgtg_credentials()

        has_session = credentials.access_token and credentials.refresh_token and credentials.cookie
        if not (self.user_email or has_session):
            LOGGER.error(
                "Missing credentials: set USER_EMAIL, or seed ACCESS_TOKEN / REFRESH_TOKEN / "
                "TGTG_COOKIE in .env before first start."
            )
            return None

        self._monitor_favorites(scheduler)

    def _has_session_changed(
        self,
        stored: TgtgCredentials
    ) -> bool:
        """Check whether the TGTG client rotated the session during this pass."""
        fresh = self.tgtg_service.credentials
        if fresh is None:
            return False
        return (
            fresh.access_token != stored.access_token
            or fresh.refresh_token != stored.refresh_token
        )

    def _monitor_favorites(
        self,
        scheduler: Scheduler
    ) -> None:
        """Check favorite items and send notifications if new items are available."""
        LOGGER.info("Checking favorite items and sending notifications if needed.")
        stored = self.state.get_tgtg_credentials()

        try:
            favorites = self.tgtg_service.get_favorites_items_list(
                self.user_email,
                stored.access_token,
                stored.refresh_token,
                stored.cookie,
                stored.last_time_token_refreshed
            )

            if self._has_session_changed(stored):
                fresh = self.tgtg_service.credentials
                self.state.save_tgtg_credentials(
                    TgtgCredentials(
                        access_token=fresh.access_token,
                        refresh_token=fresh.refresh_token,
                        cookie=fresh.cookie,
                        last_time_token_refreshed=fresh.get_last_time_token_refreshed_as_str(),
                    )
                )

            messages = self.tgtg_service.get_notification_messages(favorites)

            for message in messages:
                LOGGER.info("Sending Telegram notification.")
                Utils.send_telegram_message(message)

            if not messages:
                LOGGER.info("No new items available - no notifications sent.")

        except TgtgAPIParsingError as e:
            error_msg = f"TgtgAPIParsingError encountered: {str(e)}"
            LOGGER.error(error_msg)
            Utils.send_telegram_message(f"TgtgAPIParsingError: {error_msg}")

        except ForbiddenError as e:
            LOGGER.error(str(e))
            scheduler.activate_cooldown()
            Utils.send_telegram_message("API access forbidden. Monitoring paused temporarily.")

        except TgtgAPIConnectionError as e:
            LOGGER.error(f"Connection error to TGTG API. {str(e)}")
            Utils.send_telegram_message(f"TGTG API connection error: {str(e)}")

        except Exception as e:
            LOGGER.error(f"Unexpected error in _monitor_favorites: {str(e)}")
            Utils.send_telegram_message(f"TooGoodToMiss: Unexpected system error - {str(e)}")
