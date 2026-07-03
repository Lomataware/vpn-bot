import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Bot
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))

    # Support
    SUPPORT_USERNAME = os.getenv('SUPPORT_USERNAME', 'support')
    SUPPORT_PHONE = os.getenv('SUPPORT_PHONE', '')
    BANK_NAME = os.getenv('BANK_NAME', 'Банк')

    # MTProto
    MTPROTO_SERVER = os.getenv('MTPROTO_SERVER', '')
    MTPROTO_PORT = os.getenv('MTPROTO_PORT', '10904')
    MTPROTO_SECRET = os.getenv('MTPROTO_SECRET', '')
    MTPROTO_LINK = f"tg://proxy?server={MTPROTO_SERVER}&port={MTPROTO_PORT}&secret={MTPROTO_SECRET}"

    # CryptoBot
    CRYPTOBOT_TOKEN = os.getenv('CRYPTOBOT_TOKEN', '')
    CRYPTOBOT_ENABLED = os.getenv('CRYPTOBOT_ENABLED', 'false').lower() == 'true'

    # Prices
    PRICE_1_MONTH = int(os.getenv('PRICE_1_MONTH', '100'))
    PRICE_3_MONTHS = int(os.getenv('PRICE_3_MONTHS', '250'))
    PRICE_12_MONTHS = int(os.getenv('PRICE_12_MONTHS', '800'))