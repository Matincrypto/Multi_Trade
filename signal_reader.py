# signal_reader.py
# نسخه نهایی: فیلتر زمانی دقیق تهران + تفکیک استراتژی در بررسی تکراری

import time
import logging
import config
import db_manager
from datetime import datetime, timedelta
import pytz # برای مدیریت زمان تهران

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def fetch_signals():
    """خواندن سیگنال‌های جدید از استخر با فیلتر زمانی دقیق تهران"""
    conn = db_manager.get_signal_pool_connection()
    if not conn: return []
    
    cursor = conn.cursor(dictionary=True)
    try:
        # 1. محاسبه زمان دقیق "5 دقیقه پیش" به وقت تهران
        tehran_tz = pytz.timezone('Asia/Tehran')
        now_tehran = datetime.now(tehran_tz)
        
        # کسر کردن 5 دقیقه (یا هر عددی که در کانفیگ است)
        lookback_mins = config.BOT_SETTINGS.get("SIGNAL_LOOKBACK_MINUTES", 5)
        time_threshold = now_tehran - timedelta(minutes=lookback_mins)
        
        # فرمت کردن زمان برای MySQL (YYYY-MM-DD HH:MM:SS)
        time_threshold_str = time_threshold.strftime('%Y-%m-%d %H:%M:%S')
        
        # 2. کوئری با شرط زمانی
        # فقط سیگنال‌هایی که زمانشان جدیدتر از 5 دقیقه پیش است
        query = """
            SELECT * FROM signal_pool 
            WHERE signal_time >= %s
            ORDER BY signal_time ASC
        """
        cursor.execute(query, (time_threshold_str,))
        signals = cursor.fetchall()
        
        if signals:
            logging.info(f"Fetched {len(signals)} signals (Newer than {time_threshold_str} Tehran Time)")
            
        return signals

    except Exception as e:
        logging.error(f"Error reading pool: {e}")
        return []
    finally:
        conn.close()

def distribute_signals():
    logging.info("📡 Signal Reader Engine Started...")
    
    while True:
        try:
            signals = fetch_signals()
            
            if signals:
                # دریافت کاربران فعال
                active_accounts = db_manager.execute_query(
                    "SELECT * FROM trading_accounts WHERE is_active = TRUE",
                    fetch='all'
                )
                
                if active_accounts:
                    for sig in signals:
                        asset = sig['coin']
                        pair = sig['pair']
                        # گرفتن نام استراتژی (اگر خالی بود بذار Unknown)
                        strategy = sig.get('strategy_name') or 'Unknown'
                        grade = sig.get('signal_grade')

                        # لاگ خلاصه سیگنال
                        # logging.info(f"Signal: {asset}/{pair} ({strategy})")

                        for acc in active_accounts:
                            # --- 1. فیلتر استراتژی کاربر ---
                            allowed_strats = acc.get('allowed_strategies', '')
                            if allowed_strats and allowed_strats != 'ALL':
                                if strategy not in allowed_strats.split(','):
                                    continue 

                            # --- 2. فیلتر گرید کاربر ---
                            allowed_grades = acc.get('allowed_grades', '')
                            if allowed_grades and allowed_grades != 'ALL':
                                if grade not in allowed_grades.split(','):
                                    continue 

                            # --- 3. بررسی بودجه ---
                            budget = acc['trade_amount_tmn'] if pair == 'TMN' else acc['trade_amount_usdt']
                            if budget <= 0:
                                continue

                            # --- 4. بررسی تکراری (اصلاح شده: اضافه شدن شرط استراتژی) ---
                            # معنی: آیا این کاربر، روی این ارز، با همین استراتژی پوزیشن باز دارد؟
                            exists = db_manager.execute_query(
                                """
                                SELECT id FROM trade_ops 
                                WHERE account_id=%s 
                                  AND asset_name=%s 
                                  AND pair=%s
                                  AND strategy_name=%s  -- <-- شرط جدید: بررسی استراتژی
                                  AND status NOT IN ('SELL_ORDER_FILLED', 'CANCELED_TIMEOUT', 'ERROR', 'SKIPPED_CIRCUIT_BREAKER')
                                """,
                                (acc['account_id'], asset, pair, strategy),
                                fetch='one'
                            )
                            
                            if not exists:
                                db_manager.execute_query(
                                    """
                                    INSERT INTO trade_ops 
                                    (account_id, asset_name, pair, entry_price, exit_price, strategy_name, status)
                                    VALUES (%s, %s, %s, %s, %s, %s, 'NEW_SIGNAL')
                                    """,
                                    (acc['account_id'], asset, pair, sig['entry_price'], sig['target_price'], strategy)
                                )
                                logging.info(f"✅ Queued: {asset}/{pair} ({strategy}) -> User {acc['account_name']}")
                            else:
                                pass
                                # logging.info(f"Duplicate Skipped: {asset} ({strategy}) already active for user.")
                
        except Exception as e:
            logging.error(f"Reader Error: {e}")
        
        time.sleep(config.BOT_SETTINGS["CHECK_INTERVAL"])

if __name__ == "__main__":
    distribute_signals()
