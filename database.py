import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
import logging
import secrets

logger = logging.getLogger(__name__)
DB_PATH = 'vpn_bot.db'


def check_db_integrity():
    """Проверяет целостность БД без удаления рабочих данных при сбое."""
    if not os.path.exists(DB_PATH):
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchone()
        conn.close()
        if not result or result[0] != "ok":
            backup_path = f"{DB_PATH}.corrupt.{int(datetime.now().timestamp())}"
            logger.critical(f"Обнаружено повреждение БД ({result})! Сохраняю резервную копию в {backup_path}")
            shutil.copy2(DB_PATH, backup_path)
    except Exception as e:
        logger.error(f"Ошибка проверки целостности БД: {e}")


@contextmanager
def get_db_connection():
    """Контекстный менеджер для безопасной работы с БД"""
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        yield conn
        conn.commit()
    except sqlite3.DatabaseError as e:
        conn.rollback()
        logger.error(f"Ошибка БД: {e}")
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
                trial_used BOOLEAN DEFAULT FALSE,
                marzban_username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

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
            "ALTER TABLE users ADD COLUMN trial_used BOOLEAN DEFAULT FALSE",
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
              user.last_name or "", datetime.now().isoformat()))


def get_user_info(user_id: int):
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def set_user_marzban(user_id: int, marzban_username: str):
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE users SET marzban_username = ? WHERE id = ?",
            (marzban_username, user_id)
        )


def has_used_trial(user_id: int) -> bool:
    row = get_user_info(user_id)
    if not row:
        return False
    return bool(row["trial_used"]) or bool(row["marzban_username"])


def mark_trial_used(user_id: int):
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE users SET trial_used = TRUE WHERE id = ?",
            (user_id,)
        )


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
                          marzban_username: str = None) -> bool:
    """Атомарно обновляет статус платежа только если он ещё в 'pending'.
    Возвращает True, если статус был успешно изменён."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if marzban_username:
            cursor.execute('''
                UPDATE payments 
                SET status = ?, marzban_username = ?, paid_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'pending'
            ''', (status, marzban_username, payment_id))
        else:
            cursor.execute(
                "UPDATE payments SET status = ? WHERE id = ? AND status = 'pending'",
                (status, payment_id)
            )
        return cursor.rowcount > 0


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
    """Создаёт или ПРОДЛЕВАЕТ Hysteria2-доступ до expires_at."""
    exp_iso = expires_at.isoformat()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        try:
            cursor.execute(
                "SELECT user_id, username, password, active, expires_at "
                "FROM hy2_credentials WHERE user_id = ?",
                (user_id,)
            )
            row = cursor.fetchone()

            if row:
                cursor.execute(
                    "UPDATE hy2_credentials SET active = 1, expires_at = ? WHERE user_id = ?",
                    (exp_iso, user_id)
                )
                conn.commit()
                return row["username"], row["password"]

            username = f"tg{user_id}"
            password = secrets.token_urlsafe(16)
            cursor.execute(
                "INSERT INTO hy2_credentials "
                "(user_id, username, password, active, expires_at, created_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (user_id, username, password, exp_iso, datetime.now().isoformat())
            )
            conn.commit()
            return username, password
        except Exception:
            conn.rollback()
            raise


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
