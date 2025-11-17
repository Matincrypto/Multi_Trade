# signal_reader.py
import time
import logging
import config
import db_manager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def fetch_signals():
    """خواندن سیگنال‌های جدید از استخر (signal_pool)"""
    conn = db_manager.get_signal_pool_connection()
    if not conn: return []
    
    cursor = conn.cursor(dictionary=True)
    try:
        # دریافت سیگنال‌های X دقیقه اخیر
        # نگاشت ستون‌ها طبق خروجی شما: 
        # signal_time, pair, coin, entry_price, target_price
        query = """
            SELECT * FROM signal_pool 
            WHERE signal_time >= NOW() - INTERVAL %s MINUTE
            ORDER BY signal_time ASC
        """
        cursor.execute(query, (config.BOT_SETTINGS["SIGNAL_LOOKBACK_MINUTES"],))
        return cursor.fetchall()
    except Exception as e:
        logging.error(f"Error reading signal pool: {e}")
        return []
    finally:
        conn.close()

def distribute_signals():
    """موتور توزیع سیگنال بین کاربران فعال"""
    logging.info("📡 Signal Reader Engine Started (Connected to signal_pool)...")
    
    while True:
        try:
            signals = fetch_signals()
            
            if signals:
                # دریافت لیست کاربران فعال
                active_accounts = db_manager.execute_query(
                    "SELECT * FROM trading_accounts WHERE is_active = TRUE",
                    fetch='all'
                )
                
                if active_accounts:
                    for sig in signals:
                        asset = sig['coin']        # ستون coin از دیتابیس شما (مثلا ADA)
                        pair = sig['pair']         # ستون pair (مثلا TMN یا USDT)
                        entry = sig['entry_price']
                        target = sig['target_price'] # ستون target_price
                        
                        # لاگ کردن سیگنال دریافتی
                        logging.info(f"Signal Found: {asset}/{pair} | Price: {entry} | Target: {target}")

                        for acc in active_accounts:
                            # 1. بررسی بودجه کاربر برای این نوع جفت ارز
                            invest_amount = 0
                            if pair == 'TMN':
                                invest_amount = acc['trade_amount_tmn']
                            elif pair == 'USDT':
                                invest_amount = acc['trade_amount_usdt']
                            
                            if invest_amount <= 0:
                                continue # کاربر بودجه‌ای برای این جفت ارز ندارد

                            # 2. بررسی تکراری بودن (آیا کاربر قبلاً این ارز را خریده؟)
                            exists = db_manager.execute_query(
                                """
                                SELECT id FROM trade_ops 
                                WHERE account_id=%s AND asset_name=%s AND pair=%s
                                AND status NOT IN ('SELL_ORDER_FILLED', 'CANCELED_TIMEOUT', 'ERROR')
                                """,
                                (acc['account_id'], asset, pair),
                                fetch='one'
                            )
                            
                            if not exists:
                                # 3. ثبت در صف ترید (trade_ops)
                                db_manager.execute_query(
                                    """
                                    INSERT INTO trade_ops 
                                    (account_id, asset_name, pair, entry_price, exit_price, strategy_name, status)
                                    VALUES (%s, %s, %s, %s, %s, %s, 'NEW_SIGNAL')
                                    """,
                                    (
                                        acc['account_id'], 
                                        asset, 
                                        pair, 
                                        entry, 
                                        target, 
                                        sig.get('strategy_name', 'Unknown')
                                    )
                                )
                                logging.info(f"✅ Queued for User {acc['account_id']} -> {asset}/{pair}")
                
        except Exception as e:
            logging.error(f"Reader Loop Error: {e}")
        
        time.sleep(config.BOT_SETTINGS["CHECK_INTERVAL"])

if __name__ == "__main__":
    distribute_signals()
