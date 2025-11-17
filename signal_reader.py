# signal_reader.py
import time
import logging
import config
import db_manager
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def fetch_signals():
    """خواندن سیگنال‌های تازه از استخر"""
    conn = db_manager.get_signal_pool_connection()
    if not conn: return []
    
    cursor = conn.cursor(dictionary=True)
    try:
        # فقط سیگنال‌هایی که در X دقیقه اخیر آمده‌اند
        query = """
            SELECT * FROM signal_pool 
            WHERE signal_time >= NOW() - INTERVAL %s MINUTE
            ORDER BY signal_time ASC
        """
        cursor.execute(query, (config.BOT_SETTINGS["SIGNAL_LOOKBACK_MINUTES"],))
        return cursor.fetchall()
    except Exception as e:
        logging.error(f"Error reading pool: {e}")
        return []
    finally:
        conn.close()

def distribute_signals():
    """حلقه اصلی توزیع سیگنال"""
    logging.info("📡 Signal Reader Engine Started...")
    
    while True:
        try:
            # 1. دریافت سیگنال‌ها
            signals = fetch_signals()
            
            if signals:
                # 2. دریافت کاربران فعال
                active_accounts = db_manager.execute_query(
                    "SELECT * FROM trading_accounts WHERE is_active = TRUE",
                    fetch='all'
                )
                
                if active_accounts:
                    for sig in signals:
                        asset = sig['coin'] # مثلا BTC
                        pair = sig['pair']  # مثلا TMN یا USDT
                        
                        logging.info(f"New Signal Found: {asset}/{pair}")

                        for acc in active_accounts:
                            # چک کردن موجودی تنظیم شده توسط کاربر برای این جفت ارز
                            invest_amount = 0
                            if pair == 'TMN':
                                invest_amount = acc['trade_amount_tmn']
                            elif pair == 'USDT':
                                invest_amount = acc['trade_amount_usdt']
                            
                            # اگر کاربر برای این جفت ارز بودجه‌ای تعیین نکرده بود (صفر بود)، سیگنال را نادیده بگیر
                            if invest_amount <= 0:
                                continue

                            # بررسی تکراری بودن
                            exists = db_manager.execute_query(
                                """
                                SELECT id FROM trade_ops 
                                WHERE account_id=%s AND asset_name=%s AND pair=%s
                                AND status NOT IN ('SELL_ORDER_FILLED', 'CANCELED_TIMEOUT')
                                """,
                                (acc['account_id'], asset, pair),
                                fetch='one'
                            )
                            
                            if not exists:
                                # ثبت در صف انجام کار (trade_ops)
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
                                        sig['entry_price'], 
                                        sig['target_price'], 
                                        sig.get('strategy_name', 'Auto')
                                    )
                                )
                                logging.info(f"✅ Signal queued for User {acc['account_id']} -> {asset}/{pair}")
                
        except Exception as e:
            logging.error(f"Loop Error: {e}")
        
        time.sleep(config.BOT_SETTINGS["CHECK_INTERVAL"])

if __name__ == "__main__":
    distribute_signals()
