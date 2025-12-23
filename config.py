# config.py
# تنظیمات نهایی و هماهنگ شده با Swagger والکس

# 1. دیتابیس استخر سیگنال (منبع خواندن سیگنال‌ها)
SIGNAL_POOL_DB = {
    'host': 'localhost',
    'user': 'root',
    'password': 'YourStrongPassword123!',
    'database': 'signal_pool'
}

# 2. دیتابیس مدیریت ربات (ذخیره کاربران و تریدها)
INTERNAL_DB = {
    'host': 'localhost',
    'user': 'root',
    'password': 'YourStrongPassword123!',
    'database': 'multi_trade'
}

# 3. تنظیمات تلگرام
TELEGRAM = {
    # توکن ربات شما
    "BOT_TOKEN": "8376582196:AAH7NWY8rq1SW07oolf7qjYoMTU3myyUprs",
    # آیدی عددی ادمین برای دریافت لاگ‌های خاص (اختیاری)
    "ADMIN_ID": 0
}

# 4. تنظیمات صرافی والکس (طبق مستندات Swagger جدید)
WALLEX = {
    "BASE_URL": "https://api.wallex.ir",
    "ENDPOINTS": {
        # آدرس صحیح ثبت و لغو سفارش (طبق spot-swagger)
        "ORDERS": "/v1/account/orders",
        
        # آدرس دریافت وضعیت سفارش (شناسه سفارش بعداً به ته این اضافه می‌شود)
        "GET_ORDER": "/v1/account/orders/",
        
        # آدرس دریافت موجودی حساب (طبق basicServices-swagger)
        "ACCOUNT_BALANCES": "/v1/account/balances",
        
        # آدرس دریافت لیست تمام بازارها
        "ALL_MARKETS": "/hector/web/v1/markets"
    },
    "QUOTE_ASSET": "TMN" # پیش‌فرض (در کد به صورت داینامیک هم هندل می‌شود)
}

# 5. تنظیمات کلی ربات
BOT_SETTINGS = {
    "LOG_LEVEL": "INFO",             # سطح لاگ‌گیری
    "SIGNAL_LOOKBACK_MINUTES": 5,    # سیگنال‌های ۵ دقیقه اخیر بررسی شوند
    "CHECK_INTERVAL": 3,             # فاصله زمانی بین هر سیکل اجرا (ثانیه)
    "STALE_ORDER_MINUTES": 15        # لغو سفارش خرید اگر بعد از ۱۵ دقیقه پر نشد
}
