import copy
import logging
import threading
import time
from typing import Any, Dict, Optional

from app.config import Settings
from app.db.repository import HistoryRepository
from app.deye.client import DeyeClient
from app.telegram.notifier import TelegramNotifier


class MonitorService:
    """
    Сервис-оркестратор:
    - опрос Deye Cloud
    - лог в БД
    - триггер Telegram-уведомлений
    - отдача статуса/истории для веб-слоя
    """

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
        self._alert_state: Dict[str, Any] = {
            "status": "unknown",  # unknown | ok | low
            "last_soc": None,
            "last_alert_ts": None,
        }

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---------- публичное API для старта/остановки ----------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logging.info("MonitorService started")

    def stop(self) -> None:
        self._stop_event.set()

    # ---------- публичное API для веб-слоя ----------

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

    # ---------- внутреннее ----------

    def _select_station_id(self) -> Optional[int]:
        if self._station_id is not None:
            return self._station_id

        stations = self._client.get_station_list(page=1, size=10)
        if not stations:
            logging.warning("No stations found in Deye account")
            return None

        first = stations[0]
        # Уточни ключ id по реальному JSON
        station_id = int(first.get("id"))
        name = first.get("name")
        self._station_id = station_id
        logging.info("Using station id=%s name=%s", station_id, name)
        return station_id

    def _run_loop(self) -> None:
        logging.info(
            "MonitorService loop started, interval=%s sec", self._poll_interval
        )
        while not self._stop_event.is_set():
            try:
                station_id = self._select_station_id()
                if station_id is None:
                    logging.warning("No station_id, skip iteration")
                else:
                    payload = self._client.get_station_latest(station_id)

                    # Тут предполагаем, что Deye отдаёт batterySOC / generationPower / batteryPower
                    # либо на верхнем уровне, либо в data["data"] — см. клиента.
                    soc = payload.get("batterySOC")
                    gen = payload.get("generationPower")
                    bat_p = payload.get("batteryPower")

                    now_ts = int(time.time())

                    # в БД
                    self._repo.insert_sample(now_ts, soc, gen, bat_p)

                    # текущее состояние
                    with self._status_lock:
                        self._current_status = {
                            "soc": soc,
                            "generationPower": gen,
                            "batteryPower": bat_p,
                            "lastUpdateTime": now_ts,
                        }

                    # алерты
                    self._handle_alert(soc, gen, bat_p)

            except Exception:
                logging.exception("Error in monitor loop")

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

        # Низкий заряд → алерт
        if soc <= self._low_thr and prev_status != "low":
            text = (
                f"⚠️ <b>Deye батарея разряжена</b>\n"
                f"SOC: <b>{soc}%</b>\n"
                f"PV: {generation_power if generation_power is not None else '—'} W\n"
                f"Battery: {battery_power if battery_power is not None else '—'} W\n"
            )
            if self._notifier.send_message(text):
                logging.info("Low SOC alert sent to Telegram, soc=%s", soc)
            self._alert_state.update(
                {
                    "status": "low",
                    "last_soc": soc,
                    "last_alert_ts": int(time.time()),
                }
            )
            return

        # Вышли из "низкой" зоны → recovery-алерт
        if soc >= self._reset_thr and prev_status == "low":
            text = f"✅ <b>Deye батарея восстановилась</b>\nSOC: <b>{soc}%</b>"
            if self._notifier.send_message(text):
                logging.info("Recovery alert sent to Telegram, soc=%s", soc)
            self._alert_state.update(
                {
                    "status": "ok",
                    "last_soc": soc,
                    "last_alert_ts": int(time.time()),
                }
            )
            return

        # Просто обновляем последний SOC
        self._alert_state["last_soc"] = soc
