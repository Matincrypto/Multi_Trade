# executor.py
import time
import logging
import config
import db_manager
import wallex_api
from decimal import Decimal

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def process_new_signals():
    """مرحله ۱: پردازش سیگنال‌های جدید و ثبت سفارش خرید"""
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
            # تشخیص نوع جفت ارز و بودجه
            pair = sig['pair']
            asset = sig['asset_name']
            symbol = f"{asset}{pair}"  # مثلا BTCTMN یا BTCUSDT
            
            budget = 0
            if pair == 'TMN':
                budget = sig['trade_amount_tmn']
            elif pair == 'USDT':
                budget = sig['trade_amount_usdt']
            
            if budget <= 0:
                logging.warning(f"User {sig['account_id']}: No budget for {pair}. Skipping.")
                continue

            # محاسبه مقدار خرید
            # نکته: ما قیمت را از سیگنال داریم، اما بهتر است قیمت لحظه‌ای را برای اوردر دقیق چک کنیم
            # اینجا فعلا از قیمت سیگنال استفاده میکنیم یا قیمت بازار
            price = sig['entry_price']
            
            # محاسبه حجم: مقدار پول تقسیم بر قیمت
            quantity_raw = float(budget) / float(price)
            
            # رند کردن طبق قوانین والکس
            # (برای سادگی فعلا تا 4 رقم اعشار رند میکنیم، در نسخه پیشرفته باید از api قوانین گرفت)
            quantity = wallex_api.format_quantity(quantity_raw, precision=4)
            
            # ثبت سفارش خرید
            result = wallex_api.place_order(
                api_key=sig['wallex_api_key'],
                symbol=symbol,
                side='buy',
                price=price,
                quantity=quantity
            )
            
            if result and result.get('success'):
                order_id = result['result']['clientOrderId']
                logging.info(f"✅ Buy Placed: {symbol} | User: {sig['account_id']} | ID: {order_id}")
                
                db_manager.execute_query(
                    """
                    UPDATE trade_ops SET 
                    status='BUY_ORDER_PLACED', 
                    buy_client_order_id=%s,
                    buy_quantity_formatted=%s,
                    invested_tmn=%s 
                    WHERE id=%s
                    """,
                    (order_id, quantity, budget, sig['id'])
                )
            else:
                logging.error(f"❌ Buy Failed: {symbol} | User: {sig['account_id']}")

        except Exception as e:
            logging.error(f"Error processing signal {sig['id']}: {e}")

def check_buy_orders():
    """مرحله ۲: بررسی وضعیت سفارشات خرید"""
    query = """
    SELECT t.*, a.wallex_api_key 
    FROM trade_ops t
    JOIN trading_accounts a ON t.account_id = a.account_id
    WHERE t.status = 'BUY_ORDER_PLACED'
    """
    orders = db_manager.execute_query(query, fetch='all')
    
    for order in orders:
        status_data = wallex_api.get_order_status(order['buy_client_order_id'], order['wallex_api_key'])
        if status_data and status_data.get('status') == 'FILLED':
            # خرید کامل شد -> ثبت سفارش فروش
            executed_qty = status_data.get('executedQty')
            logging.info(f"💰 Buy Filled: {order['asset_name']} | Qty: {executed_qty}")
            
            # آپدیت وضعیت به انجام شده
            db_manager.execute_query(
                "UPDATE trade_ops SET status='BUY_ORDER_FILLED', buy_executed_quantity=%s WHERE id=%s",
                (executed_qty, order['id'])
            )
            
            # بلافاصله سفارش فروش را می‌گذاریم (Limit Sell)
            place_sell_order(order, executed_qty)

def place_sell_order(order, qty):
    """مرحله ۳: ثبت سفارش فروش (تارگت)"""
    symbol = f"{order['asset_name']}{order['pair']}"
    price = order['exit_price']
    
    result = wallex_api.place_order(
        api_key=order['wallex_api_key'],
        symbol=symbol,
        side='sell',
        price=price,
        quantity=qty
    )
    
    if result and result.get('success'):
        sell_id = result['result']['clientOrderId']
        logging.info(f"🎯 Sell Order Placed: {symbol} at {price}")
        db_manager.execute_query(
            "UPDATE trade_ops SET status='SELL_ORDER_PLACED', sell_client_order_id=%s WHERE id=%s",
            (sell_id, order['id'])
        )

def check_sell_orders():
    """مرحله ۴: بررسی وضعیت فروش‌ها (اتمام چرخه)"""
    query = """
    SELECT t.*, a.wallex_api_key 
    FROM trade_ops t
    JOIN trading_accounts a ON t.account_id = a.account_id
    WHERE t.status = 'SELL_ORDER_PLACED'
    """
    orders = db_manager.execute_query(query, fetch='all')
    
    for order in orders:
        status_data = wallex_api.get_order_status(order['sell_client_order_id'], order['wallex_api_key'])
        if status_data and status_data.get('status') == 'FILLED':
            revenue = status_data.get('cummulativeQuoteQty') # مبلغ دریافتی کل
            logging.info(f"🏁 Trade Completed: {order['asset_name']} | Revenue: {revenue}")
            
            db_manager.execute_query(
                "UPDATE trade_ops SET status='SELL_ORDER_FILLED', sell_revenue_tmn=%s WHERE id=%s",
                (revenue, order['id'])
            )

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
