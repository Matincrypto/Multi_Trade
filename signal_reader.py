# signal_reader.py
# نسخه اصلاح شده: حل مشکل تایم‌زون با استفاده از زمان خود دیتابیس

import time
import logging
import config
import db_manager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def fetch_signals():
    """خواندن سیگنال‌های جدید با استفاده از زمان سرور دیتابیس"""
    conn = db_manager.get_signal_pool_connection()
    if not conn: return []
    
    cursor = conn.cursor(dictionary=True)
    try:
        # تغییر استراتژی: به جای محاسبه زمان در پایتون، به MySQL می‌گوییم
        # "سیگنال‌های X دقیقه اخیر" را بده. اینطوری تایم‌زون پایتون و دیتابیس مهم نیست.
        
        query = """
            SELECT * FROM signal_pool 
            WHERE signal_time >= (NOW() - INTERVAL %s MINUTE)
            ORDER BY signal_time ASC
        """
        
        lookback = config.BOT_SETTINGS.get("SIGNAL_LOOKBACK_MINUTES", 5)
        cursor.execute(query, (lookback,))
        
        signals = cursor.fetchall()
        
        if signals:
            logging.info(f"Fetched {len(signals)} signals from DB")
            
        return signals

    except Exception as e:
        logging.error(f"Error reading pool: {e}")
        return []
    finally:
        conn.close()

def distribute_signals():
    logging.info("📡 Signal Reader Engine Started (Timezone Fix Applied)...")
    
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
                        strategy = sig.get('strategy_name') or 'Unknown'
                        grade = sig.get('signal_grade')

                        # لاگ پیدا شدن سیگنال (جهت اطمینان از دیده شدن)
                        logging.info(f"🔎 Signal Found: {asset}/{pair} ({strategy})")

                        for acc in active_accounts:
                            # 1. فیلتر استراتژی
                            allowed_strats = acc.get('allowed_strategies', '')
                            if allowed_strats and allowed_strats != 'ALL':
                                if strategy not in allowed_strats.split(','):
                                    continue 

                            # 2. فیلتر گرید
                            allowed_grades = acc.get('allowed_grades', '')
                            if allowed_grades and allowed_grades != 'ALL':
                                if grade not in allowed_grades.split(','):
                                    continue 

                            # 3. بررسی بودجه
                            budget = acc['trade_amount_tmn'] if pair == 'TMN' else acc['trade_amount_usdt']
                            if budget <= 0:
                                continue

                            # 4. بررسی تکراری
                            exists = db_manager.execute_query(
                                """
                                SELECT id FROM trade_ops 
                                WHERE account_id=%s AND asset_name=%s AND pair=%s AND strategy_name=%s
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
                                logging.info(f"✅ Queued: {asset}/{pair} -> User {acc['account_name']}")
                            
                else:
                    # اگر سیگنال هست ولی کاربر فعال نیست، این لاگ کمک میکنه بفهمیم
                    if signals:
                        logging.warning("⚠️ Signals found but NO ACTIVE ACCOUNTS detected.")
                
        except Exception as e:
            logging.error(f"Reader Error: {e}")
        
        time.sleep(config.BOT_SETTINGS["CHECK_INTERVAL"])

if __name__ == "__main__":
    distribute_signals()
