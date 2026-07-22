import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
import logging
import secrets

logger = logging.getLogger(__name__)
DB_PATH = 'vpn_bot.db'


def check_db_integrity():
    """Проверяет целостность БД и удаляет её, если повреждена"""
    if not os.path.exists(DB_PATH):
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchone()
        conn.close()
        if result[0] != "ok":
            logger.warning(f"БД повреждена: {result[0]}, пересоздаю...")
            os.remove(DB_PATH)
    except Exception as e:
        logger.error(f"Ошибка проверки БД: {e}")
        try:
            os.remove(DB_PATH)
        except:
            pass


@contextmanager
def get_db_connection():
    """Контекстный менеджер для безопасной работы с БД"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    except sqlite3.DatabaseError as e:
        conn.rollback()
        logger.error(f"Ошибка БД: {e}")
        if "malformed" in str(e).lower():
            try:
                os.remove(DB_PATH)
            except:
                pass
        raise
    finally:
        conn.close()


def init_db():
    """Инициализация базы данных"""
    check_db_integrity()
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_blocked BOOLEAN DEFAULT FALSE,
                marzban_username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Hysteria2-учётки с привязкой к сроку подписки
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS hy2_credentials (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT NOT NULL UNIQUE,
                password   TEXT NOT NULL,
                active     INTEGER NOT NULL DEFAULT 1,
                expires_at TEXT,
                created_at TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                plan TEXT,
                amount INTEGER,
                method TEXT,
                status TEXT DEFAULT 'pending',
                external_id TEXT,
                marzban_username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                paid_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')

        # Миграции для уже существующих таблиц
        for stmt in (
            "ALTER TABLE users ADD COLUMN is_blocked BOOLEAN DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN marzban_username TEXT",
            "ALTER TABLE users ADD COLUMN last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "ALTER TABLE hy2_credentials ADD COLUMN expires_at TEXT",
        ):
            try:
                cursor.execute(stmt)
            except sqlite3.OperationalError:
                pass


def save_user(user):
    """Сохранение/обновление пользователя"""
    with get_db_connection() as conn:
        conn.execute('''
            INSERT INTO users (id, username, first_name, last_name, last_active)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                last_active=excluded.last_active
        ''', (user.id, user.username or "", user.first_name or "",
              user.last_name or "", datetime.now()))


def get_user_info(user_id):
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def get_users_count():
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_blocked = FALSE"
        ).fetchone()[0]


def save_payment(user_id: int, plan: str, amount: int, method: str,
                 status: str = "pending", external_id: str = None) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO payments (user_id, plan, amount, method, status, external_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, plan, amount, method, status, external_id))
        return cursor.lastrowid


def update_payment_status(payment_id: int, status: str,
                          marzban_username: str = None):
    with get_db_connection() as conn:
        if marzban_username:
            conn.execute('''
                UPDATE payments 
                SET status = ?, marzban_username = ?, paid_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (status, marzban_username, payment_id))
        else:
            conn.execute(
                "UPDATE payments SET status = ? WHERE id = ?",
                (status, payment_id)
            )


def get_pending_payments(user_id: int) -> list:
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT * FROM payments WHERE user_id = ? AND status = 'pending' "
            "ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()


def get_payment(payment_id: int):
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT * FROM payments WHERE id = ?", (payment_id,)
        ).fetchone()


# ===== Hysteria2 =====

def get_hy2_cred(user_id: int):
    """Возвращает запись (username,password,active,expires_at) или None."""
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT user_id, username, password, active, expires_at "
            "FROM hy2_credentials WHERE user_id = ?",
            (user_id,)
        ).fetchone()


def hy2_access_valid(user_id: int) -> bool:
    """Есть ли у пользователя активный и НЕ истёкший Hysteria2-доступ."""
    row = get_hy2_cred(user_id)
    if not row or not row["active"]:
        return False
    exp = row["expires_at"]
    if not exp:
        return False
    try:
        return datetime.fromisoformat(exp) > datetime.now()
    except (ValueError, TypeError):
        return False


def upsert_hy2_cred(user_id: int, expires_at: datetime):
    """Создаёт или ПРОДЛЕВАЕТ Hysteria2-доступ до expires_at.
    Вызывается ТОЛЬКО при выдаче/продлении подписки — не по нажатию кнопки."""
    exp_iso = expires_at.isoformat()
    row = get_hy2_cred(user_id)
    if row:
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE hy2_credentials SET active = 1, expires_at = ? WHERE user_id = ?",
                (exp_iso, user_id)
            )
        return row["username"], row["password"]
    username = f"tg{user_id}"
    password = secrets.token_urlsafe(16)
    with get_db_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO hy2_credentials "
            "(user_id, username, password, active, expires_at, created_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (user_id, username, password, exp_iso, datetime.now().isoformat())
        )
    return username, password


def set_hy2_active(user_id: int, active: bool):
    """Ручной отзыв/возврат доступа (для админа)."""
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE hy2_credentials SET active = ? WHERE user_id = ?",
            (1 if active else 0, user_id)
        )


def check_hy2_auth(username: str, password: str) -> bool:
    """Auth-сервер: пускаем, только если пара верна, активна И срок не истёк."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT active, expires_at FROM hy2_credentials "
            "WHERE username = ? AND password = ?",
            (username, password)
        ).fetchone()
    if not row or not row["active"]:
        return False
    exp = row["expires_at"]
    if not exp:
        return False
    try:
        return datetime.fromisoformat(exp) > datetime.now()
    except (ValueError, TypeError):
        return False
