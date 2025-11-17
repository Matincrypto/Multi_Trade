# telegram_bot.py
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ConversationHandler, MessageHandler, filters, ContextTypes
)
import config
import db_manager
import wallex_api
from decimal import Decimal

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(message)s')

# مراحل مکالمه (اضافه شدن مرحله تتری)
STATE_GET_NAME, STATE_GET_API, STATE_GET_AMOUNT_TMN, STATE_GET_AMOUNT_USDT = range(4)

# --- دستورات اصلی ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # ذخیره اطلاعات کاربر در دیتابیس اختصاصی multi_trade -> جدول users
    db_manager.execute_query(
        """
        INSERT INTO users (telegram_id, first_name, username) 
        VALUES (%s, %s, %s) 
        ON DUPLICATE KEY UPDATE first_name=%s, username=%s
        """,
        (user.id, user.first_name, user.username, user.first_name, user.username)
    )
    await show_main_menu(update)

async def show_main_menu(update: Update):
    keyboard = [
        [InlineKeyboardButton("➕ افزودن حساب جدید", callback_data="add_account")],
        [InlineKeyboardButton("⚙️ مدیریت حساب‌ها", callback_data="manage_accounts")],
    ]
    text = "👋 به ربات MultiTrade خوش آمدید.\nلطفاً گزینه مورد نظر را انتخاب کنید:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# --- پروسه افزودن حساب ---

async def add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("1️⃣ لطفاً یک **نام** برای این حساب انتخاب کنید:")
    return STATE_GET_NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['acc_name'] = update.message.text
    await update.message.reply_text("2️⃣ لطفاً **API Key** صرافی والکس را ارسال کنید:")
    return STATE_GET_API

async def get_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api_key = update.message.text.strip()
    msg = await update.message.reply_text("⏳ در حال بررسی اعتبار API Key...")
    
    if wallex_api.validate_api_key(api_key):
        context.user_data['acc_api'] = api_key
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg.message_id)
        await update.message.reply_text(
            "✅ API Key تایید شد.\n\n"
            "3️⃣ برای سیگنال‌های **تومانی** (مثل BTC/TMN) چقدر خرید انجام شود؟\n"
            "(عدد به تومان وارد کنید، مثلاً: 500000)"
        )
        return STATE_GET_AMOUNT_TMN
    else:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg.message_id)
        await update.message.reply_text("❌ API Key نامعتبر است. مجدد تلاش کنید.")
        return STATE_GET_API

async def get_amount_tmn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = Decimal(update.message.text)
        if amount < 50000:
            await update.message.reply_text("حداقل خرید تومانی باید ۵۰,۰۰۰ باشد.")
            return STATE_GET_AMOUNT_TMN
        
        context.user_data['amt_tmn'] = amount
        await update.message.reply_text(
            "4️⃣ برای سیگنال‌های **تتری** (مثل ETH/USDT) چقدر خرید انجام شود؟\n"
            "(عدد به تتر وارد کنید، مثلاً: 10 یا 0 اگر نمیخواهید)"
        )
        return STATE_GET_AMOUNT_USDT
    except ValueError:
        await update.message.reply_text("فقط عدد وارد کنید.")
        return STATE_GET_AMOUNT_TMN

async def get_amount_usdt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount_usdt = Decimal(update.message.text)
        data = context.user_data
        user_id = update.effective_user.id

        # ذخیره نهایی در دیتابیس trading_accounts
        db_manager.execute_query(
            """
            INSERT INTO trading_accounts 
            (user_telegram_id, account_name, wallex_api_key, trade_amount_tmn, trade_amount_usdt, is_active)
            VALUES (%s, %s, %s, %s, %s, FALSE)
            """,
            (user_id, data['acc_name'], data['acc_api'], data['amt_tmn'], amount_usdt)
        )
        
        await update.message.reply_text(f"✅ حساب '{data['acc_name']}' ساخته شد.\nاز منوی مدیریت آن را روشن کنید.")
        context.user_data.clear()
        await show_main_menu(update)
        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text("فقط عدد وارد کنید.")
        return STATE_GET_AMOUNT_USDT

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("لغو شد.")
    await show_main_menu(update)
    return ConversationHandler.END

# --- مدیریت حساب‌ها ---

async def manage_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    accounts = db_manager.execute_query(
        "SELECT * FROM trading_accounts WHERE user_telegram_id = %s", 
        (user_id,), fetch='all'
    )
    
    if not accounts:
        await query.edit_message_text("حسابی یافت نشد.", 
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="main_menu")]]))
        return

    keyboard = []
    for acc in accounts:
        status = "🟢" if acc['is_active'] else "🔴"
        btn = f"{status} {acc['account_name']}"
        keyboard.append([InlineKeyboardButton(btn, callback_data=f"view_acc_{acc['account_id']}")])
    
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")])
    await query.edit_message_text("حساب‌های شما:", reply_markup=InlineKeyboardMarkup(keyboard))

async def account_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data.startswith("view_acc_"):
        acc_id = data.split("_")[2]
        acc = db_manager.execute_query("SELECT * FROM trading_accounts WHERE account_id=%s", (acc_id,), fetch='one')
        
        status_txt = "فعال" if acc['is_active'] else "غیرفعال"
        toggle_btn = "🔴 خاموش کردن" if acc['is_active'] else "🟢 روشن کردن"
        
        text = (
            f"👤 **{acc['account_name']}**\n"
            f"وضعیت: {status_txt}\n"
            f"خرید تومانی: {int(acc['trade_amount_tmn']):,} TMN\n"
            f"خرید تتری: {int(acc['trade_amount_usdt']):,} USDT"
        )
        
        keyboard = [
            [InlineKeyboardButton(toggle_btn, callback_data=f"toggle_{acc_id}")],
            [InlineKeyboardButton("🗑 حذف", callback_data=f"delete_{acc_id}")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="manage_accounts")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("toggle_"):
        acc_id = data.split("_")[1]
        db_manager.execute_query("UPDATE trading_accounts SET is_active = NOT is_active WHERE account_id=%s", (acc_id,))
        query.data = f"view_acc_{acc_id}"
        await account_actions(update, context)

    elif data.startswith("delete_"):
        acc_id = data.split("_")[1]
        db_manager.execute_query("DELETE FROM trade_ops WHERE account_id=%s", (acc_id,))
        db_manager.execute_query("DELETE FROM trading_accounts WHERE account_id=%s", (acc_id,))
        await query.answer("حذف شد.")
        await manage_accounts(update, context)

    elif data == "main_menu":
        await show_main_menu(update)

def run_bot():
    app = Application.builder().token(config.TELEGRAM["BOT_TOKEN"]).build()
    
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_account_start, pattern="^add_account$")],
        states={
            STATE_GET_NAME: [MessageHandler(filters.TEXT, get_name)],
            STATE_GET_API: [MessageHandler(filters.TEXT, get_api)],
            STATE_GET_AMOUNT_TMN: [MessageHandler(filters.TEXT, get_amount_tmn)],
            STATE_GET_AMOUNT_USDT: [MessageHandler(filters.TEXT, get_amount_usdt)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(manage_accounts, pattern="^manage_accounts$"))
    app.add_handler(CallbackQueryHandler(account_actions))
    
    print("Telegram Bot Running...")
    app.run_polling()

if __name__ == "__main__":
    run_bot()
