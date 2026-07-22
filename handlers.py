import logging
from datetime import datetime, timedelta
from urllib.parse import quote
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import Config
from database import (
    save_user, get_user_info, get_users_count, get_db_connection,
    save_payment, update_payment_status, get_payment, get_pending_payments,
    get_hy2_cred, upsert_hy2_cred, hy2_access_valid
)
from keyboards import get_main_menu, get_back_button, get_plans, get_payment_methods
from marzban_api import marzban
from crypto_bot import crypto_bot

logger = logging.getLogger(__name__)


def _row_get(row, key, default=None):
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


# ===== Hysteria2: конфиги только при валидном доступе =====
def build_hy2_block(user_id: int) -> str:
    """Возвращает блок Hysteria2-конфигов или '' если протокол не настроен
    ИЛИ у пользователя нет активной подписки."""
    servers = Config.get_hy2_servers()
    if not servers:
        return ""
    if not hy2_access_valid(user_id):
        return ""
    row = get_hy2_cred(user_id)
    if not row:
        return ""
    u = quote(row["username"], safe="")
    p = quote(row["password"], safe="")
    lines = ["🌀 <b>Hysteria2</b> (высокая скорость / обход блокировок):"]
    for s in servers:
        uri = (f"hysteria2://{u}:{p}@{s['host']}:{Config.HY2_PORT}"
               f"?sni={s['host']}&insecure=0#{s['label']} [Hysteria2]")
        lines.append(f"• <b>{s['label']}</b>\n<code>{uri}</code>")
    lines.append("\n💡 Вставьте в Hiddify / NekoBox / v2rayN.")
    return "\n".join(lines)


def _grant_hy2(user_id: int, days: int):
    """Привязывает Hysteria2-доступ к сроку подписки (если протокол настроен)."""
    if Config.get_hy2_servers():
        upsert_hy2_cred(user_id, datetime.now() + timedelta(days=days))


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


# ===== Админ: выдать подписку =====
async def issue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not Config.is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Использование: /issue <user_id> <days>\nПример: /issue 123456789 30"
        )
        return
    try:
        user_id = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ user_id и days должны быть числами")
        return

    username = f"tg_{user_id}_{int(datetime.now().timestamp())}"
    result = marzban.create_subscription(username, days)
    if result["success"]:
        with get_db_connection() as conn:
            conn.execute("UPDATE users SET marzban_username = ? WHERE id = ?", (username, user_id))
        _grant_hy2(user_id, days)
        try:
            hy2 = build_hy2_block(user_id)
            msg = (
                f"✅ <b>Подписка активирована!</b>\n\n"
                f"📅 Срок: <b>{days} дней</b>\n"
                f"🔗 <b>VLESS-ссылка:</b>\n<code>{result['subscription_url']}</code>\n\n"
            )
            if hy2:
                msg += hy2 + "\n\n"
            msg += "💡 Скопируйте ссылки и вставьте в Hiddify / V2RayNG / Streisand."
            await context.bot.send_message(chat_id=user_id, text=msg, parse_mode=ParseMode.HTML)
            await update.message.reply_text(f"✅ Подписка выдана {user_id} на {days} дн. ({username})")
        except Exception as e:
            await update.message.reply_text(
                f"⚠️ Подписка создана, но не отправлена пользователю: {e}\n"
                f"Username: {username}\nСсылка: {result['subscription_url']}"
            )
    else:
        await update.message.reply_text(f"❌ Ошибка: {result.get('error')}")


# ===== Админ: одобрить оплату картой =====
async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not Config.is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("❌ Использование: /approve <payment_id>")
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
    result = marzban.create_subscription(username, 30)
    if result["success"]:
        update_payment_status(payment_id, "approved", username)
        with get_db_connection() as conn:
            conn.execute("UPDATE users SET marzban_username = ? WHERE id = ?", (username, user_id))
        _grant_hy2(user_id, 30)
        try:
            hy2 = build_hy2_block(user_id)
            msg = (
                f"✅ <b>Оплата подтверждена!</b>\n\n"
                f"📅 Подписка на <b>1 месяц</b> активирована.\n"
                f"🔗 <b>VLESS-ссылка:</b>\n<code>{result['subscription_url']}</code>\n\n"
            )
            if hy2:
                msg += hy2 + "\n\n"
            msg += "💡 Скопируйте ссылки и вставьте в VPN-клиент."
            await context.bot.send_message(chat_id=user_id, text=msg, parse_mode=ParseMode.HTML)
            await update.message.reply_text(f"✅ Платёж #{payment_id} одобрен, подписка выдана {user_id}")
        except Exception as e:
            await update.message.reply_text(
                f"⚠️ Подписка создана, но пользователь не уведомлён: {e}\nСсылка: {result['subscription_url']}"
            )
    else:
        await update.message.reply_text(f"❌ Ошибка Marzban: {result.get('error')}")


# ===== Админ: ожидающие платежи =====
async def pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not Config.is_admin(update.effective_user.id):
        return
    with get_db_connection() as conn:
        payments = conn.execute('''
            SELECT p.id, p.user_id, p.plan, p.amount, p.method, p.created_at,
                   u.username, u.first_name
            FROM payments p LEFT JOIN users u ON p.user_id = u.id
            WHERE p.status = 'pending' ORDER BY p.created_at DESC
        ''').fetchall()
    if not payments:
        await update.message.reply_text("✅ Нет ожидающих платежей")
        return
    text = f"⏳ <b>Ожидающие платежи ({len(payments)}):</b>\n\n"
    for p in payments:
        user_info = f"@{p['username']}" if p['username'] else (p['first_name'] or p['user_id'])
        text += f"#{p['id']} | {user_info} | {p['amount']}₽ | {p['method']}\n   /approve {p['id']}\n\n"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ===== Админ: рассылка =====
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not Config.is_admin(update.effective_user.id):
        return
    message = " ".join(context.args) if context.args else ""
    if not message:
        await update.message.reply_text("❌ Использование: /broadcast текст сообщения")
        return
    with get_db_connection() as conn:
        users = conn.execute("SELECT id FROM users WHERE is_blocked = FALSE").fetchall()
    total = len(users)
    await update.message.reply_text(f"📢 Рассылка {total} пользователям...")
    sent, failed = 0, 0
    for row in users:
        try:
            await context.bot.send_message(chat_id=row[0], text=message)
            sent += 1
        except Exception as e:
            failed += 1
            if "blocked" in str(e).lower() or "deleted" in str(e).lower() or "403" in str(e):
                with get_db_connection() as conn:
                    conn.execute("UPDATE users SET is_blocked = TRUE WHERE id = ?", (row[0],))
            logger.error(f"Broadcast error {row[0]}: {e}")
    await update.message.reply_text(f"✅ Готово!\n📨 Отправлено: {sent}\n❌ Ошибок: {failed}")


# ===== Обработчик кнопок =====
async def handle_button
