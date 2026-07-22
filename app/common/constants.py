import os
from typing import Dict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCALIZATIONS_FILE_PATH = os.path.join(BASE_DIR, "localizable.json")
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
WELCOME_GIF_URL = "https://i.giphy.com/media/v1.Y2lkPTc5MGI3NjExY3E3MW95YmwzdXd5ancwM2o1OGhiMTJiN25mem9kMDBuYnh2eWxlaSZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/XD9o33QG9BoMis7iM4/giphy.gif"

WEEKDAY_MAP: Dict[int, str] = {
    0: 'Monday',
    1: 'Tuesday',
    2: 'Wednesday',
    3: 'Thursday',
    4: 'Friday',
    5: 'Saturday',
    6: 'Sunday'
}