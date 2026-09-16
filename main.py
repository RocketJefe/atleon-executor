import os
import time
import json
import threading
import requests
import websocket

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
EXNOVA_SSID = os.getenv("EXNOVA_SSID")
ACCOUNT_TYPE = os.getenv("EXNOVA_ACCOUNT_TYPE", "PRACTICE")
TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", "5.0"))

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WS_URL = "wss://ws.iqoption.com/echo/websocket"

ws_app = None
is_connected = False
balance_val = 0.0

def on_message(ws, message):
    global is_connected, balance_val
    try:
        data = json.loads(message)
        msg_name = data.get("name")
        
        if msg_name == "profile":
            is_connected = True
            balances = data.get("msg", {}).get("balances", [])
            target_type = 4 if ACCOUNT_TYPE == "PRACTICE" else 1
            for b in balances:
                if b.get("type") == target_type:
                    balance_val = float(b.get("amount", 0.0))
                    print(f"[EXNOVA] Sesión validada. Saldo {ACCOUNT_TYPE}: ${balance_val:.2f}")

        elif msg_name == "option-opened":
            print(f"[EXNOVA] Orden abierta con éxito: {data.get('msg')}")

    except Exception as e:
        print(f"[WS ERROR] Procesando mensaje: {e}")

def on_open(ws):
    print("[EXNOVA] WebSocket conectado. Enviando autenticación SSID...")
    ws.send(json.dumps({"name": "ssid", "msg": EXNOVA_SSID}))

def start_ws():
    global ws_app
    ws_app = websocket.WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_message=on_message
    )
    t = threading.Thread(target=ws_app.run_forever, daemon=True)
    t.start()

def execute_strike(active, direction, duration=30):
    if not is_connected or not ws_app:
        print("[ERROR] No se puede ejecutar: WebSocket desconectado")
        return False
    
    print(f"[STRIKE] Enviando orden {direction} en {active} (${TRADE_AMOUNT}, {duration}s)...")
    payload = {
        "name": "sendMessage",
        "msg": {
            "name": "binary-options.open-option",
            "version": "1.0",
            "body": {
                "user_balance_id": None,
                "active_id": 1,  # 1 = EURUSD
                "option_type_id": 3,  # Turbo blitz
                "direction": direction.lower(),
                "expired": int(time.time()) + duration,
                "price": TRADE_AMOUNT,
                "profit_percent": 85
            }
        }
    }
    ws_app.send(json.dumps(payload))
    return True

def send_telegram(chat_id, text):
    try:
        requests.post(f"{TG_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=5)
    except Exception as e:
        print(f"[TG ERROR] Error enviando mensaje: {e}")

def run_polling():
    print("[TG] Iniciando polling ultraligero de Telegram...")
    requests.get(f"{TG_API}/deleteWebhook?drop_pending_updates=True", timeout=5)
    last_update_id = 0

    while True:
        try:
            url = f"{TG_API}/getUpdates?offset={last_update_id + 1}&timeout=10"
            res = requests.get(url, timeout=15).json()
            if not res.get("ok"):
                time.sleep(2)
                continue

            for item in res.get("result", []):
                last_update_id = item["update_id"]
                msg = item.get("message") or item.get("channel_post")
                if not msg or "text" not in msg:
                    continue

                chat_id = msg["chat"]["id"]
                text = msg["text"].strip()

                if text == "/status":
                    status_dot = "🟢 Conectado" if is_connected else "🟡 Requiere actualización de SSID"
                    reply = (
                        f"📊 Estado Atleon Executor:\n"
                        f"• Conexión: {status_dot}\n"
                        f"• Saldo: ${balance_val:.2f}\n"
                        f"• Cuenta: {ACCOUNT_TYPE}\n"
                        f"• Monto por trade: ${TRADE_AMOUNT:.2f}"
                    )
                    send_telegram(chat_id, reply)

                elif "ALERTA GHOST STRIKE" in text:
                    direction = "CALL" if "ACCION: CALL" in text else "PUT"
                    send_telegram(chat_id, f"⚡️ Ejecutando orden {direction} EURUSD en Exnova...")
                    execute_strike("EURUSD", direction, duration=30)

        except Exception as e:
            print(f"[TG POLL ERROR] {e}")
            time.sleep(2)

if __name__ == "__main__":
    start_ws()
    time.sleep(2)
    run_polling()
