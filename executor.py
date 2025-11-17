# executor.py
import time
import logging
import config
import db_manager
import wallex_api
from decimal import Decimal

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def process_new_signals():
    """مرحله ۱: خرید"""
    query = """
    SELECT t.*, a.wallex_api_key, a.trade_amount_tmn, a.trade_amount_usdt 
    FROM trade_ops t
    JOIN trading_accounts a ON t.account_id = a.account_id
    WHERE t.status = 'NEW_SIGNAL' AND a.is_active = TRUE
    """
    signals = db_manager.execute_query(query, fetch='all')
    if not signals: return

    for sig in signals:
        try:
            pair = sig['pair']
            asset = sig['asset_name']
            symbol = f"{asset}{pair}" # ساخت نماد: ADATMN یا ADAUSDT
            
            # انتخاب بودجه بر اساس نوع جفت ارز
            budget = sig['trade_amount_tmn'] if pair == 'TMN' else sig['trade_amount_usdt']
            
            if budget <= 0: continue

            price = float(sig['entry_price'])
            quantity_raw = float(budget) / price
            quantity = wallex_api.format_quantity(quantity_raw, precision=4) # رند کردن
            
            # ارسال سفارش به والکس
            result = wallex_api.place_order(sig['wallex_api_key'], symbol, 'buy', price, quantity)
            
            if result and result.get('success'):
                order_id = result['result']['clientOrderId']
                logging.info(f"✅ Buy Placed: {symbol} | User: {sig['account_id']}")
                db_manager.execute_query(
                    "UPDATE trade_ops SET status='BUY_ORDER_PLACED', buy_client_order_id=%s, buy_quantity_formatted=%s, invested_amount=%s WHERE id=%s",
                    (order_id, quantity, budget, sig['id'])
                )
            else:
                logging.error(f"❌ Buy Failed: {symbol} | Resp: {result}")
                db_manager.execute_query("UPDATE trade_ops SET status='ERROR', notes='Buy API Failed' WHERE id=%s", (sig['id'],))

        except Exception as e:
            logging.error(f"Error in process_new_signals: {e}")

def check_buy_orders():
    """مرحله ۲: چک کردن خرید"""
    orders = db_manager.execute_query(
        "SELECT t.*, a.wallex_api_key FROM trade_ops t JOIN trading_accounts a ON t.account_id=a.account_id WHERE t.status='BUY_ORDER_PLACED'",
        fetch='all'
    )
    for order in orders:
        res = wallex_api.get_order_status(order['buy_client_order_id'], order['wallex_api_key'])
        if res and res.get('status') == 'FILLED':
            qty = res.get('executedQty')
            logging.info(f"💰 Buy Filled: {order['asset_name']} | Qty: {qty}")
            db_manager.execute_query("UPDATE trade_ops SET status='BUY_ORDER_FILLED', buy_quantity_executed=%s WHERE id=%s", (qty, order['id']))
            
            # بلافاصله سفارش فروش (مرحله ۳)
            symbol = f"{order['asset_name']}{order['pair']}"
            sell_res = wallex_api.place_order(order['wallex_api_key'], symbol, 'sell', order['exit_price'], qty)
            if sell_res and sell_res.get('success'):
                sid = sell_res['result']['clientOrderId']
                logging.info(f"🎯 Sell Placed: {symbol}")
                db_manager.execute_query("UPDATE trade_ops SET status='SELL_ORDER_PLACED', sell_client_order_id=%s WHERE id=%s", (sid, order['id']))

def check_sell_orders():
    """مرحله ۴: چک کردن فروش و پایان"""
    orders = db_manager.execute_query(
        "SELECT t.*, a.wallex_api_key FROM trade_ops t JOIN trading_accounts a ON t.account_id=a.account_id WHERE t.status='SELL_ORDER_PLACED'",
        fetch='all'
    )
    for order in orders:
        res = wallex_api.get_order_status(order['sell_client_order_id'], order['wallex_api_key'])
        if res and res.get('status') == 'FILLED':
            revenue = res.get('cummulativeQuoteQty')
            logging.info(f"🏁 Trade Complete: {order['asset_name']} | Revenue: {revenue}")
            db_manager.execute_query("UPDATE trade_ops SET status='SELL_ORDER_FILLED', sell_revenue=%s WHERE id=%s", (revenue, order['id']))

def run_executor():
    logging.info("🚀 Executor Engine Started...")
    while True:
        try:
            process_new_signals()
            check_buy_orders()
            check_sell_orders()
        except Exception as e:
            logging.error(f"Executor Loop Error: {e}")
        time.sleep(config.BOT_SETTINGS["CHECK_INTERVAL"])

if __name__ == "__main__":
    run_executor()
