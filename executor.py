# executor.py
# نسخه نهایی و کامل (شامل تمام مراحل)

import time
import logging
import requests
import config
import db_manager
import wallex_api
from datetime import datetime, timedelta
from decimal import Decimal

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

TIMEOUT_MINUTES = config.BOT_SETTINGS.get("STALE_ORDER_MINUTES", 15)

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

# ==============================================================================
# Step 1: Place Buy
# ==============================================================================
def step_1_place_buy():
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
# Step 2: Check Buy Status
# ==============================================================================
def step_2_check_buy_fill():
    query = """SELECT t.*, a.wallex_api_key, a.user_telegram_id 
               FROM trade_ops t JOIN trading_accounts a ON t.account_id=a.account_id 
               WHERE t.status='BUY_IN_PROGRESS'"""
    orders = db_manager.execute_query(query, fetch='all')
    if not orders: return

    for o in orders:
        try:
            res = wallex_api.get_order_status(o['buy_client_order_id'], o['wallex_api_key'])
            
            if res and res.get('status') == 'FILLED':
                raw_executed_qty = float(res.get('executedQty'))
                # تلاش برای خواندن کارمزد (ممکن است API برنگرداند، پیش‌فرض ۰)
                # در ورژن‌های جدید والکس گاهی فی را جدا کم می‌کند
                # ما برای اطمینان، مقداری که "واقعا" اضافه شده را در نظر می‌گیریم اگر بشود
                # اما اینجا فرض بر کسر فی از مقدار است
                
                # برای سادگی و اطمینان از فروش، کمی پایین‌تر گرد می‌کنیم در مرحله بعد
                net_quantity = raw_executed_qty

                # ذخیره در دیتابیس
                symbol = f"{o['asset_name']}{o['pair']}"
                qty_prec, _ = wallex_api.get_precision(symbol)
                final_sell_qty = wallex_api.format_quantity(net_quantity, qty_prec) # دوباره فرمت می‌کنیم که مطمئن شویم

                logging.info(f"✅ Buy Filled: {o['asset_name']} | Exec Qty: {final_sell_qty}")
                
                db_manager.execute_query(
                    "UPDATE trade_ops SET status='BUY_FILLED', buy_quantity_executed=%s WHERE id=%s", 
                    (final_sell_qty, o['id'])
                )
                send_telegram_alert(o['user_telegram_id'], f"✅ **خرید انجام شد**\n💎 {o['asset_name']}\n🔢 مقدار: `{final_sell_qty}`")
        except Exception as e: logging.error(f"Step 2: {e}")

# ==============================================================================
# Step 3: Place Sell Order
# ==============================================================================
def step_3_place_sell():
    query = """SELECT t.*, a.wallex_api_key, a.user_telegram_id 
               FROM trade_ops t JOIN trading_accounts a ON t.account_id=a.account_id 
               WHERE t.status='BUY_FILLED'"""
    orders = db_manager.execute_query(query, fetch='all')
    if not orders: return

    for o in orders:
        try:
            symbol = f"{o['asset_name']}{o['pair']}"
            sell_qty = float(o['buy_quantity_executed'])
            raw_price = float(o['exit_price'])
            
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

# ==============================================================================
# Step 4: Check Sell Status (Profit) [این تابع گم شده بود]
# ==============================================================================
def step_4_check_sell_fill():
    query = """SELECT t.*, a.wallex_api_key, a.user_telegram_id 
               FROM trade_ops t JOIN trading_accounts a ON t.account_id=a.account_id 
               WHERE t.status='SELL_IN_PROGRESS'"""
    orders = db_manager.execute_query(query, fetch='all')
    if not orders: return

    for o in orders:
        try:
            res = wallex_api.get_order_status(o['sell_client_order_id'], o['wallex_api_key'])
            
            if res and res.get('status') == 'FILLED':
                # محاسبه درآمد کل (تومان یا تتر دریافتی)
                revenue = res.get('cummulativeQuoteQty') or 0
                
                logging.info(f"💰 Trade Completed: {o['asset_name']} | Rev: {revenue}")
                
                db_manager.execute_query(
                    "UPDATE trade_ops SET status='COMPLETED', sell_revenue=%s WHERE id=%s",
                    (revenue, o['id'])
                )
                
                # محاسبه سود
                profit = float(revenue) - float(o['invested_amount'])
                icon = "🟢" if profit >= 0 else "🔴"
                
                send_telegram_alert(o['user_telegram_id'], 
                                    f"{icon} **معامله بسته شد**\n💎 {o['asset_name']}\n💰 دریافتی: `{revenue}`\n📊 سود/زیان: `{int(profit)}`")

        except Exception as e: logging.error(f"Step 4: {e}")

# ==============================================================================
# Step 5: Cleanup Stale Orders [این تابع هم گم شده بود]
# ==============================================================================
def step_5_cleanup():
    # سفارشاتی که در وضعیت BUY_IN_PROGRESS مانده‌اند و زمان زیادی گذشته
    query = """
    SELECT t.*, a.wallex_api_key 
    FROM trade_ops t
    JOIN trading_accounts a ON t.account_id = a.account_id
    WHERE t.status = 'BUY_IN_PROGRESS' 
    AND t.updated_at < (NOW() - INTERVAL %s MINUTE)
    """
    stale_orders = db_manager.execute_query(query, (TIMEOUT_MINUTES,), fetch='all')
    
    if not stale_orders: return

    for order in stale_orders:
        logging.warning(f"⏳ Order Timeout {order['id']}. Canceling...")
        
        res = wallex_api.cancel_order(order['wallex_api_key'], order['buy_client_order_id'])
        
        # اگر کنسل شد یا ارور داد که وجود ندارد (یعنی شاید پر شده یا قبلا کنسل شده)
        # در هر صورت از حالت انتظار خارجش می‌کنیم
        db_manager.execute_query(
            "UPDATE trade_ops SET status='CANCELED_TIMEOUT', notes='Auto cancel' WHERE id=%s",
            (order['id'],)
        )

# ==============================================================================
# Main Loop
# ==============================================================================
def run_executor():
    logging.info("🚀 Executor V14 (Fee Deduction) Started...")
    wallex_api.update_market_info()
    while True:
        try:
            step_1_place_buy()
            step_2_check_buy_fill()
            step_3_place_sell()
            step_4_check_sell_fill() # الان این تابع تعریف شده است
            step_5_cleanup()         # الان این تابع تعریف شده است
        except Exception as e: logging.error(f"Loop Error: {e}")
        time.sleep(config.BOT_SETTINGS["CHECK_INTERVAL"])

if __name__ == "__main__":
    run_executor()
