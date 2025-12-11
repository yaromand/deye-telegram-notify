import logging

from app.config import settings
from app.db.repository import HistoryRepository
from app.deye.client import DeyeClient
from app.telegram.notifier import TelegramNotifier
from app.services.monitor import MonitorService
from app.web import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# init
history_repo = HistoryRepository(settings.db_path)
history_repo.init_db()

deye_client = DeyeClient(settings)
notifier = TelegramNotifier(
    bot_token=settings.telegram_bot_token,
    chat_id=settings.telegram_chat_id,
)

monitor_service = MonitorService(
    deye_client=deye_client,
    history_repo=history_repo,
    notifier=notifier,
    settings=settings,
)
monitor_service.start()

app = create_app(monitor_service)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
