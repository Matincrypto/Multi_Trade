# config.py

# 1. دیتابیس استخر سیگنال (منبع)
SIGNAL_POOL_DB = {
    'host': 'localhost',
    'user': 'root',          
    'password': 'YourStrongPassword123!', 
    'database': 'signal_pool' 
}

# 2. دیتابیس مدیریت ربات (مقصد)
INTERNAL_DB = {
    'host': 'localhost',
    'user': 'root',
    'password': 'YourStrongPassword123!',
    'database': 'multi_trade'
}

# 3. تنظیمات تلگرام
TELEGRAM = {
    "BOT_TOKEN": "8376582196:AAH7NWY8rq1SW07oolf7qjYoMTU3myyUprs",
    "ADMIN_ID":  119385059  # آیدی عددی شما برای دریافت لاگ‌های خطای سیستم (اختیاری)
}

# 4. تنظیمات صرافی
WALLEX = {
    "BASE_URL": "https://api.wallex.ir/v1",
    "ENDPOINTS": {
        "ORDERS": "/orders",
        "GET_ORDER": "/orders/",
        "ACCOUNT_BALANCES": "/account/balances",
        "ALL_MARKETS": "/markets"
    }
    # QUOTE_ASSET حذف شد -> از دیتابیس سیگنال خوانده می‌شود
}

# 5. تنظیمات کلی
BOT_SETTINGS = {
    "LOG_LEVEL": "INFO",
    "SIGNAL_LOOKBACK_MINUTES": 10,
    "CHECK_INTERVAL": 10
}
