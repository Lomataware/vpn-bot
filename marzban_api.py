import requests
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

    def _ensure_auth(self):
        if self.token and self.token_expires and datetime.now() < self.token_expires:
            return
        self._login()

    def _login(self):
        try:
            response = requests.post(
                f"{self.base_url}/api/admin/token",
                data={
                    "username": self.username,
                    "password": self.password,
                    "grant_type": "password"
                },
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                self.token = data["access_token"]
                # Токен живёт ~12 часов, обновляем за час до истечения
                self.token_expires = datetime.now() + timedelta(hours=11)
                logger.info("Marzban: успешно авторизован")
            else:
                logger.error(f"Marzban: ошибка авторизации {response.status_code}: {response.text}")
                raise Exception("Marzban auth failed")
        except Exception as e:
            logger.error(f"Marzban: ошибка соединения: {e}")
            raise

    def _headers(self):
        self._ensure_auth()
        return {"Authorization": f"Bearer {self.token}"}

    def create_subscription(self, username: str, days: int, data_limit_gb: int = None) -> dict:
        """
        Создаёт пользователя в Marzban и возвращает ссылку на подписку.
        """
        expire_timestamp = int((datetime.now() + timedelta(days=days)).timestamp())
        data_limit = (data_limit_gb or Config.DEFAULT_DATA_LIMIT_GB) * 1024 * 1024 * 1024

        inbounds = []
        if Config.MARZBAN_VLESS_INBOUND:
            inbounds.append(Config.MARZBAN_VLESS_INBOUND)
        if Config.MARZBAN_HYSTERIA_INBOUND:
            inbounds.append(Config.MARZBAN_HYSTERIA_INBOUND)

        payload = {
            "username": username,
            "status": "active",
            "expire": expire_timestamp,
            "data_limit": data_limit,
            "data_limit_reset_strategy": "no_reset",
            "inbounds": inbounds,
            "note": f"Created via Telegram bot at {datetime.now().isoformat()}"
        }

        try:
            response = requests.post(
                f"{self.base_url}/api/user",
                json=payload,
                headers=self._headers(),
                timeout=15
            )
            if response.status_code in (200, 201):
                user_data = response.json()
                subscription_url = user_data.get("subscription_url", "")
                full_url = f"{self.base_url}{subscription_url}" if subscription_url else ""
                logger.info(f"Marzban: создан пользователь {username}")
                return {
                    "success": True,
                    "username": username,
                    "subscription_url": full_url,
                    "expire": expire_timestamp,
                    "data_limit": data_limit
                }
            else:
                logger.error(f"Marzban: ошибка создания {response.status_code}: {response.text}")
                return {"success": False, "error": response.text}
        except Exception as e:
            logger.error(f"Marzban: ошибка: {e}")
            return {"success": False, "error": str(e)}

    def get_user(self, username: str) -> dict:
        try:
            response = requests.get(
                f"{self.base_url}/api/user/{username}",
                headers=self._headers(),
                timeout=10
            )
            if response.status_code == 200:
                return {"success": True, "data": response.json()}
            return {"success": False, "error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def revoke_subscription(self, username: str) -> bool:
        """Отзывает (деактивирует) пользователя"""
        try:
            response = requests.put(
                f"{self.base_url}/api/user/{username}",
                json={"status": "disabled"},
                headers=self._headers(),
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Marzban revoke error: {e}")
            return False


marzban = MarzbanAPI()
