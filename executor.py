# executor.py
# نسخه نهایی: کسر کارمزد از مقدار دارایی قابل فروش

import time
import logging
import requests
import config
import db_manager
import wallex_api
from datetime import datetime, timedelta
from decimal import Decimal

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

TIMEOUT_MINUTES = 5

def send_telegram_alert(user_id, message):
    try:
        url = f"https://api.telegram.org/bot{config.TELEGRAM['BOT_TOKEN']}/sendMessage"
        kb = {"inline_keyboard": [[{"text": "🔙 منوی اصلی", "callback_data": "main_menu"}]]}
        payload = {'chat_id': user_id, 'text': message, 'parse_mode': 'Markdown', 'reply_markup': kb}
        requests.post(url, json=payload, timeout=5)
    except: pass

def check_circuit_breaker(account_id, pair, limit):
    if limit <= 0: return False
    query = """SELECT SUM(invested_amount) as total_locked FROM trade_ops
               WHERE account_id = %s AND pair = %s 
               AND status IN ('BUY_IN_PROGRESS', 'BUY_FILLED', 'SELL_IN_PROGRESS', 'SELL_ORDER_PLACED')"""
    res = db_manager.execute_query(query, (account_id, pair), fetch='one')
    curr = res.get('total_locked') or 0
    return curr >= limit

# --- Step 1: Place Buy ---
def step_1_place_buy():
    # ... (کد بدون تغییر) ...
    query = """SELECT t.*, a.wallex_api_key, a.user_telegram_id, a.trade_amount_tmn, a.trade_amount_usdt, 
               a.max_trade_tmn, a.max_trade_usdt 
               FROM trade_ops t JOIN trading_accounts a ON t.account_id = a.account_id
               WHERE t.status = 'NEW_SIGNAL' AND a.is_active = TRUE"""
    signals = db_manager.execute_query(query, fetch='all')
    if not signals: return

    for sig in signals:
        try:
            pair = sig['pair']
            symbol = f"{sig['asset_name']}{pair}"
            budget = sig['trade_amount_tmn'] if pair == 'TMN' else sig['trade_amount_usdt']
            limit = sig['max_trade_tmn'] if pair == 'TMN' else sig['max_trade_usdt']

            if budget <= 0:
                db_manager.execute_query("UPDATE trade_ops SET status='ERROR', notes='Budget 0' WHERE id=%s", (sig['id'],))
                continue
            if check_circuit_breaker(sig['account_id'], pair, limit):
                db_manager.execute_query("UPDATE trade_ops SET status='SKIPPED_CIRCUIT' WHERE id=%s", (sig['id'],))
                continue

            qty_prec, price_prec = wallex_api.get_precision(symbol)
            if qty_prec is None: continue

            price = float(sig['entry_price'])
            raw_qty = float(budget) / price
            final_price = wallex_api.format_price(price, price_prec)
            final_qty = wallex_api.format_quantity(raw_qty, qty_prec)

            if final_qty <= 0:
                db_manager.execute_query("UPDATE trade_ops SET status='ERROR', notes='Qty too small' WHERE id=%s", (sig['id'],))
                continue

            logging.info(f"🛒 Placing Buy {symbol} | P: {final_price} | Q: {final_qty}")
            res = wallex_api.place_order(sig['wallex_api_key'], symbol, 'buy', final_price, final_qty)

            if res and res.get('success'):
                db_manager.execute_query(
                    "UPDATE trade_ops SET status='BUY_IN_PROGRESS', buy_client_order_id=%s, invested_amount=%s, updated_at=NOW() WHERE id=%s", 
                    (res['result']['clientOrderId'], budget, sig['id'])
                )
            else:
                err = res.get('message') if res else 'API Error'
                db_manager.execute_query("UPDATE trade_ops SET status='ERROR', notes=%s WHERE id=%s", (f"Buy Fail: {err}", sig['id']))
        except Exception as e: logging.error(f"Step 1: {e}")

# ==============================================================================
# مرحله ۲: بررسی وضعیت خرید (BUY_IN_PROGRESS -> BUY_FILLED)
# ==============================================================================
def step_2_check_buy_fill():
    query = """SELECT t.*, a.wallex_api_key, a.user_telegram_id, a.trade_amount_tmn, a.trade_amount_usdt 
               FROM trade_ops t JOIN trading_accounts a ON t.account_id=a.account_id 
               WHERE t.status='BUY_IN_PROGRESS'"""
    orders = db_manager.execute_query(query, fetch='all')
    for o in orders:
        try:
            res = wallex_api.get_order_status(o['buy_client_order_id'], o['wallex_api_key'])
            
            if res and res.get('status') == 'FILLED':
                raw_executed_qty = float(res.get('executedQty'))
                fee = float(res.get('fee') or 0)
                fee_asset = res.get('feeAsset', o['pair']) # فرض بر این است که API این را برمی‌گرداند
                
                base_asset = o['asset_name']
                net_quantity = raw_executed_qty

                # 1. محاسبه مقدار خالص (کسر کارمزد اگر از خود ارز خریده شده باشد)
                if fee_asset == base_asset and fee > 0:
                    net_quantity = raw_executed_qty - fee
                    logging.info(f"Fee deducted from {base_asset}: Final Qty {net_quantity}")
                
                # 2. اعمال Precision نهایی روی مقدار خالص
                symbol = f"{base_asset}{o['pair']}"
                qty_prec, _ = wallex_api.get_precision(symbol)
                final_sell_qty = wallex_api.format_quantity(net_quantity, qty_prec)

                logging.info(f"✅ Buy Filled: {base_asset} | Net Qty: {final_sell_qty}")
                
                # 3. آپدیت دیتابیس با مقدار خالص
                db_manager.execute_query(
                    "UPDATE trade_ops SET status='BUY_FILLED', buy_quantity_executed=%s WHERE id=%s", 
                    (final_sell_qty, o['id'])
                )
                send_telegram_alert(o['user_telegram_id'], f"✅ **خرید انجام شد**\n💎 {base_asset}\n🔢 خالص: `{final_sell_qty}`")
        except Exception as e: logging.error(f"Step 2: {e}")

# ==============================================================================
# مرحله ۳: ثبت سفارش فروش (BUY_FILLED -> SELL_IN_PROGRESS)
# ==============================================================================
def step_3_place_sell():
    query = """SELECT t.*, a.wallex_api_key, a.user_telegram_id 
               FROM trade_ops t JOIN trading_accounts a ON t.account_id=a.account_id 
               WHERE t.status='BUY_FILLED'"""
    orders = db_manager.execute_query(query, fetch='all')
    
    for o in orders:
        try:
            symbol = f"{o['asset_name']}{o['pair']}"
            
            # --- مقادیر دقیق و فرمت شده قبلی را استفاده می‌کنیم ---
            sell_qty = float(o['buy_quantity_executed']) # این مقدار از قبل خالص و فرمت شده است
            raw_price = float(o['exit_price'])
            
            # دریافت دقت قیمت و فرمت کردن قیمت فروش
            _, price_prec = wallex_api.get_precision(symbol)
            sell_price = wallex_api.format_price(raw_price, price_prec)
            
            logging.info(f"⬇️ Placing Sell {symbol} | P: {sell_price} | Q: {sell_qty}")
            
            res = wallex_api.place_order(o['wallex_api_key'], symbol, 'sell', sell_price, sell_qty)
            
            if res and res.get('success'):
                sid = res['result']['clientOrderId']
                db_manager.execute_query(
                    "UPDATE trade_ops SET status='SELL_IN_PROGRESS', sell_client_order_id=%s, notes='Sell Placed' WHERE id=%s", 
                    (sid, o['id'])
                )
                send_telegram_alert(o['user_telegram_id'], f"⬇️ **سفارش فروش ثبت شد**\n🎯 تارگت: `{sell_price}`")
            else:
                err = res.get('message') if res else 'API Error'
                logging.error(f"Sell Place Failed: {err}")
                db_manager.execute_query("UPDATE trade_ops SET notes=%s WHERE id=%s", (f"Sell Place Fail: {err}", o['id']))

        except Exception as e: logging.error(f"Step 3: {e}")

# --- Step 4, 5, and run_executor remain unchanged ---
# ... (کدهای check_sell_fill و cleanup و run_executor) ...

def run_executor():
    logging.info("🚀 Executor V14 (Fee Deduction) Started...")
    wallex_api.update_market_info()
    while True:
        try:
            step_1_place_buy()
            step_2_check_buy_fill()
            step_3_place_sell()
            step_4_check_sell_fill()
            step_5_cleanup()
        except Exception as e: logging.error(f"Loop Error: {e}")
        time.sleep(config.BOT_SETTINGS["CHECK_INTERVAL"])

if __name__ == "__main__":
    run_executor()
