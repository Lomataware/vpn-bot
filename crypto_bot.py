import requests
import logging
from config import Config

logger = logging.getLogger(__name__)


class CryptoBotAPI:
    # CryptoBot имеет два API: тестовый и боевой
    MAINNET_URL = "https://pay.crypt.bot/api"
    TESTNET_URL = "https://testnet-pay.crypt.bot/api"

    def __init__(self):
        self.token = Config.CRYPTOBOT_TOKEN
        # Для начала используем testnet, потом переключимся на mainnet
        self.base_url = self.TESTNET_URL if Config.CRYPTOBOT_TESTNET else self.MAINNET_URL

    def _headers(self):
        return {"Crypto-Pay-API-Token": self.token}

    def create_invoice(self, amount: float, currency: str = "RUB", 
                       description: str = "", user_id: int = None) -> dict:
        """Создаёт инвойс для оплаты"""
        if not self.token:
            return {"success": False, "error": "CryptoBot token not configured"}

        payload = {
            "asset": "USDT",  # Оплата в USDT, сумма в RUB конвертируется
            "amount": str(amount),
            "description": description or "VPN subscription",
            "paid_btn_name": "callback",
            "paid_btn_url": "https://t.me/your_bot",  # Замени на своего бота
            "payload": str(user_id) if user_id else ""
        }

        try:
            response = requests.post(
                f"{self.base_url}/createInvoice",
                json=payload,
                headers=self._headers(),
                timeout=15
            )
            data = response.json()
            if data.get("ok"):
                invoice = data["result"]
                logger.info(f"CryptoBot: создан инвойс {invoice['invoice_id']}")
                return {
                    "success": True,
                    "invoice_id": invoice["invoice_id"],
                    "pay_url": invoice["pay_url"],
                    "amount": invoice["amount"],
                    "asset": invoice["asset"]
                }
            else:
                logger.error(f"CryptoBot: {data}")
                return {"success": False, "error": str(data)}
        except Exception as e:
            logger.error(f"CryptoBot error: {e}")
            return {"success": False, "error": str(e)}

    def get_invoice(self, invoice_id: int) -> dict:
        """Проверяет статус инвойса"""
        try:
            response = requests.get(
                f"{self.base_url}/getInvoices",
                params={"invoice_ids": invoice_id},
                headers=self._headers(),
                timeout=10
            )
            data = response.json()
            if data.get("ok") and data.get("result"):
                invoice = data["result"][0]
                return {
                    "success": True,
                    "status": invoice["status"],  # active, paid, expired
                    "invoice": invoice
                }
            return {"success": False, "error": "Invoice not found"}
        except Exception as e:
            return {"success": False, "error": str(e)}


crypto_bot = CryptoBotAPI()
