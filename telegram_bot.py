# telegram_bot.py
# نسخه نهایی و کاملاً پایدار (Final Stable Version) - حذف ارجاع خطا

import logging
import re
import os
import pandas as pd
import xlsxwriter
from datetime import datetime
from decimal import Decimal
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ConversationHandler, MessageHandler, filters, ContextTypes
)
import config
import db_manager
import wallex_api
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(message)s')

# --- States ---
(STATE_NAME, STATE_MOBILE, STATE_EMAIL, STATE_API, 
 STATE_MAX_TMN, STATE_MAX_USDT, 
 STATE_BUDGET_TMN, STATE_BUDGET_USDT,
 STATE_SELECT_STRATEGY, STATE_SELECT_GRADE) = range(10)

# Edit States (حذف شده از قابلیت‌ها، اما برای تعریف ConversationHandler باقی می‌ماند)
STATE_EDIT_MENU, STATE_EDIT_INPUT, STATE_EDIT_STRAT, STATE_EDIT_GRADE = range(10, 14)

ALL_STRATEGIES = ["G1", "Internal", "Computiational", "Arbitrage"]
ALL_GRADES = ["Q1", "Q2", "Q3", "Q4"]

# ==============================================================================
# Helper Functions
# ==============================================================================

async def clean_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پیام قبلی ربات را پاک می‌کند تا منو همیشه پایین باشد"""
    try:
        last_id = context.user_data.get('last_msg_id')
        if last_id:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=last_id)
    except Exception:
        pass

async def send_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text, reply_markup=None):
    """ارسال پیام جدید و ذخیره ID آن"""
    await clean_chat(update, context)
    try:
        msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup, parse_mode="Markdown")
        context.user_data['last_msg_id'] = msg.message_id
    except Exception as e:
        logger.error(f"Send Menu Error: {e}")

def get_selection_keyboard(options, selected_list, prefix, done_cb):
    kb = []
    row = []
    for opt in options:
        txt = f"✅ {opt}" if opt in selected_list else opt
        row.append(InlineKeyboardButton(txt, callback_data=f"{prefix}_{opt}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton("ثبت و ادامه ✔️", callback_data=done_cb)])
    return InlineKeyboardMarkup(kb)

def get_account_info_text(acc):
    strats = acc.get('allowed_strategies', 'ALL').replace(',', '، ')
    grades = acc.get('allowed_grades', 'ALL').replace(',', '، ')
    return (
        f"👤 **حساب:** `{acc['account_name']}`\n"
        f"📱 موبایل: `{acc['mobile_number']}`\n"
        f"📧 ایمیل: `{acc['email']}`\n"
        f"──────────────\n"
        f"🛡 سقف ریسک (TMN): `{int(acc['max_trade_tmn']):,}`\n"
        f"🛡 سقف ریسک (USDT): `{int(acc['max_trade_usdt']):,}`\n"
        f"──────────────\n"
        f"💰 بودجه پوزیشن (TMN): `{int(acc['trade_amount_tmn']):,}`\n"
        f"💵 بودجه پوزیشن (USDT): `{int(acc['trade_amount_usdt']):,}`\n"
        f"──────────────\n"
        f"🎯 استراتژی‌ها: {strats}\n"
        f"💎 گریدها: {grades}"
    )

# --- Excel Report ---
def get_live_prices_snapshot():
    url = config.WALLEX["BASE_URL"] + config.WALLEX["ENDPOINTS"]["ALL_MARKETS"]
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            markets = resp.json().get("result", {}).get("markets", [])
            prices = {}
            for m in markets:
                symbol = m.get("symbol")
                last = m.get("stats", {}).get("lastPrice")
                if symbol and last:
                    prices[symbol] = float(last)
            return prices
    except Exception:
        pass
    return {}

def create_profit_report_excel(user_id, account_id):
    try:
        acc_filter = "AND ta.account_id = %s" if account_id != "all" else ""
        params = [user_id]
        if account_id != "all":
            params.append(account_id)
        
        file_path = f"/tmp/Report_{user_id}_{int(datetime.now().timestamp())}.xlsx"
        writer = pd.ExcelWriter(file_path, engine='xlsxwriter')
        
        q_closed = f"SELECT ts.asset_name, ts.pair, ts.invested_amount, ts.sell_revenue, ts.created_at FROM trade_ops ts JOIN trading_accounts ta ON ts.account_id = ta.account_id WHERE ta.user_telegram_id = %s {acc_filter} AND (ts.status = 'COMPLETED' OR ts.status = 'SELL_ORDER_FILLED')"
        closed = db_manager.execute_query(q_closed, tuple(params), fetch='all')
        
        has_data = False
        if closed:
            has_data = True
            data_list = []
            for row in closed:
                inv = float(row['invested_amount'] or 0)
                rev = float(row['sell_revenue'] or 0)
                data_list.append({
                    'Asset': row['asset_name'],
                    'Pair': row['pair'],
                    'Date': str(row['created_at']),
                    'Invested': inv,
                    'Revenue': rev,
                    'Profit': rev - inv
                })
            pd.DataFrame(data_list).to_excel(writer, sheet_name='History', index=False)
            
        q_open = f"SELECT ts.asset_name, ts.pair, ts.invested_amount, ts.buy_quantity_executed FROM trade_ops ts JOIN trading_accounts ta ON ts.account_id = ta.account_id WHERE ta.user_telegram_id = %s {acc_filter} AND ts.status IN ('BUY_FILLED', 'SELL_IN_PROGRESS', 'SELL_ORDER_PLACED')"
        opened = db_manager.execute_query(q_open, tuple(params), fetch='all')
        
        if opened:
            has_data = True
            live_prices = get_live_prices_snapshot()
            open_list = []
            for row in opened:
                sym = f"{row['asset_name']}{row['pair']}"
                curr = live_prices.get(sym, 0)
                qty = float(row['buy_quantity_executed'] or 0)
                inv = float(row['invested_amount'] or 0)
                curr_val = qty * curr
                open_list.append({
                    'Asset': row['asset_name'],
                    'Pair': row['pair'],
                    'Qty': qty,
                    'Invested': inv,
                    'Current Price': curr,
                    'Current Value': curr_val,
                    'PnL': curr_val - inv
                })
            pd.DataFrame(open_list).to_excel(writer, sheet_name='Active Trades', index=False)

        writer.close()
        if not has_data: return None
        return file_path
    except Exception as e:
        logging.error(f"Excel Error: {e}")
        return None

# ==============================================================================
# Add Account Wizard
# ==============================================================================

async def start_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['sel_strategies'] = []
    context.user_data['sel_grades'] = []
    await send_menu(update, context, "👋 **افزودن حساب جدید**\n\n1️⃣ لطفاً یک **نام دلخواه** وارد کنید:\n(مثال: حساب اصلی)")
    return STATE_NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['name'] = update.message.text
    try:
        await update.message.delete()
    except Exception:
        pass
    await send_menu(update, context, "📱 **مرحله ۲:** لطفاً **شماره موبایل** وارد کنید:\n(09xxxxxxxxx)")
    return STATE_MOBILE

async def get_mobile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    val = update.message.text
    try:
        await update.message.delete()
    except Exception:
        pass
    if not re.match(r'^09\d{9}$', val):
        await send_menu(update, context, "❌ فرمت نامعتبر! لطفاً ۱۱ رقمی و با ۰۹ وارد کنید:")
        return STATE_MOBILE
    context.user_data['mobile'] = val
    await send_menu(update, context, "📧 **مرحله ۳:** لطفاً **ایمیل** وارد کنید:")
    return STATE_EMAIL

async def get_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['email'] = update.message.text
    try:
        await update.message.delete()
    except Exception:
        pass
    await send_menu(update, context, "🔑 **مرحله ۴:** لطفاً **API Key** والکس را ارسال کنید:")
    return STATE_API

async def get_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass
    
    if wallex_api.validate_api_key(api):
        context.user_data['api'] = api
        await send_menu(update, context, "🛡 **مرحله ۵ (مدار شکن تومان):**\nسقف مجاز قفل شده تومانی؟\n(مثال: 10000000)")
        return STATE_MAX_TMN
    else:
        await send_menu(update, context, "❌ نامعتبر! مجدد تلاش کنید:")
        return STATE_API

async def get_max_tmn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['max_tmn'] = Decimal(update.message.text)
        try:
            await update.message.delete()
        except Exception:
            pass
        await send_menu(update, context, "🛡 **مرحله ۶ (مدار شکن تتر):**\nسقف مجاز قفل شده تتری؟\n(مثال: 500)")
        return STATE_MAX_USDT
    except:
        await send_menu(update, context, "❌ لطفاً عدد وارد کنید:")
        return STATE_MAX_TMN

async def get_max_usdt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['max_usdt'] = Decimal(update.message.text)
        try:
            await update.message.delete()
        except Exception:
            pass
        await send_menu(update, context, "💰 **مرحله ۷ (بودجه تومان):**\nبرای هر خرید تومانی چقدر هزینه شود؟")
        return STATE_BUDGET_TMN
    except:
        await send_menu(update, context, "❌ لطفاً عدد وارد کنید:")
        return STATE_MAX_USDT

async def get_budget_tmn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = update.message.text
        Decimal(val)
        context.user_data['bud_tmn'] = val
        try:
            await update.message.delete()
        except Exception:
            pass
        await send_menu(update, context, "💵 **مرحله ۸ (بودجه تتر):**\nبرای هر خرید تتری چقدر هزینه شود؟")
        return STATE_BUDGET_USDT
    except:
        await send_menu(update, context, "❌ لطفاً عدد وارد کنید:")
        return STATE_BUDGET_TMN

async def get_budget_usdt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = update.message.text
        Decimal(val)
        context.user_data['bud_usdt'] = val
        try:
            await update.message.delete()
        except Exception:
            pass
        
        kb = get_selection_keyboard(ALL_STRATEGIES, [], "ts", "ds")
        await send_menu(update, context, "🎯 **مرحله ۹:** انتخاب استراتژی‌های مجاز:", kb)
        return STATE_SELECT_STRATEGY
    except:
        await send_menu(update, context, "❌ لطفاً عدد وارد کنید:")
        return STATE_BUDGET_USDT

async def sel_strat_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "ds":
        kb = get_selection_keyboard(ALL_GRADES, [], "tg", "dg")
        await send_menu(update, context, "💎 **مرحله آخر:** انتخاب گریدهای مجاز:", kb)
        return STATE_SELECT_GRADE
    
    item = data.replace("ts_", "")
    l = context.user_data.get('sel_strategies', [])
    if item in l:
        l.remove(item)
    else:
        l.append(item)
    context.user_data['sel_strategies'] = l
    await query.edit_message_reply_markup(get_selection_keyboard(ALL_STRATEGIES, l, "ts", "ds"))
    return STATE_SELECT_STRATEGY

async def sel_grade_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "dg":
        d = context.user_data
        try:
            db_manager.execute_query(
                """INSERT INTO trading_accounts 
                (user_telegram_id, account_name, mobile_number, email, wallex_api_key, 
                 max_trade_tmn, max_trade_usdt, trade_amount_tmn, trade_amount_usdt, 
                 allowed_strategies, allowed_grades, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE)""",
                (update.effective_user.id, d['name'], d['mobile'], d['email'], d['api'], 
                 d['max_tmn'], d['max_usdt'], d['bud_tmn'], d['bud_usdt'], 
                 ",".join(d['sel_strategies']), ",".join(d['sel_grades']))
            )
            await send_menu(update, context, "🎉 **حساب ساخته شد!**\nاز منوی مدیریت آن را فعال کنید.")
            await show_main_menu(update, context)
            return ConversationHandler.END
        except Exception as e:
            logging.error(e)
            await send_menu(update, context, "❌ خطا در دیتابیس.")
            return ConversationHandler.END
    
    item = data.replace("tg_", "")
    l = context.user_data.get('sel_grades', [])
    if item in l:
        l.remove(item)
    else:
        l.append(item)
    context.user_data['sel_grades'] = l
    await query.edit_message_reply_markup(get_selection_keyboard(ALL_GRADES, l, "tg", "dg"))
    return STATE_SELECT_GRADE

# ==============================================================================
# Main Handlers
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db_manager.execute_query("""INSERT INTO users (telegram_id, first_name, username) VALUES (%s, %s, %s) 
                                ON DUPLICATE KEY UPDATE first_name=%s, username=%s""",
                             (u.id, u.first_name, u.username, u.first_name, u.username))
    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text="👋 **به ربات MultiTrade خوش آمدید.**\nلطفاً یک گزینه را انتخاب کنید:"):
    kb = [[InlineKeyboardButton("➕ افزودن حساب جدید", callback_data="add_acc")],
          [InlineKeyboardButton("⚙️ مدیریت حساب‌ها", callback_data="manage")],
          [InlineKeyboardButton("📊 گزارش عملکرد", callback_data="report")]]
    await send_menu(update, context, text, InlineKeyboardMarkup(kb))

async def manage_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    d = q.data if q else "manage"
    
    uid = update.effective_user.id
    
    if d.startswith("edit_start_"): return

    if d == "manage":
        accs = db_manager.execute_query("SELECT * FROM trading_accounts WHERE user_telegram_id=%s", (uid,), fetch='all')
        if not accs:
            await send_menu(update, context, "❌ شما هنوز حسابی ندارید.")
            return
        kb = []
        for a in accs: kb.append([InlineKeyboardButton(f"{'🟢' if a['is_active'] else '🔴'} {a['account_name']}", callback_data=f"det_{a['account_id']}")])
        kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")])
        await send_menu(update, context, "⚙️ **مدیریت حساب‌ها**\nحساب مورد نظر را انتخاب کنید:", InlineKeyboardMarkup(kb))

    elif d == "main_menu":
        await show_main_menu(update, context)

    elif d.startswith("det_"):
        aid = d.split("_")[1]
        acc = db_manager.execute_query("SELECT * FROM trading_accounts WHERE account_id=%s", (aid,), fetch='one')
        txt = get_account_info_text(acc)
        kb = [
            [InlineKeyboardButton("تغییر وضعیت 🔄", callback_data=f"tog_{aid}")],
            [InlineKeyboardButton("🗑 حذف حساب", callback_data=f"del_{aid}")],
            [InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="manage")]
        ]
        await send_menu(update, context, txt, InlineKeyboardMarkup(kb))

    elif d.startswith("tog_"):
        db_manager.execute_query("UPDATE trading_accounts SET is_active = NOT is_active WHERE account_id=%s", (d.split("_")[1],))
        q.data = f"det_{d.split('_')[1]}"
        await manage_handler(update, context)

    elif d.startswith("del_"):
        aid = d.split("_")[1]
        db_manager.execute_query("DELETE FROM trade_ops WHERE account_id=%s", (aid,))
        db_manager.execute_query("DELETE FROM trading_accounts WHERE account_id=%s", (aid,))
        await send_menu(update, context, "✅ حساب حذف شد.")
        await manage_handler(update, context)

    elif d == "report":
        accs = db_manager.execute_query("SELECT account_id, account_name FROM trading_accounts WHERE user_telegram_id=%s", (uid,), fetch='all')
        kb = []
        for a in accs: kb.append([InlineKeyboardButton(a['account_name'], callback_data=f"gre_{a['account_id']}")])
        kb.append([InlineKeyboardButton("همه حساب‌ها", callback_data="gre_all")])
        kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")])
        await send_menu(update, context, "📊 **گزارش عملکرد**\nانتخاب کنید:", InlineKeyboardMarkup(kb))
        
    elif d.startswith("gre_"):
        await q.answer("⏳ در حال تولید گزارش...")
        acc_id = d.split("_")[1]
        path = create_profit_report_excel(update.effective_user.id, acc_id if acc_id != 'all' else 'all')
        if path:
            await context.bot.send_document(chat_id=update.effective_chat.id, document=open(path, 'rb'), filename="Report.xlsx")
            os.remove(path)
            await show_main_menu(update, context)
        else:
            await send_menu(update, context, "❌ داده‌ای یافت نشد.")

async def cancel(u,c): 
    await show_main_menu(u,c)
    return ConversationHandler.END

def run_bot():
    app = Application.builder().token(config.TELEGRAM["BOT_TOKEN"]).build()
    
    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add, pattern="^add_acc$")],
        states={
            STATE_NAME: [MessageHandler(filters.TEXT, get_name)],
            STATE_MOBILE: [MessageHandler(filters.TEXT, get_mobile)],
            STATE_EMAIL: [MessageHandler(filters.TEXT, get_email)],
            STATE_API: [MessageHandler(filters.TEXT, get_api)],
            STATE_MAX_TMN: [MessageHandler(filters.TEXT, get_max_tmn)],
            STATE_MAX_USDT: [MessageHandler(filters.TEXT, get_max_usdt)],
            STATE_BUDGET_TMN: [MessageHandler(filters.TEXT, get_budget_tmn)],
            STATE_BUDGET_USDT: [MessageHandler(filters.TEXT, get_budget_usdt)],
            STATE_SELECT_STRATEGY: [CallbackQueryHandler(sel_strat_add)],
            STATE_SELECT_GRADE: [CallbackQueryHandler(sel_grade_add)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(add_conv)
    app.add_handler(CallbackQueryHandler(manage_handler))
    
    print("Telegram Bot Running (Final Corrected)...")
    app.run_polling()

if __name__ == "__main__":
    run_bot()
