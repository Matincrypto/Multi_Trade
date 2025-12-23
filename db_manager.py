# db_manager.py
import mysql.connector
from mysql.connector import pooling
import logging
import config

# تنظیمات لاگ
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 1. ایجاد استخر اتصال برای دیتابیس داخلی (نوشتن/خواندن زیاد) ---
try:
    internal_pool = pooling.MySQLConnectionPool(
        pool_name="multi_trade_pool",
        pool_size=10,
        **config.INTERNAL_DB
    )
    logging.info("✅ Database Pool (Internal) Created.")
except Exception as e:
    logging.critical(f"❌ Critical Error creating DB Pool: {e}")
    internal_pool = None

# --- توابع کمکی ---

def get_internal_connection():
    """یک اتصال از استخر دیتابیس داخلی می‌گیرد"""
    if not internal_pool: return None
    try:
        return internal_pool.get_connection()
    except Exception as e:
        logging.error(f"Pool Connection Error: {e}")
        return None

def get_signal_pool_connection():
    """یک اتصال مستقیم به دیتابیس استخر سیگنال می‌سازد"""
    try:
        return mysql.connector.connect(**config.SIGNAL_POOL_DB)
    except Exception as e:
        logging.error(f"Signal DB Connection Error: {e}")
        return None

def execute_query(query, params=None, fetch=None, use_signal_db=False):
    """
    تابع جامع اجرای کوئری.
    """
    conn = None
    cursor = None
    
    try:
        if use_signal_db:
            conn = get_signal_pool_connection()
        else:
            conn = get_internal_connection()

        if not conn: return None

        cursor = conn.cursor(dictionary=True)
        cursor.execute(query, params or ())

        if fetch == 'all':
            return cursor.fetchall()
        elif fetch == 'one':
            return cursor.fetchone()
        else:
            conn.commit()
            return cursor.lastrowid if "INSERT" in query.upper() else cursor.rowcount

    except mysql.connector.Error as e:
        logging.error(f"SQL Error: {e}\nQuery: {query}")
        return None
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
