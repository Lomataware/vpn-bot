"""
Отдельный HTTPS-бэкенд аутентификации для Hysteria2 (auth.type: http).
Читает ту же sqlite через database.check_hy2_auth -> не дублируем путь БД и секреты.
Запускается отдельным юнитом hy2auth.service, НЕ зависит от vpnbot.service.

Контракт Hysteria2: POST {addr, auth, tx} -> 200 {ok:true,id} ИЛИ не-200 {ok:false}.
В userpass-режиме поле auth = "username:password".
Общий секрет передаётся в пути: /hy2auth/<TOKEN> (у Hysteria2 нет кастомных заголовков).
"""
import ssl
import hmac
import logging
from aiohttp import web

from config import Config
from database import check_hy2_auth, init_db

logging.basicConfig(
    format='%(asctime)s - hy2auth - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("hy2auth")


def _ok(user_id: str):
    return web.json_response({"ok": True, "id": user_id}, status=200)


def _fail(reason: str = "denied", status: int = 403):
    return web.json_response({"ok": False, "reason": reason}, status=status)


async def handle_auth(request: web.Request):
    # 1) Проверка общего секрета из пути (constant-time)
    token_in_path = request.match_info.get("token", "")
    if not hmac.compare_digest(token_in_path, Config.HY2_AUTH_TOKEN or "__unset__"):
        logger.warning("auth: bad token from %s", request.remote)
        return _fail("bad token", 401)

    # 2) Парсинг тела от Hysteria2
    try:
        body = await request.json()
    except Exception:
        return _fail("bad json", 400)

    auth_str = body.get("auth", "")
    addr = body.get("addr", "")
    if not auth_str or ":" not in auth_str:
        logger.info("auth: malformed auth field from %s", addr)
        return _fail("malformed", 401)

    username, password = auth_str.split(":", 1)

    # 3) Проверка пары + срока по БД
    if check_hy2_auth(username, password):
        logger.info("auth OK user=%s addr=%s", username, addr)
        return _ok(username)

    logger.info("auth DENY user=%s addr=%s", username, addr)
    return _fail("invalid credentials", 403)


async def handle_health(request: web.Request):
    return web.json_response({"status": "ok"})


def main():
    if not Config.HY2_AUTH_TOKEN:
        logger.error("HY2_AUTH_TOKEN не задан в .env!")
        return

    # Гарантируем актуальную схему БД (в т.ч. колонку expires_at) до первой проверки
    init_db()

    app = web.Application()
    app.router.add_post("/hy2auth/{token}", handle_auth)
    app.router.add_get("/health", handle_health)

    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(Config.HY2_AUTH_CERTFILE, Config.HY2_AUTH_KEYFILE)

    logger.info("hy2auth стартует на %s:%s (https)", Config.HY2_AUTH_HOST, Config.HY2_AUTH_PORT)
    web.run_app(app, host=Config.HY2_AUTH_HOST, port=Config.HY2_AUTH_PORT, ssl_context=ssl_ctx)


if __name__ == "__main__":
    main()
