from dataclasses import dataclass
from pydantic import ValidationError
from datetime import datetime
from typing import List, Optional
from app.common.logger import LOGGER
from app.core.state import StateStore
from app.services.tgtg_service.tgtg_client import TgtgClient
from app.services.tgtg_service.notification_formatter import NotificationFormatter
from app.services.tgtg_service.models import ItemDetails
from app.services.tgtg_service.exceptions import TgtgLoginError, TgtgAPIConnectionError, TgtgAPIParsingError, ForbiddenError

@dataclass
class Credentials:
    access_token: Optional[str]
    refresh_token: Optional[str]
    cookie: Optional[str]
    last_time_token_refreshed: Optional[datetime]

    def get_last_time_token_refreshed_as_str(self) -> str:
        if self.last_time_token_refreshed:
            return self.last_time_token_refreshed.isoformat()
        return ""

class TgtgService:
    USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_7_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1"

    def __init__(
        self,
        state: StateStore
    ):
        self.state = state
        self.credentials: Credentials = None

    def get_favorites_items_list(
            self,
            email: Optional[str], 
            access_token: Optional[str], 
            refresh_token: Optional[str], 
            cookie: Optional[str],
            last_time_token_refreshed_str: Optional[str]
        ) -> List[ItemDetails]:
        """Login to TGTG if needed and fetch and parse favorite items from TGTG API."""
        LOGGER.info("Logging in to TGTG API.")
        last_time_token_refreshed = datetime.fromisoformat(last_time_token_refreshed_str) if last_time_token_refreshed_str else None

        try: 
            tgtg_client = TgtgClient(
                email=email, 
                access_token=access_token, 
                refresh_token=refresh_token, 
                cookie=cookie, 
                user_agent=self.USER_AGENT, 
                last_time_token_refreshed=last_time_token_refreshed,
                device_type="IPHONE"
            )
            LOGGER.info("TGTG client initialised.")

        except Exception as e:
            raise TgtgLoginError("Unable to login with provided credentials.") from e

        LOGGER.info("Fetching favorite items from TGTG API.")
        try:
            json_data = tgtg_client.get_favorites()
            self.credentials = Credentials(tgtg_client.access_token, tgtg_client.refresh_token, tgtg_client.cookie, tgtg_client.last_time_token_refreshed)
            LOGGER.info("Local credentials updated after TGTG request.")
            LOGGER.info(f"Raw API response: {json_data}")
            favorites = [ItemDetails(**item) for item in json_data]
            LOGGER.info(f"Parsed {len(favorites)} favorite items from TGTG API.")
            return favorites
        
        except ValidationError as e:
            raise TgtgAPIParsingError("Error parsing item details.") from e

        except Exception as e:
            error_message = str(e)
            LOGGER.error(f"Unexpected error occurred: {error_message}")

            if "captcha" in error_message.lower():
                LOGGER.error("Anti-bot CAPTCHA challenge detected.")
                raise ForbiddenError("Blocked by CAPTCHA challenge.") from e
            else:
                raise TgtgAPIConnectionError("An unexpected error occurred while connecting to TGTG API.") from e

    def get_notification_messages(
        self,
        item_details_list: List[ItemDetails]
    ) -> List[str]:
        """Generate notification messages for available favorite items."""
        messages = []
        for item_details in item_details_list:
            store_id = str(item_details.store.store_id)

            if item_details.items_available > 0 and not self.state.was_notified_today(store_id):
                messages.append(NotificationFormatter.format_message(item_details))
                self.state.mark_notified(store_id)

        return messages
