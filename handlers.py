import asyncio
import logging
from datetime import datetime, timedelta
from urllib.parse import quote
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import Config
from database import (
    save_user, get_user_info, set_user_marzban, has_used_trial, mark_trial_used,
    get_db_connection, save_payment, update_payment_status, get_payment,
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


def _menu_for_user(user_id: int):
    return get_main_menu(show_trial=not has_used_trial(user_id))


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


def _sync_hy2_expire(user_id: int, expire_ts: int):
    """Синхронизирует срок Hysteria2 с timestamp окончания подписки в Marzban."""
    if Config.get_hy2_servers() and expire_ts:
        upsert_hy2_cred(user_id, datetime.fromtimestamp(expire_ts))


async def _activate_user_sub(user_id: int, days: int) -> dict:
    """Создаёт или продлевает подписку пользователя в Marzban и Hysteria2."""
    info = get_user_info(user_id)
    existing_mu = _row_get(info, "marzban_username")
    result = await marzban.create_or_extend_subscription(
        user_id=user_id,
        days=days,
        existing_username=existing_mu
    )
    if result["success"]:
        set_user_marzban(user_id, result["username"])
        _sync_hy2_expire(user_id, result["expire"])
    return result


# ===== /start =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    user = update.effective_user
    text = (
        f"👋 Привет, {user.first_name}!\n\n"
        f"Добро пожаловать в VPN-сервис.\n"
        f"Выберите нужное действие:"
    )
    await update.message.reply_text(text, reply_markup=_menu_for_user(user.id))


# ===== Админ: выдать/продлить подписку =====
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

    result = await _activate_user_sub(user_id, days)
    if result["success"]:
        action_word = "продлена" if result.get("extended") else "активирована"
        expire_str = datetime.fromtimestamp(result["expire"]).strftime("%d.%m.%Y %H:%M")
        try:
            hy2 = build_hy2_block(user_id)
            msg = (
                f"✅ <b>Подписка {action_word}!</b>\n\n"
                f"📅 Добавлено: <b>{days} дн.</b> (до <b>{expire_str}</b>)\n"
                f"🔗 <b>VLESS-ссылка:</b>\n<code>{result['subscription_url']}</code>\n\n"
            )
            if hy2:
                msg += hy2 + "\n\n"
            msg += "💡 Скопируйте ссылки и вставьте в Hiddify / V2RayNG / Streisand."
            await context.bot.send_message(chat_id=user_id, text=msg, parse_mode=ParseMode.HTML)
            await update.message.reply_text(
                f"✅ Подписка {action_word} для {user_id} на {days} дн. ({result['username']})"
            )
        except Exception as e:
            await update.message.reply_text(
                f"⚠️ Подписка {action_word}, но не отправлена пользователю: {e}\n"
                f"Username: {result['username']}\nСсылка: {result['subscription_url']}"
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
    result = await _activate_user_sub(user_id, 30)
    if result["success"]:
        if not update_payment_status(payment_id, "approved", result["username"]):
            await update.message.reply_text("⚠️ Платёж уже был обработан ранее.")
            return
        expire_str = datetime.fromtimestamp(result["expire"]).strftime("%d.%m.%Y")
        try:
            hy2 = build_hy2_block(user_id)
            msg = (
                f"✅ <b>Оплата подтверждена!</b>\n\n"
                f"📅 Подписка активна до: <b>{expire_str}</b>\n"
                f"🔗 <b>VLESS-ссылка:</b>\n<code>{result['subscription_url']}</code>\n\n"
            )
            if hy2:
                msg += hy2 + "\n\n"
            msg += "💡 Если вы уже настраивали подключение ранее, менять ссылку в приложении не нужно."
            await context.bot.send_message(chat_id=user_id, text=msg, parse_mode=ParseMode.HTML)
            await update.message.reply_text(f"✅ Платёж #{payment_id} одобрен, подписка выдана {user_id}")
        except Exception as e:
            await update.message.reply_text(
                f"⚠️ Подписка выдана, но пользователь не уведомлён: {e}\nСсылка: {result['subscription_url']}"
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


# ===== Админ: рассылка (с защитой от FloodWait) =====
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
            await asyncio.sleep(0.05)  # Защита от лимита 30 сообщений/сек в Telegram
        except Exception as e:
            failed += 1
            if "blocked" in str(e).lower() or "deleted" in str(e).lower() or "403" in str(e):
                with get_db_connection() as conn:
                    conn.execute("UPDATE users SET is_blocked = TRUE WHERE id = ?", (row[0],))
            logger.error(f"Broadcast error {row[0]}: {e}")
    await update.message.reply_text(f"✅ Готово!\n📨 Отправлено: {sent}\n❌ Ошибок: {failed}")


# ===== Обработчик кнопок =====
async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = update.effective_user
    save_user(user)

    try:
        if data == "main_menu":
            await query.answer()
            await query.edit_message_text("Главное меню:", reply_markup=_menu_for_user(user.id))

        elif data == "free_trial":
            if has_used_trial(user.id):
                await query.answer("Вы уже использовали пробный период!", show_alert=True)
                await query.edit_message_text("Главное меню:", reply_markup=_menu_for_user(user.id))
                return

            await query.answer("Активируем пробный период...")
            result = await _activate_user_sub(user.id, Config.TRIAL_DAYS)
            if result["success"]:
                mark_trial_used(user.id)
                expire_str = datetime.fromtimestamp(result["expire"]).strftime("%d.%m.%Y %H:%M")
                hy2 = build_hy2_block(user.id)
                text = (
                    f"🎁 <b>Пробный период на {Config.TRIAL_DAYS} дня активирован!</b>\n\n"
                    f"📅 Действует до: <b>{expire_str}</b>\n"
                    f"🔗 <b>VLESS-ссылка:</b>\n<code>{result['subscription_url']}</code>\n\n"
                )
                if hy2:
                    text += hy2 + "\n\n"
                text += (
                    "💡 Скопируйте ссылку и вставьте в Hiddify / V2RayNG / Streisand.\n"
                    "При последующей покупке подписки ссылка останется прежней!"
                )
                await query.edit_message_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_back_button("main_menu")
                )
            else:
                await query.edit_message_text(
                    "❌ Не удалось активировать пробный период. Попробуйте позже или напишите в поддержку.",
                    reply_markup=get_back_button("main_menu")
                )

        elif data == "buy_subscription":
            await query.answer()
            text = (
                "💳 <b>Покупка подписки</b>\n\n"
                "Подписка даёт полноценный VPN-доступ для всех ваших устройств.\n"
                "Протоколы: VLESS (REALITY) + Hysteria2.\n"
                "Если у вас уже есть подписка или пробный период, срок просто продлится без смены настроек!\n\n"
                "Выберите тариф:"
            )
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_plans())

        elif data == "plan_1":
            await query.answer()
            context.user_data['selected_plan'] = {"name": "1 месяц", "price": Config.PRICE_1_MONTH, "days": 30}
            text = (
                f"✅ Тариф: <b>1 месяц</b>\n💰 Стоимость: <b>{Config.PRICE_1_MONTH}₽</b>\n\n"
                f"Выберите способ оплаты:"
            )
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_payment_methods())

        elif data == "pay_manual":
            await query.answer()
            plan = context.user_data.get('selected_plan', {"name": "1 месяц", "price": Config.PRICE_1_MONTH})
            payment_id = save_payment(user.id, plan.get('name'), plan.get('price'), "card")
            text = (
                f"💳 <b>Оплата на карту</b>\n\n"
                f"📦 Тариф: <b>{plan.get('name')}</b>\n"
                f"💰 К оплате: <b>{plan.get('price')}₽</b>\n"
                f"🔢 Номер платежа: <code>#{payment_id}</code>\n\n"
                f"📱 <b>Реквизиты:</b>\n"
                f"• <b>Банк:</b> {Config.BANK_NAME}\n"
                f"• <b>Телефон:</b> <code>{Config.SUPPORT_PHONE}</code>\n\n"
                f"📩 После оплаты напишите @{Config.SUPPORT_USERNAME}:\n"
                f"• Скриншот платежа\n• Номер платежа: <code>#{payment_id}</code>\n\n"
                f"⏱ Подтверждение 5–30 минут."
            )
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button("main_menu"))

        elif data == "pay_crypto":
            await query.answer()
            plan = context.user_data.get('selected_plan', {"name": "1 месяц", "price": Config.PRICE_1_MONTH})
            result = await crypto_bot.create_invoice(
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
                    f"💰 К оплате: <b>{result.get('amount')} {result.get('currency', 'RUB')}</b>\n"
                    f"🔢 Платёж: <code>#{payment_id}</code>\n\n"
                    f"Нажмите «Оплатить», затем «Проверить оплату» после перевода."
                )
                await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            else:
                logger.error(f"CryptoBot create_invoice failed: {result.get('error')}")
                text = (
                    "₿ <b>Оплата криптовалютой</b>\n\n"
                    "<i>Сервис оплаты временно недоступен.</i>\n\nИспользуйте оплату на карту."
                )
                await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button("main_menu"))

        elif data.startswith("check_"):
            try:
                invoice_id = int(data.split("_")[1])
            except (ValueError, IndexError):
                await query.answer("Неверный инвойс", show_alert=True)
                return
            invoice = context.user_data.get('active_invoice', {})
            if not invoice or invoice.get("invoice_id") != invoice_id:
                await query.answer("Сначала создайте платёж", show_alert=True)
                return

            payment_id = invoice["payment_id"]
            payment_record = get_payment(payment_id)
            if payment_record and payment_record['status'] in ('paid', 'approved'):
                await query.answer("Платёж уже обработан!", show_alert=True)
                return

            result = await crypto_bot.get_invoice(invoice_id)
            if not result["success"]:
                await query.answer("Ошибка проверки платежа", show_alert=True)
                return

            status = result["status"]
            if status == "paid":
                if not update_payment_status(payment_id, "paid", f"tg_{user.id}"):
                    await query.answer("Платёж уже обработан. Проверьте 'Мой аккаунт'.", show_alert=True)
                    return

                await query.answer("Оплата получена! Активируем подписку...")
                mr = await _activate_user_sub(user.id, 30)
                if mr["success"]:
                    mark_trial_used(user.id)
                    expire_str = datetime.fromtimestamp(mr["expire"]).strftime("%d.%m.%Y")
                    hy2 = build_hy2_block(user.id)
                    text = (
                        f"✅ <b>Оплата прошла успешно!</b>\n\n"
                        f"📅 Подписка активна до: <b>{expire_str}</b>\n"
                        f"🔗 <b>VLESS-ссылка:</b>\n<code>{mr['subscription_url']}</code>\n\n"
                    )
                    if hy2:
                        text += hy2 + "\n\n"
                    text += "💡 Вставьте в Hiddify / V2RayNG / Streisand."
                    await query.edit_message_text(
                        text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=get_back_button("main_menu")
                    )
                else:
                    await query.edit_message_text(f"❌ Ошибка выдачи подписки: {mr.get('error')}")
            elif status == "active":
                await query.answer("Оплата ещё не поступила. Попробуйте через минуту.", show_alert=True)
            elif status == "expired":
                await query.answer("Инвойс истёк. Создайте платёж заново.", show_alert=True)
            else:
                await query.answer(f"Статус: {status}", show_alert=True)

        elif data == "my_account":
            await query.answer()
            info = get_user_info(user.id)
            created = _row_get(info, 'created_at', 'Неизвестно')
            mu = _row_get(info, 'marzban_username')
            username_disp = f"@{user.username}" if user.username else "—"
            text = (
                f"👤 <b>Ваш профиль</b>\n\n"
                f"🆔 ID: <code>{user.id}</code>\n"
                f"👤 Имя: {user.full_name}\n"
                f"📱 Username: {username_disp}\n"
                f"📅 С нами с: {str(created)[:10]}\n\n"
            )
            keyboard_rows = []
            if mu:
                ud = await marzban.get_user(mu)
                if ud["success"]:
                    d = ud["data"]
                    status = d.get('status', 'unknown')
                    expire = d.get('expire')
                    status_emoji = "🟢" if status == "active" else "🔴"
                    expire_str = datetime.fromtimestamp(expire).strftime("%d.%m.%Y") if expire else "бессрочно"
                    text += f"{status_emoji} <b>Подписка:</b> {status} (до {expire_str})\n"
                    sub_active = (status == "active")

                    if sub_active and Config.get_hy2_servers():
                        hy_row = get_hy2_cred(user.id)
                        need_sync = (not hy_row) or (not hy_row["active"]) or (not hy_row["expires_at"])
                        if need_sync:
                            exp_dt = (datetime.fromtimestamp(expire) if expire
                                      else datetime.now() + timedelta(days=30))
                            upsert_hy2_cred(user.id, exp_dt)

                    keyboard_rows.append([InlineKeyboardButton("📊 Подробнее о подписке", callback_data="sub_info")])
                    keyboard_rows.append([InlineKeyboardButton("🔗 VLESS-конфиг", callback_data="resend_link")])
                else:
                    text += "📌 <b>Подписка:</b> данные недоступны\n"
            else:
                text += "📌 <b>Подписка:</b> нет активной\n\nАктивируйте пробный период или купите подписку в меню 💳"

            if Config.get_hy2_servers() and hy2_access_valid(user.id):
                keyboard_rows.append([InlineKeyboardButton("🌀 Hysteria2-конфиг", callback_data="hy2_configs")])

            keyboard_rows.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="main_menu")])
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard_rows))

        elif data == "sub_info":
            info = get_user_info(user.id)
            mu = _row_get(info, 'marzban_username')
            if not mu:
                await query.answer("У вас нет активной подписки", show_alert=True)
                return
            ud = await marzban.get_user(mu)
            if not ud["success"]:
                await query.answer("Не удалось получить данные", show_alert=True)
                return
            await query.answer()
            d = ud["data"]
            expire = d.get('expire')
            expire_str = datetime.fromtimestamp(expire).strftime("%d.%m.%Y %H:%M") if expire else "бессрочно"
            used = (d.get('used_traffic') or 0) / 1024 / 1024 / 1024
            limit = d.get('data_limit')
            limit_str = f"{limit / 1024 / 1024 / 1024:.0f} ГБ" if limit else "∞"
            status = d.get('status', 'unknown')
            status_emoji = "🟢" if status == "active" else "🔴"
            text = (
                f"📊 <b>Ваша подписка</b>\n\n"
                f"{status_emoji} <b>Статус:</b> {status}\n"
                f"📅 <b>Действует до:</b> {expire_str}\n"
                f"📊 <b>Трафик:</b> {used:.2f} ГБ / {limit_str}\n"
            )
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button("my_account"))

        elif data == "resend_link":
            info = get_user_info(user.id)
            mu = _row_get(info, 'marzban_username')
            if not mu:
                await query.answer("У вас нет активной подписки", show_alert=True)
                return
            ud = await marzban.get_user(mu)
            if ud["success"] and ud.get("subscription_url"):
                await query.answer()
                full_url = ud["subscription_url"]
                text = (
                    f"🔗 <b>VLESS-конфиг</b>\n\n"
                    f"<code>{full_url}</code>\n\n"
                    f"💡 Скопируйте и вставьте в Hiddify / V2RayNG / Streisand."
                )
                await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button("my_account"))
            else:
                await query.answer("Не удалось получить ссылку", show_alert=True)

        elif data == "hy2_configs":
            if not hy2_access_valid(user.id):
                await query.answer("Нет активной подписки — Hysteria2 недоступен", show_alert=True)
                return
            block = build_hy2_block(user.id)
            if not block:
                await query.answer("Hysteria2 пока не настроен", show_alert=True)
                return
            await query.answer()
            text = (
                "🌀 <b>Ваши Hysteria2 конфиги</b>\n\n"
                "Персональные — у каждого свой ключ, можно отозвать отдельно.\n\n"
                + block
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В профиль", callback_data="my_account")],
                [InlineKeyboardButton("🏠 В меню", callback_data="main_menu")]
            ])
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

        elif data == "terms":
            await query.answer()
            text = (
                "📋 <b>Пользовательское соглашение</b>\n\n"
                "1. Сервис предоставляется «как есть» без гарантий.\n"
                "2. Вы несёте ответственность за соблюдение законодательства вашей страны.\n"
                "3. Запрещено использовать сервис для противоправной деятельности.\n"
                "4. Возврат возможен в течение 24 часов, если услугой не пользовались.\n"
                "5. Администратор вправе изменить условия в одностороннем порядке.\n\n"
                "Используя сервис, вы принимаете эти условия."
            )
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())

        elif data == "help":
            await query.answer()
            text = (
                "❓ <b>Помощь</b>\n\n"
                f"📞 <b>Поддержка:</b> @{Config.SUPPORT_USERNAME}\n\n"
                "<b>Как подключиться:</b>\n"
                "1. Активируйте пробный период (3 дня) или купите подписку\n"
                "2. Скопируйте ссылки (VLESS и/или Hysteria2)\n"
                "3. Вставьте в приложение:\n"
                "   • Hiddify (все платформы) — рекомендуется\n"
                "   • V2RayNG ≥ 1.8 (Android)\n"
                "   • Streisand (iOS)\n"
                "   • v2rayN ≥ 6.0 (Windows)\n\n"
                "🌀 <b>Hysteria2</b> — используйте, если VLESS тормозит/блокируется.\n\n"
                "<b>Проблемы?</b> Напишите в поддержку."
            )
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_back_button())

    except Exception as e:
        logger.error(f"handle_button error ({data}): {e}", exc_info=True)
        try:
            await query.edit_message_text(
                "⚠️ Произошла ошибка. Попробуйте ещё раз или напишите в поддержку.",
                reply_markup=get_back_button("main_menu")
            )
        except Exception:
            pass


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    user = update.effective_user
    text = update.message.text.lower()
    if "привет" in text or "hi" in text:
        await update.message.reply_text("Привет! 👋", reply_markup=_menu_for_user(user.id))


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not Config.is_admin(update.effective_user.id):
        return
    with get_db_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM users WHERE date(last_active) = date('now')").fetchone()[0]
        trials = conn.execute("SELECT COUNT(*) FROM users WHERE trial_used = TRUE").fetchone()[0]
        pend = conn.execute("SELECT COUNT(*) FROM payments WHERE status = 'pending'").fetchone()[0]
        paid = conn.execute("SELECT COUNT(*) FROM payments WHERE status IN ('approved','paid')").fetchone()[0]
    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Всего пользователей: {total}\n"
        f"🟢 Активных сегодня: {active}\n"
        f"🎁 Взяли триал: {trials}\n\n"
        f"💳 Платежей в ожидании: {pend}\n"
        f"✅ Оплачено: {paid}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception: {context.error}", exc_info=context.error)
