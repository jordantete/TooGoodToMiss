import os, pytest, requests
from unittest.mock import patch, mock_open, MagicMock
from app.common.utils import Utils

class TestUtils:
    @pytest.fixture
    def mock_env_vars(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test_bot_token", "TELEGRAM_CHAT_ID": "test_chat_id"}):
            yield

    @patch("builtins.open", new_callable=mock_open, read_data='{"en": {"greeting": "Hello"}, "fr": {"greeting": "Bonjour"}}')
    def test_load_localizable_data_success(self, mock_file):
        data = Utils.load_localizable_data()
        assert data == {"en": {"greeting": "Hello"}, "fr": {"greeting": "Bonjour"}}

    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_load_localizable_data_file_not_found(self, mock_file):
        data = Utils.load_localizable_data()
        assert data == {}

    @patch("builtins.open", new_callable=mock_open, read_data="{invalid_json}")
    def test_load_localizable_data_invalid_json(self, mock_file):
        data = Utils.load_localizable_data()
        assert data == {}

    def test_localize_success(self):
        localizable_data = {"en": {"greeting": "Hello"}, "fr": {"greeting": "Bonjour"}}
        translation = Utils.localize("greeting", "en", localizable_data)
        assert translation == "Hello"

    def test_localize_missing_translation(self):
        localizable_data = {"en": {"greeting": "Hello"}}
        translation = Utils.localize("farewell", "en", localizable_data)
        assert translation == ""

    def test_localize_missing_language(self):
        localizable_data = {"en": {"greeting": "Hello"}}
        translation = Utils.localize("greeting", "fr", localizable_data)
        assert translation == ""

    def test_get_environment_variable_with_value(self, mock_env_vars):
        value = Utils.get_environment_variable("TELEGRAM_BOT_TOKEN")
        assert value == "test_bot_token"

    def test_get_environment_variable_with_default(self):
        value = Utils.get_environment_variable("NON_EXISTENT_VAR", default="default_value")
        assert value == "default_value"

    def test_get_environment_variable_without_default(self):
        value = Utils.get_environment_variable("NON_EXISTENT_VAR")
        assert value is None

    @patch("requests.get")
    def test_send_telegram_message_success(self, mock_requests, mock_env_vars):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_requests.return_value = mock_response

        Utils.send_telegram_message("Test message")
        mock_requests.assert_called_once()
        args, kwargs = mock_requests.call_args
        assert "test_bot_token" in args[0]
        assert "test_chat_id" in args[0]

    @patch("requests.get", side_effect=Exception("Network error"))
    @patch("app.common.utils.Utils.get_environment_variable", side_effect=["test_bot_token", "test_chat_id"])
    @patch("app.common.logger.LOGGER.error")
    def test_send_telegram_message_failure(self, mock_logger, mock_env_vars, mock_requests):
        Utils.send_telegram_message("Test message")
        mock_requests.assert_called_once()
        mock_logger.assert_called_once_with("Unexpected error while sending Telegram message: Network error.")

    @patch("app.common.logger.LOGGER.error")
    def test_send_telegram_message_http_error_does_not_leak_token(self, mock_logger, mock_env_vars):
        """Finding 3 regression: the real leak was in the RequestException branch -
        HTTPError.__str__ from raise_for_status() embeds the full request URL,
        which contains the Telegram bot token."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        http_error = requests.HTTPError(
            "401 Client Error: Unauthorized for url: https://api.telegram.org/bottest_bot_token/sendMessage",
            response=mock_response
        )

        with patch("requests.get") as mock_requests:
            mock_requests.return_value.raise_for_status.side_effect = http_error
            Utils.send_telegram_message("Test message")

        mock_logger.assert_called_once()
        logged_message = mock_logger.call_args[0][0]
        assert "test_bot_token" not in logged_message

    @patch("requests.get")
    def test_send_telegram_message_missing_bot_token(self, mock_requests):
        with patch.dict(os.environ, {}, clear=True):
            Utils.send_telegram_message("Test message")
            mock_requests.assert_not_called()

    @patch("requests.get")
    def test_send_telegram_message_missing_chat_id(self, mock_requests):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test_bot_token"}, clear=True):
            Utils.send_telegram_message("Test message")
            mock_requests.assert_not_called()