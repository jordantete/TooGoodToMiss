import os, json, requests
from typing import Optional
from urllib.parse import quote
from app.common.logger import LOGGER
from app.common.constants import LOCALIZATIONS_FILE_PATH, TELEGRAM_API_URL

class Utils:
    @classmethod
    def get_environment_variable(
        cls, 
        var_name: str, 
        default: Optional[str] = None
    ):
        """Retrieve an environment variable, defaulting to the provided value if not found."""
        value = os.getenv(var_name, default)

        if value is None:
            LOGGER.warning(f"Environment variable '{var_name}' is not set and no default value was provided.")
        return value

    @staticmethod
    def load_localizable_data():
        """Load localization data from JSON."""
        try:
            with open(LOCALIZATIONS_FILE_PATH, "r", encoding="utf-8") as file:
                return json.load(file)
            
        except FileNotFoundError:
            LOGGER.error(f"Localization file not found at {LOCALIZATIONS_FILE_PATH}")

        except json.JSONDecodeError as e:
            LOGGER.error(f"Error decoding JSON in localization file: {e}")

        return {}

    @staticmethod
    def localize(
        key, 
        language, 
        localizable_data
    ):
        """Retrieve a localized string by key and language."""
        translation = localizable_data.get(language, {}).get(key)
        if not translation:
            LOGGER.warning(f"Missing translation for '{key}' in '{language}'")
            return ""
        return translation

    @staticmethod
    def send_telegram_message(
        text: str, 
        chat_id: Optional[str] = None,
        parse_mode: str = "Markdown",
        disable_web_page_preview: bool = True
    ):
        """Send a message via Telegram to a specific user or default chat."""
        bot_token = Utils.get_environment_variable("TELEGRAM_BOT_TOKEN")
        if not bot_token:
            LOGGER.error("Telegram bot token is missing.")
            return
        
        if chat_id is None:
            chat_id = Utils.get_environment_variable("TELEGRAM_CHAT_ID")
            if not chat_id:
                LOGGER.error("Telegram chat ID is missing and was not provided.")
                return

        encoded_text = quote(text, safe='')
        url = (
            f"{TELEGRAM_API_URL.format(token=bot_token)}"
            f"?chat_id={chat_id}&disable_web_page_preview={disable_web_page_preview}&parse_mode={parse_mode}&text={encoded_text}"
        )
        
        try:
            response = requests.get(url)
            response.raise_for_status()
            LOGGER.info(f"Telegram message sent successfully to chat_id: {chat_id}")

        except requests.RequestException as e:
            status_code = e.response.status_code if e.response is not None else None
            LOGGER.error(f"Failed to send Telegram message to chat_id: {chat_id}. {type(e).__name__} (status={status_code}).")

        except Exception as e:
            LOGGER.error(f"Unexpected error while sending Telegram message: {type(e).__name__}.")

    @staticmethod
    def format_remaining_time(remaining_seconds: float) -> str:
        """Convert remaining seconds into a more readable format (e.g., hours, minutes, seconds)."""
        hours = int(remaining_seconds // 3600)
        minutes = int((remaining_seconds % 3600) // 60)
        seconds = int(remaining_seconds % 60)

        time_parts = []
        if hours > 0:
            time_parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
        if minutes > 0:
            time_parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")
        if seconds > 0:
            time_parts.append(f"{seconds} second{'s' if seconds > 1 else ''}")
        
        return " ".join(time_parts)