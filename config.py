import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ===== BOT =====
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))

    # ===== SUPPORT (все личные данные в .env!) =====
    SUPPORT_USERNAME = os.getenv('SUPPORT_USERNAME', 'support')
    SUPPORT_PHONE = os.getenv('SUPPORT_PHONE', '')
    BANK_NAME = os.getenv('BANK_NAME', 'Банк')

    # ===== MARZBAN =====
    MARZBAN_URL = os.getenv('MARZBAN_URL', '')
    MARZBAN_ADMIN_USERNAME = os.getenv('MARZBAN_ADMIN_USERNAME', 'admin')
    MARZBAN_ADMIN_PASSWORD = os.getenv('MARZBAN_ADMIN_PASSWORD', '')
    MARZBAN_VLESS_INBOUND = os.getenv('MARZBAN_VLESS_INBOUND', '')
    MARZBAN_HYSTERIA_INBOUND = os.getenv('MARZBAN_HYSTERIA_INBOUND', '')
    DEFAULT_DATA_LIMIT_GB = int(os.getenv('DEFAULT_DATA_LIMIT_GB', '100'))

    # ===== CRYPTOBOT =====
    CRYPTOBOT_TOKEN = os.getenv('CRYPTOBOT_TOKEN', '')
    CRYPTOBOT_ENABLED = os.getenv('CRYPTOBOT_ENABLED', 'false').lower() == 'true'
    CRYPTOBOT_TESTNET = os.getenv('CRYPTOBOT_TESTNET', 'true').lower() == 'true'

    # ===== PRICES =====
    PRICE_1_MONTH = int(os.getenv('PRICE_1_MONTH', '100'))

    # ===== ADMIN COMMANDS =====
    # Дополнительные админы через запятую (опционально)
    ADDITIONAL_ADMINS = [
        int(x.strip()) for x in os.getenv('ADDITIONAL_ADMINS', '').split(',') 
        if x.strip().isdigit()
    ]

    @classmethod
    def is_admin(cls, user_id: int) -> bool:
        return user_id == cls.ADMIN_ID or user_id in cls.ADDITIONAL_ADMINS
