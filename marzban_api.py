import httpx
import logging
from datetime import datetime, timedelta
from config import Config

logger = logging.getLogger(__name__)


class MarzbanAPI:
    def __init__(self):
        self.base_url = Config.MARZBAN_URL.rstrip('/')
        self.username = Config.MARZBAN_ADMIN_USERNAME
        self.password = Config.MARZBAN_ADMIN_PASSWORD
        self.token = None
        self.token_expires = None

    async def _ensure_auth(self):
        if self.token and self.token_expires and datetime.now() < self.token_expires:
            return
        await self._login()

    async def _login(self):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/admin/token",
                    data={
                        "username": self.username,
                        "password": self.password,
                        "grant_type": "password"
                    }
                )
            if response.status_code == 200:
                data = response.json()
                self.token = data["access_token"]
                self.token_expires = datetime.now() + timedelta(hours=11)
                logger.info("Marzban: успешно авторизован")
            else:
                logger.error(f"Marzban: ошибка авторизации {response.status_code}: {response.text}")
                raise Exception("Marzban auth failed")
        except Exception as e:
            logger.error(f"Marzban: ошибка соединения: {e}")
            raise

    async def _headers(self) -> dict:
        await self._ensure_auth()
        return {"Authorization": f"Bearer {self.token}"}

    def _build_inbounds_and_proxies(self):
        inbounds = {}
        proxies = {}
        if Config.MARZBAN_VLESS_INBOUND:
            inbounds["vless"] = [Config.MARZBAN_VLESS_INBOUND]
            proxies["vless"] = {"flow": "xtls-rprx-vision"}
        if Config.MARZBAN_HYSTERIA_INBOUND:
            inbounds["hysteria2"] = [Config.MARZBAN_HYSTERIA_INBOUND]
            proxies["hysteria2"] = {}
        return inbounds, proxies

    def _extract_client_url(self, user_data: dict) -> str:
        """Возвращает прямые vless:// ключи (если панель на 127.0.0.1) или публичную sub-ссылку."""
        sub_url = user_data.get("subscription_url", "")
        links = user_data.get("links") or []
        is_local = "127.0.0.1" in self.base_url or "localhost" in self.base_url

        # Если Marzban закрыт на 127.0.0.1 и не задан публичный префикс подписки,
        # отдаём готовые vless:// ключи (включая все подключённые ноды)
        if is_local and sub_url.startswith("/") and links:
            return "\n".join(links)

        if not sub_url:
            return "\n".join(links) if links else ""
        return f"{self.base_url}{sub_url}" if sub_url.startswith("/") else sub_url

    async def create_or_extend_subscription(
        self,
        user_id: int,
        days: int,
        existing_username: str = None,
        data_limit_gb: int = None
    ) -> dict:
        """Создаёт новую подписку или продлевает существующую без смены ссылки клиента."""
        username = existing_username or f"tg_{user_id}"
        limit_gb = data_limit_gb if data_limit_gb is not None else Config.DEFAULT_DATA_LIMIT_GB
        data_limit = limit_gb * 1024 * 1024 * 1024 if limit_gb > 0 else 0

        existing = await self.get_user(username)
        if existing["success"]:
            user_data = existing["data"]
            now_ts = int(datetime.now().timestamp())
            current_expire = user_data.get("expire") or 0
            base_ts = max(current_expire, now_ts)
            new_expire = base_ts + int(timedelta(days=days).total_seconds())

            payload = {
                "status": "active",
                "expire": new_expire,
                "data_limit": data_limit,
            }
            try:
                headers = await self._headers()
                async with httpx.AsyncClient(timeout=15.0) as client:
                    await client.post(
                        f"{self.base_url}/api/user/{username}/reset",
                        headers=headers
                    )
                    response = await client.put(
                        f"{self.base_url}/api/user/{username}",
                        json=payload,
                        headers=headers
                    )
                if response.status_code == 200:
                    updated_data = response.json()
                    full_url = self._extract_client_url(updated_data)
                    logger.info(f"Marzban: продлён пользователь {username} на {days} дн.")
                    return {
                        "success": True,
                        "username": username,
                        "subscription_url": full_url,
                        "expire": new_expire,
                        "data_limit": data_limit,
                        "extended": True
                    }
                else:
                    logger.error(f"Marzban: ошибка продления {response.status_code}: {response.text}")
                    return {"success": False, "error": response.text}
            except Exception as e:
                logger.error(f"Marzban: ошибка при продлении: {e}")
                return {"success": False, "error": str(e)}

        return await self.create_subscription(username, days, data_limit_gb)

    async def create_subscription(self, username: str, days: int, data_limit_gb: int = None) -> dict:
        """Создаёт пользователя в Marzban и возвращает ссылку/ключи."""
        expire_timestamp = int((datetime.now() + timedelta(days=days)).timestamp())

        limit_gb = data_limit_gb if data_limit_gb is not None else Config.DEFAULT_DATA_LIMIT_GB
        data_limit = limit_gb * 1024 * 1024 * 1024 if limit_gb > 0 else 0

        inbounds, proxies = self._build_inbounds_and_proxies()
        if not inbounds:
            return {"success": False, "error": "No inbounds configured in .env"}

        payload = {
            "username": username,
            "status": "active",
            "expire": expire_timestamp,
            "data_limit": data_limit,
            "data_limit_reset_strategy": "no_reset",
            "inbounds": inbounds,
            "proxies": proxies,
            "note": f"Created via Telegram bot at {datetime.now().isoformat()}"
        }

        try:
            headers = await self._headers()
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/user",
                    json=payload,
                    headers=headers
                )
            if response.status_code in (200, 201):
                user_data = response.json()
                full_url = self._extract_client_url(user_data)
                logger.info(f"Marzban: создан пользователь {username}")
                return {
                    "success": True,
                    "username": username,
                    "subscription_url": full_url,
                    "expire": expire_timestamp,
                    "data_limit": data_limit,
                    "extended": False
                }
            else:
                logger.error(f"Marzban: ошибка создания {response.status_code}: {response.text}")
                return {"success": False, "error": response.text}
        except Exception as e:
            logger.error(f"Marzban: ошибка: {e}")
            return {"success": False, "error": str(e)}

    async def get_user(self, username: str) -> dict:
        """Получает информацию о пользователе"""
        try:
            headers = await self._headers()
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.base_url}/api/user/{username}",
                    headers=headers
                )
            if response.status_code == 200:
                data = response.json()
                full_url = self._extract_client_url(data)
                return {"success": True, "data": data, "subscription_url": full_url}
            return {"success": False, "error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def revoke_subscription(self, username: str) -> bool:
        """Отзывает (деактивирует) пользователя"""
        try:
            headers = await self._headers()
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.put(
                    f"{self.base_url}/api/user/{username}",
                    json={"status": "disabled"},
                    headers=headers
                )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Marzban revoke error: {e}")
            return False


marzban = MarzbanAPI()
