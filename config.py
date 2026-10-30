import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ===== BOT =====
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))

    # ===== SUPPORT =====
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

    # ===== TRIAL & PRICES =====
    TRIAL_DAYS = int(os.getenv('TRIAL_DAYS', '3'))
    PRICE_1_MONTH = int(os.getenv('PRICE_1_MONTH', '100'))

    # ===== ADMIN COMMANDS =====
    ADDITIONAL_ADMINS = [
        int(x.strip()) for x in os.getenv('ADDITIONAL_ADMINS', '').split(',')
        if x.strip().isdigit()
    ]

    @classmethod
    def is_admin(cls, user_id: int) -> bool:
        return user_id == cls.ADMIN_ID or user_id in cls.ADDITIONAL_ADMINS

    # ===== Hysteria2 =====
    HY2_PORT = int(os.getenv("HY2_PORT", "443"))
    HY2_AUTH_TOKEN = os.getenv("HY2_AUTH_TOKEN", "")
    HY2_AUTH_HOST = os.getenv("HY2_AUTH_HOST", "0.0.0.0")
    HY2_AUTH_PORT = int(os.getenv("HY2_AUTH_PORT", "9443"))
    HY2_AUTH_CERTFILE = os.getenv("HY2_AUTH_CERTFILE", "")
    HY2_AUTH_KEYFILE = os.getenv("HY2_AUTH_KEYFILE", "")
    _HY2_SERVERS_RAW = os.getenv("HY2_SERVERS", "")

    @classmethod
    def get_hy2_servers(cls):
        """Парсит HY2_SERVERS='host|label,host|label' в список dict.
        Пусто -> [] (тогда Hysteria2 в боте не показывается)."""
        result = []
        for item in cls._HY2_SERVERS_RAW.split(","):
            item = item.strip()
            if not item:
                continue
            if "|" in item:
                host, label = item.split("|", 1)
            else:
                host, label = item, item
            host = host.strip()
            if host:
                result.append({"host": host, "label": label.strip()})
        return result
