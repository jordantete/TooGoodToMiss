import logging, os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Loggers that are either very verbose at INFO or log full request URLs
# (which embed the Telegram bot token, e.g. httpx's "HTTP Request: POST
# https://api.telegram.org/bot<TOKEN>/getUpdates" fired every ~10s by long
# polling). logs/app.log is persistent on the VPS, unlike CloudWatch, so this
# must never reach INFO on the root logger.
_NOISY_LOGGER_NAMES = ("httpx", "httpcore", "telegram", "apscheduler")

LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
LOG_FILE = LOG_DIR / "app.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5

def create_logger():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    except OSError as e:
        logger.warning(f"File logging disabled ({e}) - falling back to stdout only.")

    for noisy_logger_name in _NOISY_LOGGER_NAMES:
        logging.getLogger(noisy_logger_name).setLevel(logging.WARNING)

    return logger

LOGGER = create_logger()