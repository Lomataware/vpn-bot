from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import Config


def get_main_menu():
    buttons = [
        [InlineKeyboardButton("🔌 MTProto прокси (бесплатно)", callback_data="mtproto")],
        [InlineKeyboardButton("💳 Купить подписку", callback_data="buy_subscription")],
        [InlineKeyboardButton("👤 Мой аккаунт", callback_data="my_account")],
        [InlineKeyboardButton("📄 Пользовательское соглашение", callback_data="terms")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")],
    ]
    return InlineKeyboardMarkup(buttons)


def get_back_button(target="main_menu"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад в меню", callback_data=target)]
    ])


def get_payment_methods():
    buttons = [
        [InlineKeyboardButton(" Оплата на карту (вручную)", callback_data="pay_manual")],
    ]
    if Config.CRYPTOBOT_ENABLED:
        buttons.insert(0, [InlineKeyboardButton("₿ Криптовалюта (автоматически)", callback_data="pay_crypto")])
    buttons.append([InlineKeyboardButton("️ Назад", callback_data="buy_subscription")])  # ← Исправлено: назад к тарифам
    return InlineKeyboardMarkup(buttons)


def get_plans():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"1 месяц — {Config.PRICE_1_MONTH}₽", callback_data="plan_1")],
        # Когда добавишь другие тарифы — раскомментируй:
        # [InlineKeyboardButton(f"3 месяца — {Config.PRICE_3_MONTHS}₽", callback_data="plan_3")],
        # [InlineKeyboardButton(f"12 месяцев — {Config.PRICE_12_MONTHS}₽", callback_data="plan_12")],
        [InlineKeyboardButton("◀️ Назад", callback_data="buy_subscription")]  # ← Исправлено
    ])
