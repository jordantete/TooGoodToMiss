import logging

# Loggers that are either very verbose at INFO or log full request URLs
# (which embed the Telegram bot token, e.g. httpx's "HTTP Request: POST
# https://api.telegram.org/bot<TOKEN>/getUpdates" fired every ~10s by long
# polling). logs/app.log is persistent on the VPS, unlike CloudWatch, so this
# must never reach INFO on the root logger.
_NOISY_LOGGER_NAMES = ("httpx", "httpcore", "telegram", "apscheduler")

def create_logger():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    for noisy_logger_name in _NOISY_LOGGER_NAMES:
        logging.getLogger(noisy_logger_name).setLevel(logging.WARNING)

    return logger

LOGGER = create_logger()