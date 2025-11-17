# executor.py
import time
import logging
import config
import db_manager
import wallex_api
from decimal import Decimal

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def process_new_signals():
    """مرحله ۱: بررسی سیگنال‌های جدید و ثبت سفارش خرید"""
    # فقط سیگنال‌هایی که هنوز NEW_SIGNAL هستند و حساب کاربر فعال است
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
            symbol = f"{asset}{pair}"  # مثال: BTCUSDT یا BTCTMN
            
            # انتخاب بودجه بر اساس نوع جفت ارز
            budget = sig['trade_amount_tmn'] if pair == 'TMN' else sig['trade_amount_usdt']
            
            # اگر بودجه صفر بود، یعنی کاربر نمی‌خواهد این جفت ارز را ترید کند
            if budget <= 0:
                logging.warning(f"Skipping {symbol} for User {sig['account_id']}: Budget is 0")
                # وضعیت را به ERROR تغییر می‌دهیم که دوباره چک نشود
                db_manager.execute_query("UPDATE trade_ops SET status='SKIPPED_NO_BUDGET' WHERE id=%s", (sig['id'],))
                continue

            price = float(sig['entry_price'])
            
            # محاسبه مقدار خرید (Budget / Price)
            quantity_raw = float(budget) / price
            
            # رند کردن تا ۴ رقم اعشار (استاندارد معمول)
            quantity = wallex_api.format_quantity(quantity_raw, precision=4)
            
            # ثبت سفارش خرید در والکس
            logging.info(f"Buying {symbol}... Price: {price}, Qty: {quantity}, Budget: {budget}")
            result = wallex_api.place_order(sig['wallex_api_key'], symbol, 'buy', price, quantity)
            
            if result and result.get('success'):
                order_id = result['result']['clientOrderId']
                logging.info(f"✅ Buy Order Placed: {symbol} | ID: {order_id}")
                
                # آپدیت دیتابیس
                db_manager.execute_query(
                    """
                    UPDATE trade_ops SET 
                    status='BUY_ORDER_PLACED', 
                    buy_client_order_id=%s, 
                    buy_quantity_formatted=%s, 
                    invested_amount=%s 
                    WHERE id=%s
                    """,
                    (order_id, quantity, budget, sig['id'])
                )
            else:
                logging.error(f"❌ Buy Failed for {symbol}. Marking as ERROR.")
                db_manager.execute_query("UPDATE trade_ops SET status='ERROR', notes='API Buy Failed' WHERE id=%s", (sig['id'],))

        except Exception as e:
            logging.error(f"Error in process_new_signals (ID: {sig['id']}): {e}")

def check_buy_orders():
    """مرحله ۲: بررسی اینکه آیا خرید انجام شده است؟"""
    orders = db_manager.execute_query(
        """
        SELECT t.*, a.wallex_api_key 
        FROM trade_ops t 
        JOIN trading_accounts a ON t.account_id = a.account_id 
        WHERE t.status = 'BUY_ORDER_PLACED'
        """,
        fetch='all'
    )
    
    if not orders: return

    for order in orders:
        status_data = wallex_api.get_order_status(order['buy_client_order_id'], order['wallex_api_key'])
        
        if status_data and status_data.get('status') == 'FILLED':
            executed_qty = status_data.get('executedQty')
            logging.info(f"💰 Buy Filled: {order['asset_name']} | Qty: {executed_qty}")
            
            # ذخیره مقدار واقعی خرید
            db_manager.execute_query(
                "UPDATE trade_ops SET status='BUY_ORDER_FILLED', buy_quantity_executed=%s WHERE id=%s",
                (executed_qty, order['id'])
            )
            
            # === مرحله ۳: ثبت فوری سفارش فروش ===
            place_sell_order(order, executed_qty)

def place_sell_order(order, qty):
    """ثبت سفارش فروش (تارگت)"""
    symbol = f"{order['asset_name']}{order['pair']}"
    price = order['exit_price']
    
    logging.info(f"Placing Sell Target for {symbol} at {price}...")
    result = wallex_api.place_order(
        order['wallex_api_key'], 
        symbol, 
        'sell', 
        price, 
        qty
    )
    
    if result and result.get('success'):
        sell_id = result['result']['clientOrderId']
        logging.info(f"🎯 Sell Order Placed: {symbol} | ID: {sell_id}")
        db_manager.execute_query(
            "UPDATE trade_ops SET status='SELL_ORDER_PLACED', sell_client_order_id=%s WHERE id=%s",
            (sell_id, order['id'])
        )
    else:
        logging.error(f"❌ Failed to place Sell Order for {symbol}")
        # اینجا ارور نمی‌کنیم، چون دارایی خریده شده. باید دستی بررسی شود یا ربات دوباره تلاش کند
        db_manager.execute_query("UPDATE trade_ops SET notes='Sell API Failed - Check Manually' WHERE id=%s", (order['id'],))

def check_sell_orders():
    """مرحله ۴: بررسی وضعیت فروش و ذخیره سود"""
    orders = db_manager.execute_query(
        """
        SELECT t.*, a.wallex_api_key 
        FROM trade_ops t 
        JOIN trading_accounts a ON t.account_id = a.account_id 
        WHERE t.status = 'SELL_ORDER_PLACED'
        """,
        fetch='all'
    )
    
    if not orders: return

    for order in orders:
        res = wallex_api.get_order_status(order['sell_client_order_id'], order['wallex_api_key'])
        
        if res and res.get('status') == 'FILLED':
            revenue = res.get('cummulativeQuoteQty') # کل مبلغ دریافتی (تومان یا تتر)
            logging.info(f"🏁 Trade Cycle Complete: {order['asset_name']} | Revenue: {revenue}")
            
            db_manager.execute_query(
                "UPDATE trade_ops SET status='SELL_ORDER_FILLED', sell_revenue=%s WHERE id=%s",
                (revenue, order['id'])
            )

def cleanup_stale_orders():
    """مرحله ۵: پاکسازی سفارشات خریدی که مدت زیادی باز مانده‌اند"""
    # خواندن زمان تایم‌اوت از کانفیگ (پیش‌فرض ۱۵ دقیقه)
    timeout_mins = config.BOT_SETTINGS.get("STALE_ORDER_MINUTES", 15)
    
    query = """
    SELECT t.*, a.wallex_api_key 
    FROM trade_ops t
    JOIN trading_accounts a ON t.account_id = a.account_id
    WHERE t.status = 'BUY_ORDER_PLACED' 
    AND t.updated_at < (NOW() - INTERVAL %s MINUTE)
    """
    stale_orders = db_manager.execute_query(query, (timeout_mins,), fetch='all')
    
    if not stale_orders: return

    for order in stale_orders:
        logging.warning(f"⏳ Order {order['buy_client_order_id']} is stale ({timeout_mins} mins). Canceling...")
        
        # تلاش برای لغو در صرافی
        res = wallex_api.cancel_order(order['wallex_api_key'], order['buy_client_order_id'])
        
        # اگر لغو موفق بود یا ارور داد که "سفارش وجود ندارد" (یعنی قبلا کنسل شده یا پر شده)
        if (res and res.get('success')) or (res is None): 
            # وضعیت را در دیتابیس به کنسل شده تغییر می‌دهیم
            db_manager.execute_query(
                "UPDATE trade_ops SET status='CANCELED_TIMEOUT', notes='Auto canceled by bot' WHERE id=%s",
                (order['id'],)
            )
            logging.info(f"❌ Order {order['id']} status updated to CANCELED_TIMEOUT.")

def run_executor():
    """حلقه اصلی اجرا"""
    logging.info("🚀 Executor Engine Started (Multi-Currency Support)...")
    
    while True:
        try:
            # 1. سیگنال‌های جدید را بخر
            process_new_signals()
            
            # 2. وضعیت خریدهای قبلی را چک کن
            check_buy_orders()
            
            # 3. وضعیت فروش‌های قبلی را چک کن
            check_sell_orders()
            
            # 4. سفارشات گیر کرده را پاک کن
            cleanup_stale_orders()
            
        except Exception as e:
            logging.error(f"Critical Loop Error: {e}")
        
        # وقفه کوتاه تا سیکل بعدی
        time.sleep(config.BOT_SETTINGS["CHECK_INTERVAL"])

if __name__ == "__main__":
    run_executor()
