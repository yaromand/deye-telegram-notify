import hashlib
import time
from typing import Any, Dict, List, Optional

import requests

from app.config import Settings


class DeyeClient:
    """
    Мини-клиент для Deye Cloud OpenAPI:
    - POST /account/token?appId=...   -> accessToken / refreshToken / expiresIn
    - POST /station/list              -> список станций
    - POST /station/latest            -> последние данные по станции

    Логика токена:
      * При первом запросе логинимся и сохраняем accessToken + expiresIn.
      * Перед каждым запросом проверяем, не истёк ли токен по времени.
      * Если от API прилетает 401, сбрасываем токен и логинимся заново, потом повторяем запрос один раз.
    """

    def __init__(self, settings: Settings):
        self.base_url = settings.deye_base_url.rstrip("/")
        self.app_id = settings.deye_app_id
        self.app_secret = settings.deye_app_secret
        self.email = settings.deye_email
        self.password = settings.deye_password

        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expire_at: float = 0.0  # unix time

    # ---------- внутренняя авторизация ----------

    def _hash_password(self) -> str:
        """
        Deye ожидает пароль в виде SHA256-хеша (hex lower-case).
        """
        return hashlib.sha256(self.password.encode("utf-8")).hexdigest().lower()

    def _login(self) -> None:
        """
        Реальный запрос к /account/token.

        Ожидаемый ответ (по твоему примеру):

        {
            "code": "1000000",
            "msg": "success",
            "success": true,
            "accessToken": "...",
            "tokenType": "bearer",
            "refreshToken": "...",
            "expiresIn": "5183999",
            "scope": "all",
            "uid": 1000
        }
        """
        url = f"{self.base_url}/account/token"
        params = {"appId": self.app_id}
        payload: Dict[str, Any] = {
            "appSecret": self.app_secret,
            "email": self.email,
            "password": self._hash_password(),
        }

        resp = requests.post(url, params=params, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success", False) or data.get("code") not in (None, "1000000"):
            raise RuntimeError(f"Deye auth failed: {data}")

        access_token = data.get("accessToken")
        if not access_token:
            raise RuntimeError(f"No accessToken in Deye response: {data}")

        self._access_token = access_token
        self._refresh_token = data.get("refreshToken")

        expires_raw = data.get("expiresIn", 3600)
        try:
            expires_in = int(expires_raw)
        except (ValueError, TypeError):
            expires_in = 3600

        # небольшой запас по времени
        self._token_expire_at = time.time() + expires_in - 60

    def _ensure_token(self) -> None:
        """
        Гарантирует, что у нас есть валидный accessToken.
        """
        now = time.time()
        if self._access_token is None or now >= self._token_expire_at:
            self._login()

    def _auth_headers(self) -> Dict[str, str]:
        self._ensure_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        """
        Обёртка над requests.request:
        - автоматически подставляет Authorization
        - при 401 сбрасывает токен и логинится заново, потом повторяет запрос один раз
        """
        self._ensure_token()
        url = f"{self.base_url}{path}"

        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._auth_headers())

        resp = requests.request(method, url, headers=headers, timeout=15, **kwargs)

        if resp.status_code == 401:
            # токен протух / отозван — перелогиниваемся
            self._access_token = None
            self._token_expire_at = 0
            self._ensure_token()

            headers = kwargs.pop("headers", {}) or {}
            headers.update(self._auth_headers())
            resp = requests.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                timeout=15,
                **kwargs,
            )

        resp.raise_for_status()
        return resp.json()

    # public api

    def get_station_list(self, page: int = 1, size: int = 10) -> List[Dict[str, Any]]:
        data = self._request(
            "POST",
            "/station/list",
            json={"page": page, "size": size},
        )

        if "stationList" in data:
            return data["stationList"]
        if "data" in data and isinstance(data["data"], dict):
            return data["data"].get("stationList", [])

        return []

    def get_station_latest(self, station_id: int) -> Dict[str, Any]:
        data = self._request(
            "POST",
            "/station/latest",
            json={"stationId": station_id},
        )

        if "data" in data and isinstance(data["data"], dict):
            return data["data"]
        return data
