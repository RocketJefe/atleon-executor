import os
import time
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import websocket

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
EXNOVA_SSID = os.getenv("EXNOVA_SSID", "").strip()
ACCOUNT_TYPE = os.getenv("EXNOVA_ACCOUNT_TYPE", "PRACTICE").strip()
TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", "5.0"))
PORT = int(os.getenv("PORT", 10000))

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WS_URL = "wss://ws.iqoption.com/echo/websocket"

ws_app = None
is_connected = False
balance_val = 0.0
active_balance_id = None

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Atleon Executor Online")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_health():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()

def set_broker_balance(balance_id):
    """Fuerza al broker a sincronizar esta cuenta como la activa en la sesión"""
    if ws_app and balance_id:
        sync_payload = {
            "name": "sendMessage",
            "msg": {
                "name": "internal-billing.set-active-balance",
                "version": "1.0",
                "body": {
                    "balance_id": balance_id
                }
            }
        }
        ws_app.send(json.dumps(sync_payload))
        print(f"[EXNOVA] Cuenta activa fijada en broker: Balance ID {balance_id}")

def on_message(ws, message):
    global is_connected, balance_val, active_balance_id
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
                    active_balance_id = int(b.get("id"))
                    print(f"[EXNOVA] Detectada {ACCOUNT_TYPE} | Saldo: ${balance_val:.2f} | Balance ID: {active_balance_id}")
                    # Amarrar el balance en el broker de inmediato
                    set_broker_balance(active_balance_id)
                    break

        elif msg_name in ["option-opened", "order-placed-temp", "position-opened"]:
            print(f"[EXNOVA] Posición confirmada visible en plataforma: {data.get('msg')}")

    except Exception as e:
        print(f"[WS ERROR] {e}")

def on_open(ws):
    print("[EXNOVA] Conectando socket... Enviando SSID")
    ws.send(json.dumps({"name": "ssid", "msg": EXNOVA_SSID}))

def start_ws():
    global ws_app
    ws_app = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
    ws_app.run_forever()

def execute_strike(active, direction, duration=30):
    global active_balance_id
    if not is_connected or not ws_app or not active_balance_id:
        print("[ERROR] No se puede ejecutar: Sesión o Balance ID no listos")
        return False

    dir_clean = "call" if direction.upper() in ["CALL", "HIGHER", "BUY"] else "put"
    print(f"[STRIKE] Abriendo orden en vivo: {dir_clean.upper()} en {active} (${TRADE_AMOUNT}) | ID Cuenta: {active_balance_id}")
    
    # Asegurar el balance antes de colocar la orden
    set_broker_balance(active_balance_id)

    payload = {
        "name": "sendMessage",
        "msg": {
            "name": "binary-options.open-option",
            "version": "1.0",
            "body": {
                "user_balance_id": active_balance_id,
                "active_id": 1,  # 1 = EURUSD
                "option_type_id": 3,  # Turbo / Blitz
                "direction": dir_clean,
                "expired": int(time.time()) + duration,
                "price": TRADE_AMOUNT,
                "profit_percent": 87
            }
        }
    }
    ws_app.send(json.dumps(payload))
    return True

def send_telegram(chat_id, text):
    try:
        requests.post(f"{TG_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=5)
    except Exception as e:
        print(f"[TG ERROR] {e}")

def run_polling():
    try:
        requests.get(f"{TG_API}/deleteWebhook?drop_pending_updates=True", timeout=15)
    except Exception as e:
        print(f"[TG SETUP WARN] {e}")

    last_update_id = 0
    while True:
        try:
            url = f"{TG_API}/getUpdates?offset={last_update_id + 1}&timeout=20"
            res = requests.get(url, timeout=25).json()
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

                if text.startswith("/status"):
                    status_dot = "🟢 Conectado" if is_connected else "🟡 Requiere actualización de SSID"
                    reply = (
                        f"📊 Estado Atleon Executor:\n"
                        f"• Conexión: {status_dot}\n"
                        f"• Saldo: ${balance_val:.2f}\n"
                        f"• Cuenta: {ACCOUNT_TYPE} (ID: {active_balance_id})\n"
                        f"• Monto: ${TRADE_AMOUNT:.2f}"
                    )
                    send_telegram(chat_id, reply)

                elif "ALERTA GHOST STRIKE" in text:
                    direction = "CALL" if "ACCION: CALL" in text else "PUT"
                    send_telegram(chat_id, f"⚡️ Orden enviada {direction} EURUSD...")
                    execute_strike("EURUSD", direction, duration=30)

        except requests.exceptions.RequestException:
            time.sleep(2)
        except Exception as e:
            print(f"[POLL ERROR] {e}")
            time.sleep(2)

if __name__ == "__main__":
    threading.Thread(target=run_health, daemon=True).start()
    threading.Thread(target=start_ws, daemon=True).start()
    time.sleep(2)
    run_polling()
