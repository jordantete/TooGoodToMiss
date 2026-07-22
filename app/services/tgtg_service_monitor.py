from typing import Optional
from app.core.scheduler import Scheduler
from app.services.tgtg_service.tgtg_service import TgtgService, Credentials
from app.services.tgtg_service.exceptions import TgtgAPIConnectionError, TgtgAPIParsingError, ForbiddenError
from app.common.logger import LOGGER
from app.common.utils import Utils

class TgtgServiceMonitor:
    def __init__(self):
        self.user_email: Optional[str] = Utils.get_environment_variable("USER_EMAIL")
        self.access_token: Optional[str] = Utils.get_environment_variable("ACCESS_TOKEN")
        self.refresh_token: Optional[str] = Utils.get_environment_variable("REFRESH_TOKEN")
        self.tgtg_cookie: Optional[str] = Utils.get_environment_variable("TGTG_COOKIE")
        self.last_time_token_refreshed: Optional[str] = Utils.get_environment_variable("LAST_TIME_TOKEN_REFRESHED")
        aws_account_id = Utils.get_environment_variable("AWS_ACCOUNT_ID")
        aws_region = Utils.get_environment_variable("DEFAULT_AWS_REGION")
        self.monitoring_lambda_arn = f"arn:aws:lambda:{aws_region}:{aws_account_id}:function:too-good-to-miss-monitoring"
        self.tgtg_service = TgtgService()
    
    def start_monitoring(self, scheduler: Scheduler) -> None:
        """
        Start the monitoring process by checking for valid credentials. 
        If the credentials are valid, it proceeds to monitor the favorites.
        """
        if not (self.user_email or self.access_token and self.refresh_token and self.tgtg_cookie):
            LOGGER.error("Missing or invalid credentials. Please ensure that all your environment variables are set correctly.")
            LOGGER.error(f"Current credentials are: user_email: {self.user_email}, access_token: {self.access_token}, refresh_token: {self.refresh_token}, tgtg_cookie: {self.tgtg_cookie}")
            return None

        self._monitor_favorites(scheduler)

    def has_tgtg_token_credentials_been_updated(self) -> bool:
        """Check if the new credentials retrieved differ from the current ones."""
        try:
            new_credentials = self.tgtg_service.credentials
            token_credentials_updated = (
                new_credentials.access_token != Utils.get_environment_variable("ACCESS_TOKEN") or
                new_credentials.refresh_token != Utils.get_environment_variable("REFRESH_TOKEN")
            )
            return token_credentials_updated

        except Exception as e:
            LOGGER.error(f"Error checking if TGTG credentials have been updated: {e}")
            return False

    def _monitor_favorites(self, scheduler: Scheduler) -> None:
        """Check favorite items and send notifications if new items are available."""
        LOGGER.info("Checking favorite items and sending notifications if needed.")
        try:
            favorites = self.tgtg_service.get_favorites_items_list(
                self.user_email, 
                self.access_token, 
                self.refresh_token, 
                self.tgtg_cookie,
                self.last_time_token_refreshed
            )

            LOGGER.info("Will check if env var credentials needs to be udpated...")

            if self.has_tgtg_token_credentials_been_updated():
                self.update_credentials_env_vars(new_credentials=self.tgtg_service.credentials)
            
            messages = self.tgtg_service.get_notification_messages(favorites)

            for message in messages:
                LOGGER.info(f"Sending Telegram message: {message}")
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
    
    def update_credentials_env_vars(
        self, 
        new_credentials: Credentials
    ):
        LOGGER.info(f"Will update env vars with new TGTG credentials: {new_credentials}")
        new_env_vars = {
            "ACCESS_TOKEN": new_credentials.access_token,
            "REFRESH_TOKEN": new_credentials.refresh_token,
            "TGTG_COOKIE": new_credentials.cookie,
            "LAST_TIME_TOKEN_REFRESHED": new_credentials.get_last_time_token_refreshed_as_str()
        }
        Utils.update_lambda_env_vars(self.monitoring_lambda_arn, new_env_vars)