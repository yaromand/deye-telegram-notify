import logging
from typing import Optional

import requests


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self._bot_token = bot_token
        self._chat_id = chat_id

    def send_message(self, text: str, parse_mode: Optional[str] = "HTML") -> bool:
        if not self._bot_token or not self._chat_id:
            logging.warning("Telegram not configured, skip sending")
            return False

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            if not resp.ok:
                logging.error(
                    "Telegram send failed: %s %s", resp.status_code, resp.text
                )
                return False
            return True
        except Exception as e:
            logging.exception("Error sending Telegram message: %s", e)
            return False
