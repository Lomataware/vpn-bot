import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
DB_PATH = 'vpn_bot.db'


def check_db_integrity():
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                plan_type TEXT,
                amount_paid INTEGER,
                payment_method TEXT,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')


def save_user(user):
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
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_users_count():
    with get_db_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM users WHERE is_blocked = FALSE").fetchone()[0]


def save_payment(user_id: int, plan: str, amount: int, method: str, 
                 status: str = "pending", external_id: str = None) -> int:
    """Сохраняет платёж и возвращает его ID"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Создаём таблицу если её нет
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
        cursor.execute('''
            INSERT INTO payments (user_id, plan, amount, method, status, external_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, plan, amount, method, status, external_id))
        return cursor.lastrowid


def update_payment_status(payment_id: int, status: str, 
                          marzban_username: str = None):
    """Обновляет статус платежа"""
    with get_db_connection() as conn:
        if marzban_username:
            conn.execute('''
                UPDATE payments SET status = ?, marzban_username = ?, paid_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (status, marzban_username, payment_id))
        else:
            conn.execute(
                "UPDATE payments SET status = ? WHERE id = ?",
                (status, payment_id)
            )


def get_pending_payments(user_id: int) -> list:
    """Получить все ожидающие платежи пользователя"""
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT * FROM payments WHERE user_id = ? AND status = 'pending' ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()


def get_payment(payment_id: int):
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT * FROM payments WHERE id = ?", (payment_id,)
        ).fetchone()
