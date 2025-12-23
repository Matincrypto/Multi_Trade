# main.py
import threading
import time
import logging
import sys

# ایمپورت کردن ماژول‌های پروژه
import signal_reader
import executor
import telegram_bot

# تنظیمات لاگ کلی
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [MASTER] - %(message)s',
    handlers=[
        logging.FileHandler("bot_log.log"), # ذخیره لاگ در فایل
        logging.StreamHandler(sys.stdout)   # نمایش در کنسول
    ]
)

def run_signal_reader():
    """اجرای ماژول خواندن سیگنال"""
    try:
        logging.info("Starting Signal Reader...")
        signal_reader.distribute_signals()
    except Exception as e:
        logging.critical(f"Signal Reader Crashed: {e}")

def run_executor():
    """اجرای ماژول ترید"""
    try:
        logging.info("Starting Executor...")
        executor.run_executor()
    except Exception as e:
        logging.critical(f"Executor Crashed: {e}")

def run_telegram():
    """اجرای بات تلگرام (باید در ترد اصلی یا جداگانه باشد)"""
    try:
        logging.info("Starting Telegram Bot...")
        telegram_bot.run_bot()
    except Exception as e:
        logging.critical(f"Telegram Bot Crashed: {e}")

if __name__ == "__main__":
    logging.info("--- System Starting Up ---")

    # 1. ساخت ترد برای Reader
    t_reader = threading.Thread(target=run_signal_reader, name="ReaderThread", daemon=True)
    
    # 2. ساخت ترد برای Executor
    t_executor = threading.Thread(target=run_executor, name="ExecutorThread", daemon=True)

    # 3. شروع تردها
    t_reader.start()
    t_executor.start()

    # 4. اجرای تلگرام در ترد اصلی (چون run_polling مسدودکننده است)
    # وقتی تلگرام بسته شود، کل برنامه بسته می‌شود
    run_telegram()
