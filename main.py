import os
import time
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import websocket

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
EXNOVA_SSID = os.getenv("EXNOVA_SSID", "").strip()
ACCOUNT_TYPE = os.getenv("EXNOVA_ACCOUNT_TYPE", "PRACTICE").strip().upper()
TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", "5.0"))
PORT = int(os.getenv("PORT", 10000))

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WS_URL = "wss://ws.iqoption.com/echo/websocket"

ws_app = None
is_connected = False
balance_val = 0.0
active_balance_id = None
user_profile_id = None

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

def set_http_balance(b_id):
    """Fija la cuenta activa en la API web de Exnova"""
    try:
        headers = {"Cookie": f"ssid={EXNOVA_SSID}", "User-Agent": "Mozilla/5.0"}
        requests.post("https://exnova.com/api/profile/changebalance", data={"balance_id": b_id}, headers=headers, timeout=5)
    except Exception:
        pass

def on_message(ws, message):
    global is_connected, balance_val, active_balance_id, user_profile_id
    try:
        data = json.loads(message)
        msg_name = data.get("name")
        
        if msg_name == "profile":
            is_connected = True
            msg = data.get("msg", {})
            user_profile_id = msg.get("user_id") or msg.get("id")
            balances = msg.get("balances", [])
            target_type = 4 if ACCOUNT_TYPE == "PRACTICE" else 1
            
            for b in balances:
                if b.get("type") == target_type:
                    balance_val = float(b.get("amount", 0.0))
                    active_balance_id = int(b.get("id"))
                    print(f"[EXNOVA OK] Sesion validada | Balance {ACCOUNT_TYPE}: ${balance_val:.2f} | ID: {active_balance_id}")
                    threading.Thread(target=set_http_balance, args=(active_balance_id,), daemon=True).start()
                    break

        elif msg_name in ["option-opened", "order-placed-temp", "position-opened", "blitz-opened"]:
            print(f"[EXNOVA TRADE OK] >>> ORDEN EJECUTADA: {data.get('msg')}")

        elif msg_name in ["option-rejected", "order-rejected"]:
            print(f"[EXNOVA TRADE RECHAZADO] >>> Motivo: {data.get('msg')}")

    except Exception as e:
        print(f"[WS ERR] {e}")

def on_open(ws):
    print("[EXNOVA] Enviando autenticacion SSID...")
    ws.send(json.dumps({"name": "ssid", "msg": EXNOVA_SSID}))

def start_ws():
    global ws_app
    while True:
        try:
            ws_app = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws_app.run_forever()
        except Exception as e:
            print(f"[WS RECONNECT] {e}")
        time.sleep(3)

def execute_strike(active="EURUSD", direction="CALL", duration=30):
    global active_balance_id, ws_app
    if not is_connected or not ws_app or not active_balance_id:
        print("[ERROR] No se puede ejecutar: WebSocket o Balance no listos.")
        return False

    dir_clean = "call" if direction.upper() in ["CALL", "HIGHER", "BUY"] else "put"
    now = int(time.time())
    
    print(f"[DISPARO] Ejecutando orden {dir_clean.upper()} en {active} por ${TRADE_AMOUNT}...")

    # Formato Blitz nativo de Exnova
    blitz_msg = {
        "name": "sendMessage",
        "msg": {
            "name": "blitz-options.open-option",
            "version": "1.0",
            "body": {
                "user_balance_id": active_balance_id,
                "active_id": 1,
                "direction": dir_clean,
                "duration": duration,
                "price": TRADE_AMOUNT
            }
        }
    }
    
    # Formato Turbo/Binaria estandar
    turbo_msg = {
        "name": "sendMessage",
        "msg": {
            "name": "binary-options.open-option",
            "version": "1.0",
            "body": {
                "user_balance_id": active_balance_id,
                "active_id": 1,
                "option_type_id": 3,
                "direction": dir_clean,
                "expired": now + duration,
                "price": TRADE_AMOUNT,
                "profit_percent": 85
            }
        }
    }

    try:
        ws_app.send(json.dumps(blitz_msg))
        ws_app.send(json.dumps(turbo_msg))
        print("[DISPARO] Paquetes de ejecucion transmitidos con exito a Exnova.")
        return True
    except Exception as e:
        print(f"[ERROR DISPARO] {e}")
        return False

def send_telegram(chat_id, text):
    try:
        requests.post(f"{TG_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=5)
    except Exception as e:
        print(f"[TG ERROR] {e}")

def run_polling():
    print("[TG] Iniciando escucha de comandos...")
    last_update_id = 0
    while True:
        try:
            url = f"{TG_API}/getUpdates?offset={last_update_id + 1}&timeout=15"
            res = requests.get(url, timeout=20).json()
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
                print(f"[TG RECIBIDO] {text}")

                if text.startswith("/status"):
                    status_dot = "🟢 Conectado" if is_connected else "🟡 Conectando..."
                    reply = (
                        f"📊 Estado Atleon Executor:\n"
                        f"• Conexión: {status_dot}\n"
                        f"• Saldo: ${balance_val:.2f}\n"
                        f"• Cuenta: {ACCOUNT_TYPE} (ID: {active_balance_id})\n"
                        f"• Monto por trade: ${TRADE_AMOUNT:.2f}"
                    )
                    send_telegram(chat_id, reply)

                elif "ALERTA GHOST STRIKE" in text or text.upper() in ["CALL", "PUT"]:
                    direction = "CALL" if ("CALL" in text.upper() or "HIGHER" in text.upper()) else "PUT"
                    send_telegram(chat_id, f"⚡️ Ejecutando orden {direction} EURUSD en vivo...")
                    execute_strike("EURUSD", direction, duration=30)

        except Exception as e:
            time.sleep(3)

if __name__ == "__main__":
    threading.Thread(target=run_health, daemon=True).start()
    threading.Thread(target=start_ws, daemon=True).start()
    time.sleep(1)
    run_polling()
