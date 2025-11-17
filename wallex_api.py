# wallex_api.py (ادامه فایل قبلی یا اضافه کردن این توابع)

import requests
import logging
import json
import config

# ... (سایر توابع مثل validate_api_key که قبلاً داشتید) ...

def place_order(api_key, symbol, side, price, quantity):
    """ثبت سفارش در والکس"""
    url = config.WALLEX["BASE_URL"] + config.WALLEX["ENDPOINTS"]["ORDERS"]
    headers = {"Content-Type": "application/json", "x-api-key": api_key}
    
    payload = {
        "symbol": symbol,
        "price": str(price),
        "quantity": str(quantity),
        "side": side,
        "type": "limit"
    }
    try:
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        if resp.status_code == 201:
            return resp.json()
        logging.error(f"Order Error: {resp.text}")
        return None
    except Exception as e:
        logging.error(f"Exception placing order: {e}")
        return None

def get_order_status(client_order_id, api_key):
    """دریافت وضعیت سفارش"""
    url = config.WALLEX["BASE_URL"] + config.WALLEX["ENDPOINTS"]["GET_ORDER"] + str(client_order_id)
    headers = {"x-api-key": api_key}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("result")
        return None
    except Exception as e:
        logging.error(f"Exception getting order: {e}")
        return None

def cancel_order(api_key, client_order_id):
    """لغو سفارش باز"""
    url = config.WALLEX["BASE_URL"] + config.WALLEX["ENDPOINTS"]["ORDERS"]
    headers = {"Content-Type": "application/json", "x-api-key": api_key}
    payload = {"clientOrderId": client_order_id}
    
    try:
        resp = requests.delete(url, headers=headers, data=json.dumps(payload), timeout=10)
        if resp.status_code == 200:
            return resp.json()
        logging.error(f"Cancel Error: {resp.text}")
        return None
    except Exception as e:
        logging.error(f"Exception canceling order: {e}")
        return None
