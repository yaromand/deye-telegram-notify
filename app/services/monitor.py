import copy
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import Settings
from app.db.repository import HistoryRepository
from app.deye.client import DeyeClient
from app.telegram.notifier import TelegramNotifier


class MonitorService:
    """
    Сервіс-оркестратор:
    - опитування Deye Cloud
    - логування в БД
    - тригер Telegram-сповіщень
    - віддача статусу/історії для веб-шару
    """

    # Файл, де зберігаємо стан алертів між перезапусками
    STATE_FILE = Path("alert_state.json")

    def __init__(
        self,
        deye_client: DeyeClient,
        history_repo: HistoryRepository,
        notifier: TelegramNotifier,
        settings: Settings,
    ):
        self._client = deye_client
        self._repo = history_repo
        self._notifier = notifier
        self._settings = settings

        self._station_id: Optional[int] = settings.deye_station_id

        self._poll_interval = settings.poll_interval_sec
        self._low_thr = settings.low_soc_threshold
        self._reset_thr = settings.low_soc_reset

        self._status_lock = threading.Lock()
        self._current_status: Dict[str, Any] = {
            "soc": None,
            "generationPower": None,
            "batteryPower": None,
            "lastUpdateTime": None,
        }
        # Стейт алертів (буде підвантажено з файлу, якщо він є)
        self._alert_state: Dict[str, Any] = {
            "status": "unknown",  # unknown | ok | low
            "last_soc": None,
            "last_alert_ts": None,
        }

        self._load_alert_state_from_disk()

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---------- робота зі стейтом на диску ----------

    def _load_alert_state_from_disk(self) -> None:
        """
        Підвантажуємо стан алертів з JSON-файлу, якщо він існує.
        Це дозволяє не дублювати повідомлення після перезапуску сервісу.
        """
        try:
            if self.STATE_FILE.exists():
                with self.STATE_FILE.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._alert_state["status"] = data.get("status", "unknown")
                    self._alert_state["last_soc"] = data.get("last_soc")
                    self._alert_state["last_alert_ts"] = data.get("last_alert_ts")
                    logging.info(
                        "Завантажено стан алертів з файлу: %s", self._alert_state
                    )
        except Exception:
            logging.exception("Не вдалося завантажити стан алертів з файлу")

    def _persist_alert_state(self) -> None:
        """
        Зберігаємо поточний стан алертів у JSON-файл.
        Викликаємо тільки при зміні статусу (low/ok), щоб уникнути зайвих записів.
        """
        try:
            data = {
                "status": self._alert_state.get("status"),
                "last_soc": self._alert_state.get("last_soc"),
                "last_alert_ts": self._alert_state.get("last_alert_ts"),
            }
            with self.STATE_FILE.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            logging.exception("Не вдалося зберегти стан алертів у файл")

    # ---------- публічне API для старту/зупинки ----------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logging.info("MonitorService запущено")

    def stop(self) -> None:
        self._stop_event.set()

    # ---------- публічне API для веб-шару ----------

    def get_status(self) -> Dict[str, Any]:
        with self._status_lock:
            status_copy = copy.deepcopy(self._current_status)
            alert_copy = copy.deepcopy(self._alert_state)

        status_copy.update(
            {
                "pollIntervalSec": self._poll_interval,
                "threshold": self._low_thr,
                "resetThreshold": self._reset_thr,
                "alertState": alert_copy,
                "serverTime": int(time.time()),
            }
        )
        return status_copy

    def get_history_last_24h(self) -> Dict[str, Any]:
        now = int(time.time())
        since = now - 24 * 3600
        items = self._repo.get_history(since_ts=since, limit=1000)
        return {"items": items}

    # ---------- внутрішня логіка ----------

    def _select_station_id(self) -> Optional[int]:
        if self._station_id is not None:
            return self._station_id

        stations = self._client.get_station_list(page=1, size=10)
        if not stations:
            logging.warning("У акаунті Deye не знайдено жодної станції")
            return None

        first = stations[0]
        # Уточни ключ id за реальним JSON
        station_id = int(first.get("id"))
        name = first.get("name")
        self._station_id = station_id
        logging.info("Використовуємо станцію id=%s name=%s", station_id, name)
        return station_id

    def _run_loop(self) -> None:
        logging.info(
            "Запущено цикл MonitorService, інтервал=%s сек", self._poll_interval
        )
        while not self._stop_event.is_set():
            try:
                station_id = self._select_station_id()
                if station_id is None:
                    logging.warning("Немає station_id, пропускаємо ітерацію")
                else:
                    payload = self._client.get_station_latest(station_id)
                    print(payload)
                    # Тут припускаємо, що Deye віддає batterySOC / generationPower / batteryPower
                    # або на верхньому рівні, або в data["data"] — див. клієнт.
                    soc = payload.get("batterySOC")
                    gen = payload.get("generationPower")
                    bat_p = payload.get("batteryPower")

                    now_ts = int(time.time())

                    # логування в БД
                    self._repo.insert_sample(now_ts, soc, gen, bat_p)

                    # поточний стан
                    with self._status_lock:
                        self._current_status = {
                            "soc": soc,
                            "generationPower": gen,
                            "batteryPower": bat_p,
                            "lastUpdateTime": now_ts,
                        }

                    # алерти
                    self._handle_alert(soc, gen, bat_p)

            except Exception:
                logging.exception("Помилка в циклі моніторингу")

            self._stop_event.wait(self._poll_interval)

    def _handle_alert(
        self,
        soc: Optional[int],
        generation_power: Optional[float],
        battery_power: Optional[float],
    ) -> None:
        if soc is None:
            return

        prev_status = self._alert_state.get("status")

        # Низький заряд → алерт
        if soc <= self._low_thr and prev_status != "low":
            text = (
                "⚠️ <b>Рівень заряду акумулятора майже досяг критичного порога</b>\n"
                "Можливі відключення електроенергії найближчим часом.\n"
                "Будь ласка, утримайтеся від користування ліфтом, щоб не застрягнути.\n\n"
                f"Заряд акумулятора: <b>{soc}%</b>\n"
           #     f"PV: {generation_power if generation_power is not None else '—'} W\n"
           #     f"Battery: {battery_power if battery_power is not None else '—'} W\n"
            )
            if self._notifier.send_message(text):
                logging.info(
                    "Надіслано попередження про низький SOC у Telegram, soc=%s", soc
                )
            self._alert_state.update(
                {
                    "status": "low",
                    "last_soc": soc,
                    "last_alert_ts": int(time.time()),
                }
            )
            self._persist_alert_state()
            return

        # Вийшли з «низької» зони → сповіщення про відновлення
        if soc >= self._reset_thr and prev_status == "low":
            text = (
                f"✅ <b>Рівень заряду акумулятора відновився вище критичного порога</b>\n"
                f"SOC: <b>{soc}%</b>"
            )
            if self._notifier.send_message(text):
                logging.info(
                    "Надіслано сповіщення про відновлення SOC у Telegram, soc=%s", soc
                )
            self._alert_state.update(
                {
                    "status": "ok",
                    "last_soc": soc,
                    "last_alert_ts": int(time.time()),
                }
            )
            self._persist_alert_state()
            return

        # Просто оновлюємо останній SOC (можна без запису в файл, щоб не спамити IO)
        self._alert_state["last_soc"] = soc
