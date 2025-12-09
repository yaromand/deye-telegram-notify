import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    # Deye Cloud
    deye_app_id: str
    deye_app_secret: str
    deye_email: str
    deye_password: str
    deye_station_id: Optional[int]
    deye_base_url: str

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str

    # Мониторинг
    poll_interval_sec: int
    low_soc_threshold: int
    low_soc_reset: int

    # БД
    db_path: str


def _must_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is required")
    return value


def load_settings() -> Settings:
    station_id_raw = os.getenv("DEYE_STATION_ID")
    station_id = int(station_id_raw) if station_id_raw else None

    return Settings(
        deye_app_id=_must_env("DEYE_APP_ID"),
        deye_app_secret=_must_env("DEYE_APP_SECRET"),
        deye_email=_must_env("DEYE_EMAIL"),
        deye_password=_must_env("DEYE_PASSWORD"),
        deye_station_id=station_id,
        deye_base_url=os.getenv(
            "DEYE_BASE_URL", "https://eu1-developer.deyecloud.com/v1.0"
        ),
        telegram_bot_token=_must_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_must_env("TELEGRAM_CHAT_ID"),
        poll_interval_sec=int(os.getenv("POLL_INTERVAL_SEC", "60")),
        low_soc_threshold=int(os.getenv("LOW_SOC_THRESHOLD", "20")),
        low_soc_reset=int(os.getenv("LOW_SOC_RESET", "25")),
        db_path=os.getenv("DB_PATH", "soc_history.db"),
    )


settings = load_settings()
