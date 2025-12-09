import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


class HistoryRepository:
    """
    Простое хранилище истории SOC в SQLite.
    """

    def __init__(self, db_path: str):
        self._db_path = Path(db_path)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS soc_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    soc INTEGER,
                    generation_power REAL,
                    battery_power REAL
                )
                """
            )
        conn.close()

    def insert_sample(
        self,
        ts: int,
        soc: Optional[int],
        generation_power: Optional[float],
        battery_power: Optional[float],
    ) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute(
                """
                INSERT INTO soc_samples (ts, soc, generation_power, battery_power)
                VALUES (?, ?, ?, ?)
                """,
                (ts, soc, generation_power, battery_power),
            )
        conn.close()

    def get_history(
        self, since_ts: Optional[int] = None, limit: int = 1000
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        cur = conn.cursor()

        if since_ts is not None:
            cur.execute(
                """
                SELECT ts, soc, generation_power, battery_power
                FROM soc_samples
                WHERE ts >= ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (since_ts, limit),
            )
        else:
            cur.execute(
                """
                SELECT ts, soc, generation_power, battery_power
                FROM soc_samples
                ORDER BY ts DESC
                LIMIT ?
                """,
                (limit,),
            )

        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
