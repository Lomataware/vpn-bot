import logging
from datetime import datetime
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import Config
from database import save_user, get_user_info, get_users_count
from keyboards import get_main_menu, get_back_button, get_payment_methods, get_plans

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await save_user(update.effective_user)
    user = update.effective_user
    text = (
        f"👋 Привет, {user.first_name}!\n\n"
        f"Добро пожаловать в VPN-сервис.\n"
        f"Выберите нужное действие:"
    )
    await update.message.reply_text(text, reply_markup=get_main_menu())


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user

    if data == "main_menu":
        await query.edit_message_text("Главное меню:", reply_markup=get_main_menu())

    elif data == "mtproto":
        text = (
            "🔌 <b>MTProto прокси для Telegram</b>\n\n"
            "MTProto — это встроенный в Telegram протокол, который позволяет обходить "
            "замедление и блокировки <b>только внутри самого Telegram</b>.\n\n"
            "⚠️ <b>Важно:</b> этот прокси НЕ даёт полноценный VPN для браузера и других приложений. "
            "Для этого приобретите подписку.\n\n"
            "📲 <b>Способ 1: Автоматическое подключение</b>\n"
            "Нажмите на ссылку ниже прямо в Telegram — прокси добавится автоматически:\n"
            f"👉 <a href='{Config.MTPROTO_LINK}'>Подключить прокси</a>\n\n"
            "🔧 <b>Способ 2: Ручной ввод</b>\n"
            "Если ссылка не сработала, введите данные вручную:\n"
            "<i>Настройки → Данные и хранилище → Использование хранилища → Настройки прокси</i>\n\n"
            f"• <b>Сервер:</b> <code>{Config.MTPROTO_SERVER}</code>\n"
            f"• <b>Порт:</b> <code>{Config.MTPROTO_PORT}</code>\n"
            f"• <b>Секрет:</b> <code>{Config.MTPROTO_SECRET}</code>\n\n"
            "💡 <b>Совет:</b> для лучшего обхода блокировок используйте прокси вместе с "
            "программами Byedpi, ByeByeDPI или Zapret."
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())

    elif data == "buy_subscription":
        text = (
            "💳 <b>Покупка подписки</b>\n\n"
            "Подписка даёт полноценный VPN-доступ для всех ваших устройств: "
            "смартфона, компьютера, планшета. Работает с любыми приложениями и сайтами.\n\n"
            "Выберите тариф:"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_plans())

    elif data in ["plan_1", "plan_3", "plan_12"]:
        plans = {
            "plan_1": ("1 месяц", Config.PRICE_1_MONTH),
            "plan_3": ("3 месяца", Config.PRICE_3_MONTHS),
            "plan_12": ("12 месяцев", Config.PRICE_12_MONTHS),
        }
        plan_name, price = plans[data]
        context.user_data['selected_plan'] = {"name": plan_name, "price": price, "code": data}
        text = (
            f"✅ Вы выбрали тариф: <b>{plan_name}</b>\n"
            f"💰 Стоимость: <b>{price}₽</b>\n\n"
            "Выберите удобный способ оплаты:"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_payment_methods())

    elif data == "pay_manual":
        plan = context.user_data.get('selected_plan', {})
        text = (
            f"💳 <b>Оплата на карту</b>\n\n"
            f"📦 Тариф: <b>{plan.get('name', '')}</b>\n"
            f"💰 К оплате: <b>{plan.get('price', '')}₽</b>\n\n"
            f"📱 <b>Переведите точную сумму</b> по реквизитам:\n"
            f"• <b>Банк:</b> {Config.BANK_NAME}\n"
            f"• <b>Телефон:</b> <code>{Config.SUPPORT_PHONE}</code>\n\n"
            f"📩 После оплаты напишите в поддержку: @{Config.SUPPORT_USERNAME}\n"
            f"Приложите скриншот платежа и укажите ваш Telegram ID: <code>{user.id}</code>\n\n"
            f"⏱ Обычно подтверждение занимает 5–30 минут."
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button("main_menu"))

    elif data == "pay_crypto":
        plan = context.user_data.get('selected_plan', {})
        text = (
            f"₿ <b>Оплата криптовалютой</b>\n\n"
            f"📦 Тариф: <b>{plan.get('name', '')}</b>\n"
            f"💰 К оплате: <b>{plan.get('price', '')}₽</b>\n\n"
            f"🔗 Автоматическая выдача подписки после оплаты.\n"
            f"<i>(Функция в разработке — скоро будет доступна)</i>"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button("main_menu"))

    elif data == "my_account":
        info = get_user_info(user.id)
        created = info['created_at'] if info else "Неизвестно"
        text = (
            f"👤 <b>Ваш профиль</b>\n\n"
            f"🆔 ID: <code>{user.id}</code>\n"
            f"👤 Имя: {user.full_name}\n"
            f"📱 Username: {'@' + user.username if user.username else '—'}\n"
            f"📅 С нами с: {str(created)[:10]}\n\n"
            f"📌 <b>Активная подписка:</b> Нет\n"
            f"📊 <b>Остаток трафика:</b> —"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())

    elif data == "terms":
        text = (
            "📋 <b>Пользовательское соглашение</b>\n\n"
            "<b>1. Общие положения</b>\n"
            "1.1. Настоящее соглашение регулирует отношения между администратором сервиса "
            "(далее — «Исполнитель») и пользователем (далее — «Заказчик»).\n"
            "1.2. Используя сервис, вы подтверждаете полное согласие с условиями данного соглашения.\n\n"
            "<b>2. Предмет соглашения</b>\n"
            "2.1. Исполнитель предоставляет Заказчику доступ к VPN-сервису на условиях подписки.\n"
            "2.2. Сервис предоставляется по принципу «как есть» (as is) без каких-либо гарантий.\n\n"
            "<b>3. Ответственность сторон</b>\n"
            "3.1. Заказчик самостоятельно несёт ответственность за соблюдение законодательства "
            "своей страны при использовании сервиса.\n"
            "3.2. Исполнитель не несёт ответственности за:\n"
            "• Возможные перебои в работе сервиса;\n"
            "• Скорость соединения и качество связи;\n"
            "• Блокировки со стороны провайдеров;\n"
            "• Действия третьих лиц.\n\n"
            "<b>4. Оплата и возврат средств</b>\n"
            "4.1. Оплата производится авансом за выбранный период подписки.\n"
            "4.2. Возврат средств возможен в течение 24 часов с момента оплаты при условии, "
            "что услугой не пользовались.\n\n"
            "<b>5. Запрещённое использование</b>\n"
            "5.1. Запрещается использовать сервис для:\n"
            "• Распространения вредоносного ПО;\n"
            "• Несанкционированного доступа к чужим системам;\n"
            "• Нарушения авторских прав;\n"
            "• Иной противоправной деятельности.\n\n"
            "<b>6. Заключительные положения</b>\n"
            "6.1. Исполнитель оставляет за собой право изменять условия соглашения в одностороннем порядке.\n"
            "6.2. Все споры решаются путём переговоров, а при недостижении согласия — "
            "в соответствии с законодательством РФ."
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())

    elif data == "help":
        text = (
            "❓ <b>Помощь и поддержка</b>\n\n"
            f"📞 <b>Связь с поддержкой:</b> @{Config.SUPPORT_USERNAME}\n\n"
            "<b>Частые вопросы:</b>\n\n"
            "🔹 <i>Прокси не работает?</i>\n"
            "Убедитесь, что включили его в настройках Telegram. Попробуйте перезапустить приложение.\n\n"
            "🔹 <i>Низкая скорость?</i>\n"
            "Это может быть связано с нагрузкой на сервер или вашим провайдером. "
            "Для стабильной работы рекомендуем приобрести VPN-подписку.\n\n"
            "🔹 <i>Как оплатить?</i>\n"
            "Нажмите «Купить подписку» и выберите удобный способ оплаты.\n\n"
            "🔹 <i>Бот не отвечает?</i>\n"
            "Напишите в поддержку, мы ответим в течение 24 часов."
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await save_user(update.effective_user)
    text = update.message.text.lower()
    if "привет" in text or "hi" in text:
        await update.message.reply_text("Привет! 👋 Используйте меню:", reply_markup=get_main_menu())


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != Config.ADMIN_ID:
        return
    count = get_users_count()
    await update.message.reply_text(f"📊 Всего пользователей: {count}")


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != Config.ADMIN_ID:
        await update.message.reply_text("❌ Нет доступа")
        return
    message = " ".join(context.args) if context.args else ""
    if not message:
        await update.message.reply_text("❌ Использование: /broadcast текст сообщения")
        return
    await update.message.reply_text(f"📢 Начинаю рассылку...")
    # Логика рассылки...
    await update.message.reply_text("✅ Рассылка завершена!")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception: {context.error}", exc_info=context.error)
