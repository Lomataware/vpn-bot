import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import Config
from database import (
    save_user, get_user_info, get_users_count, get_db_connection,
    save_payment, update_payment_status, get_payment, get_pending_payments
)
from keyboards import get_main_menu, get_back_button, get_plans, get_payment_methods
from marzban_api import marzban
from crypto_bot import crypto_bot

logger = logging.getLogger(__name__)


# ===== /start =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    user = update.effective_user
    text = (
        f"👋 Привет, {user.first_name}!\n\n"
        f"Добро пожаловать в VPN-сервис.\n"
        f"Выберите нужное действие:"
    )
    await update.message.reply_text(text, reply_markup=get_main_menu())


# ===== Админ-команда: выдать подписку =====
async def issue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Использование: /issue <user_id> <days>
    Пример: /issue 123456789 30
    """
    if not Config.is_admin(update.effective_user.id):
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Использование: /issue <user_id> <days>\n"
            "Пример: /issue 123456789 30"
        )
        return

    try:
        user_id = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ user_id и days должны быть числами")
        return

    # Генерируем уникальное имя для Marzban
    username = f"tg_{user_id}_{int(datetime.now().timestamp())}"

    result = marzban.create_subscription(username, days)
    if result["success"]:
        # Обновляем пользователя в БД
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE users SET marzban_username = ? WHERE id = ?",
                (username, user_id)
            )

        # Отправляем пользователю
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"✅ <b>Подписка активирована!</b>\n\n"
                    f"📅 Срок: <b>{days} дней</b>\n"
                    f"🔗 <b>Ссылка для подключения:</b>\n"
                    f"<code>{result['subscription_url']}</code>\n\n"
                    f"💡 Скопируйте ссылку и вставьте в ваш VPN-клиент "
                    f"(V2RayNG, Hiddify, Streisand и др.)"
                ),
                parse_mode=ParseMode.HTML
            )
            await update.message.reply_text(
                f"✅ Подписка выдана пользователю {user_id} на {days} дней\n"
                f"Username: {username}"
            )
        except Exception as e:
            await update.message.reply_text(
                f"⚠️ Подписка создана, но не удалось отправить пользователю: {e}\n"
                f"Username: {username}\n"
                f"Ссылка: {result['subscription_url']}"
            )
    else:
        await update.message.reply_text(f"❌ Ошибка: {result.get('error')}")


# ===== Админ-команда: одобрить оплату на карту =====
async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Использование: /approve <payment_id>
    Пример: /approve 42
    """
    if not Config.is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Использование: /approve <payment_id>\n"
            "Пример: /approve 42"
        )
        return

    try:
        payment_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ payment_id должен быть числом")
        return

    payment = get_payment(payment_id)
    if not payment:
        await update.message.reply_text("❌ Платёж не найден")
        return

    if payment['status'] != 'pending':
        await update.message.reply_text(f"⚠️ Платёж уже в статусе: {payment['status']}")
        return

    user_id = payment['user_id']
    username = f"tg_{user_id}_{int(datetime.now().timestamp())}"

    result = marzban.create_subscription(username, 30)  # 1 месяц
    if result["success"]:
        update_payment_status(payment_id, "approved", username)
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE users SET marzban_username = ? WHERE id = ?",
                (username, user_id)
            )

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"✅ <b>Оплата подтверждена!</b>\n\n"
                    f"📅 Подписка на <b>1 месяц</b> активирована.\n"
                    f"🔗 <b>Ссылка для подключения:</b>\n"
                    f"<code>{result['subscription_url']}</code>\n\n"
                    f"💡 Скопируйте ссылку и вставьте в ваш VPN-клиент."
                ),
                parse_mode=ParseMode.HTML
            )
            await update.message.reply_text(
                f"✅ Платёж #{payment_id} одобрен, подписка выдана пользователю {user_id}"
            )
        except Exception as e:
            await update.message.reply_text(
                f"⚠️ Подписка создана, но не удалось уведомить пользователя: {e}\n"
                f"Ссылка: {result['subscription_url']}"
            )
    else:
        await update.message.reply_text(f"❌ Ошибка Marzban: {result.get('error')}")


# ===== Админ-команда: список ожидающих платежей =====
async def pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает все ожидающие подтверждения платежи"""
    if not Config.is_admin(update.effective_user.id):
        return

    with get_db_connection() as conn:
        payments = conn.execute('''
            SELECT p.id, p.user_id, p.plan, p.amount, p.method, p.created_at,
                   u.username, u.first_name
            FROM payments p
            LEFT JOIN users u ON p.user_id = u.id
            WHERE p.status = 'pending'
            ORDER BY p.created_at DESC
        ''').fetchall()

    if not payments:
        await update.message.reply_text("✅ Нет ожидающих платежей")
        return

    text = f"⏳ <b>Ожидающие платежи ({len(payments)}):</b>\n\n"
    for p in payments:
        user_info = f"@{p['username']}" if p['username'] else p['first_name']
        text += (
            f"#{p['id']} | {user_info} | {p['amount']}₽ | {p['method']}\n"
            f"   /approve {p['id']}\n\n"
        )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ===== Обработчик кнопок =====
async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user

    if data == "main_menu":
        await query.edit_message_text("Главное меню:", reply_markup=get_main_menu())

    elif data == "buy_subscription":
        text = (
            "💳 <b>Покупка подписки</b>\n\n"
            "Подписка даёт полноценный VPN-доступ для всех ваших устройств.\n"
            "Поддерживаемые приложения: Hiddify(телефоны), V2rayN(пк) ТОЛЬКО ЭТИ!!!.\n"
            "Поддерживаемые протоколы: VLESS, Hysteria2(В будующем).\n\n"
            "Выберите тариф:"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_plans())

    elif data == "plan_1":
        plan_name = "1 месяц"
        price = Config.PRICE_1_MONTH
        context.user_data['selected_plan'] = {"name": plan_name, "price": price}
        text = (
            f"✅ Тариф: <b>{plan_name}</b>\n"
            f"💰 Стоимость: <b>{price}₽</b>\n\n"
            "Выберите способ оплаты:"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_payment_methods())

    elif data == "pay_manual":
        plan = context.user_data.get('selected_plan', {})
        # Сохраняем платёж в БД
        payment_id = save_payment(user.id, plan.get('name'), plan.get('price'), "card")
        text = (
            f"💳 <b>Оплата на карту</b>\n\n"
            f"📦 Тариф: <b>{plan.get('name')}</b>\n"
            f"💰 К оплате: <b>{plan.get('price')}₽</b>\n"
            f"🔢 Номер платежа: <code>#{payment_id}</code>\n\n"
            f"📱 <b>Реквизиты для перевода:</b>\n"
            f"• <b>Банк:</b> {Config.BANK_NAME}\n"
            f"• <b>Телефон:</b> <code>{Config.SUPPORT_PHONE}</code>\n\n"
            f"📩 После оплаты напишите @{Config.SUPPORT_USERNAME}:\n"
            f"• Приложите скриншот платежа\n"
            f"• Укажите номер платежа: <code>#{payment_id}</code>\n\n"
            f"⏱ Подтверждение занимает 5–30 минут."
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button("main_menu"))

    elif data == "pay_crypto":
        plan = context.user_data.get('selected_plan', {})
        # Создаём инвойс в CryptoBot
        result = crypto_bot.create_invoice(
            amount=plan.get('price'),
            description=f"VPN {plan.get('name')}",
            user_id=user.id
        )
        if result["success"]:
            payment_id = save_payment(
                user.id, plan.get('name'), plan.get('price'), 
                "crypto", "pending", str(result["invoice_id"])
            )
            context.user_data['active_invoice'] = {
                "invoice_id": result["invoice_id"],
                "payment_id": payment_id
            }
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Оплатить", url=result["pay_url"])],
                [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_{result['invoice_id']}")],
                [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")]
            ])
            text = (
                f"₿ <b>Оплата криптовалютой</b>\n\n"
                f"📦 Тариф: <b>{plan.get('name')}</b>\n"
                f"💰 К оплате: <b>{result['amount']} {result['asset']}</b>\n"
                f"🔢 Платёж: <code>#{payment_id}</code>\n\n"
                f"Нажмите кнопку ниже для оплаты. После оплаты нажмите «Проверить оплату»."
            )
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        else:
            text = (
                f"₿ <b>Оплата криптовалютой</b>\n\n"
                f"<i>(Функция временно недоступна)</i>\n\n"
                f"Используйте оплату на карту."
            )
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button("main_menu"))

    elif data.startswith("check_"):
        invoice_id = int(data.split("_")[1])
        invoice = context.user_data.get('active_invoice', {})
        if not invoice or invoice.get("invoice_id") != invoice_id:
            await query.answer("Инвойс не найден", show_alert=True)
            return

        result = crypto_bot.get_invoice(invoice_id)
        if result["success"]:
            if result["status"] == "paid":
                payment_id = invoice["payment_id"]
                username = f"tg_{user.id}_{int(datetime.now().timestamp())}"
                marzban_result = marzban.create_subscription(username, 30)
                if marzban_result["success"]:
                    update_payment_status(payment_id, "paid", username)
                    with get_db_connection() as conn:
                        conn.execute(
                            "UPDATE users SET marzban_username = ? WHERE id = ?",
                            (username, user.id)
                        )
                    text = (
                        f"✅ <b>Оплата прошла!</b>\n\n"
                        f"🔗 <b>Ваша ссылка для подключения:</b>\n"
                        f"<code>{marzban_result['subscription_url']}</code>\n\n"
                        f"💡 Скопируйте ссылку и вставьте в ваш VPN-клиент."
                    )
                    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button("main_menu"))
                else:
                    await query.edit_message_text(f"❌ Ошибка создания подписки: {marzban_result.get('error')}")
            elif result["status"] == "active":
                await query.answer("Оплата ещё не получена", show_alert=True)
            else:
                await query.answer(f"Статус: {result['status']}", show_alert=True)
        else:
            await query.answer("Ошибка проверки", show_alert=True)

    elif data == "my_account":
        info = get_user_info(user.id)
        created = info['created_at'] if info else "Неизвестно"
        marzban_username = info['marzban_username'] if info and 'marzban_username' in info.keys() else None
        
        text = (
            f"👤 <b>Ваш профиль</b>\n\n"
            f"🆔 ID: <code>{user.id}</code>\n"
            f"👤 Имя: {user.full_name}\n"
            f"📱 Username: {'@' + user.username if user.username else '—'}\n"
            f"📅 С нами с: {str(created)[:10]}\n\n"
        )

        if marzban_username:
            user_data = marzban.get_user(marzban_username)
            if user_data["success"]:
                data = user_data["data"]
                expire = datetime.fromtimestamp(data['expire']).strftime("%d.%m.%Y") if data.get('expire') else "—"
                used = (data.get('used_traffic', 0) / 1024 / 1024 / 1024)
                limit = (data.get('data_limit', 0) / 1024 / 1024 / 1024)
                text += (
                    f"📌 <b>Подписка:</b> активна\n"
                    f"📅 Действует до: {expire}\n"
                    f"📊 Трафик: {used:.1f} / {limit:.1f} ГБ\n\n"
                    f"🔗 <b>Ссылка:</b>\n"
                    f"<code>{Config.MARZBAN_URL}/sub/{marzban_username}</code>"
                )
            else:
                text += "📌 <b>Подписка:</b> нет активной"
        else:
            text += "📌 <b>Подписка:</b> нет активной"

        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())

    elif data == "terms":
        text = (
            "📋 <b>Пользовательское соглашение</b>\n\n"
            "1. Сервис предоставляется «как есть» без гарантий.\n"
            "2. Вы несёте ответственность за соблюдение законодательства вашей страны.\n"
            "3. Запрещено использовать сервис для противоправной деятельности.\n"
            "4. Возврат возможен в течение 24 часов при условии, что услугой не пользовались.\n"
            "5. Администратор вправе изменить условия в одностороннем порядке.\n\n"
            "Используя сервис, вы принимаете эти условия."
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())

    elif data == "help":
        text = (
            "❓ <b>Помощь</b>\n\n"
            f"📞 <b>Поддержка:</b> @{Config.SUPPORT_USERNAME}\n\n"
            "<b>Как подключиться:</b>\n"
            "1. Купите подписку или получите у админа\n"
            "2. Скопируйте ссылку\n"
            "3. Вставьте в приложение: V2RayNG (Android), "
            "Streisand (iOS), Hiddify (все платформы)\n\n"
            "<b>Проблемы?</b> Напишите в поддержку."
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    text = update.message.text.lower()
    if "привет" in text or "hi" in text:
        await update.message.reply_text("Привет! 👋", reply_markup=get_main_menu())


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not Config.is_admin(update.effective_user.id):
        return
    with get_db_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM users WHERE date(last_active) = date('now')"
        ).fetchone()[0]
        payments_pending = conn.execute(
            "SELECT COUNT(*) FROM payments WHERE status = 'pending'"
        ).fetchone()[0]
        payments_approved = conn.execute(
            "SELECT COUNT(*) FROM payments WHERE status IN ('approved', 'paid')"
        ).fetchone()[0]

    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Всего пользователей: {total}\n"
        f"🟢 Активных сегодня: {active}\n\n"
        f"💳 Платежей в ожидании: {payments_pending}\n"
        f"✅ Оплачено: {payments_approved}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception: {context.error}", exc_info=context.error)
