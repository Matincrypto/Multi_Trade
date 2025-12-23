# wallex_api.py
# نسخه دقیق: دریافت اطلاعات از API (بدون حدس زدن)

import requests
import logging
import json
import config
import math
from decimal import Decimal

# حافظه کش برای نگهداری اطلاعات دقیق بازار
MARKET_INFO_CACHE = {}

def get_url(endpoint):
    base = config.WALLEX["BASE_URL"].rstrip('/')
    path = endpoint.lstrip('/')
    return f"{base}/{path}"

def update_market_info():
    """
    دریافت لیست کامل بازارها و دقت اعشار از API والکس
    Endpoint: /hector/web/v1/markets
    """
    global MARKET_INFO_CACHE
    url = get_url(config.WALLEX["ENDPOINTS"]["ALL_MARKETS"])
    
    try:
        logging.info("🔄 Fetching ALL market precisions from Wallex API...")
        resp = requests.get(url, timeout=20)
        
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success") and "result" in data:
                markets = data["result"]["markets"]
                
                # پاکسازی کش قبلی برای اطمینان از تازگی داده‌ها
                MARKET_INFO_CACHE.clear()
                
                for m in markets:
                    symbol = m["symbol"]
                    # دریافت دقیق مقادیر از API
                    # نکته: در جیسون شما amount_precision و price_precision وجود دارد
                    amt_p = m.get("amount_precision")
                    prc_p = m.get("price_precision")
                    
                    if amt_p is not None and prc_p is not None:
                        MARKET_INFO_CACHE[symbol] = {
                            "qty_prec": int(amt_p),
                            "price_prec": int(prc_p)
                        }
                
                logging.info(f"✅ Market Info Loaded: {len(MARKET_INFO_CACHE)} pairs cached.")
                return True
            else:
                logging.error(f"API Response Error: {data}")
        else:
            logging.error(f"HTTP Error fetching markets: {resp.status_code}")
            
    except Exception as e:
        logging.error(f"Connection Error updating markets: {e}")
    
    return False

def get_precision(symbol):
    """
    جستجوی دقت در کش. اگر نبود، آپدیت می‌کند.
    اگر باز هم نبود، None برمی‌گرداند (ترید انجام نشود).
    """
    # اگر کش خالی است یا نماد در کش نیست، یکبار آپدیت کن
    if not MARKET_INFO_CACHE or symbol not in MARKET_INFO_CACHE:
        update_market_info()
    
    info = MARKET_INFO_CACHE.get(symbol)
    
    if info:
        return info["qty_prec"], info["price_prec"]
    
    # اگر پیدا نشد، یعنی این ارز در مارکت والکس نیست یا API مشکل دارد
    logging.warning(f"⚠️ Precision not found for {symbol} in API data.")
    return None, None

def format_quantity(quantity, precision):
    """گرد کردن مقدار (Quantity) دقیقاً با تعداد اعشار API"""
    if precision is None: return None
    
    d_qty = Decimal(str(quantity))
    factor = Decimal(10) ** precision
    # همیشه به پایین گرد میکنیم تا موجودی کم نیاید
    return float(math.floor(d_qty * factor) / factor)

def format_price(price, precision):
    """
    گرد کردن قیمت (Price) دقیقاً با تعداد اعشار API.
    اگر precision=0 باشد، int برمی‌گرداند.
    """
    if precision is None: return None
    
    d_price = Decimal(str(price))
    
    if precision == 0:
        return int(d_price) # حذف کامل اعشار
    
    factor = Decimal(10) ** precision
    # برای قیمت معمولاً گرد کردن معمولی یا به پایین (برای خرید) استفاده می‌شود
    # اینجا به پایین گرد میکنیم تا قیمت پرت نشود
    return float(math.floor(d_price * factor) / factor)

# --- تابع‌های سفارش‌گذاری (تغییر نکرده‌اند اما از توابع بالا استفاده می‌کنند) ---

def validate_api_key(api_key):
    url = get_url(config.WALLEX["ENDPOINTS"]["ACCOUNT_BALANCES"])
    headers = {"x-api-key": api_key}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        return r.status_code == 200 and r.json().get("success")
    except: return False

def place_order(api_key, symbol, side, price, quantity):
    url = get_url(config.WALLEX["ENDPOINTS"]["ORDERS"])
    headers = {"Content-Type": "application/json", "x-api-key": api_key}
    
    # تبدیل به رشته برای ارسال امن
    str_price = str(price)
    # حذف صفرهای اضافه برای مقدار (مثلا 12.500 -> 12.5)
    str_qty = f"{quantity:.10f}".rstrip('0').rstrip('.') 
    
    payload = {
        "symbol": symbol,
        "price": str_price,
        "quantity": str_qty,
        "side": side.upper(),
        "type": "LIMIT"
    }
    
    logging.info(f"📤 Sending {symbol} | P: {str_price} | Q: {str_qty}")
    
    try:
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        if resp.status_code in [200, 201]: return resp.json()
        
        logging.error(f"❌ Order Failed: {resp.text}")
        return {"success": False, "message": resp.text}
    except Exception as e:
        logging.error(f"Exception Place Order: {e}")
        return None

def get_order_status(client_id, api_key):
    base = config.WALLEX["ENDPOINTS"]["GET_ORDER"]
    url = get_url(f"{base}{client_id}")
    headers = {"x-api-key": api_key}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200: return r.json().get("result")
        return None
    except: return None

def cancel_order(api_key, client_id):
    url = get_url(config.WALLEX["ENDPOINTS"]["ORDERS"])
    headers = {"Content-Type": "application/json", "x-api-key": api_key}
    payload = {"clientOrderId": client_id}
    try:
        r = requests.delete(url, headers=headers, data=json.dumps(payload), timeout=10)
        if r.status_code == 200: return r.json()
        return None
    except: return None
