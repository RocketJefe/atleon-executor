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
active_balance_type = 4 if ACCOUNT_TYPE == "PRACTICE" else 1

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

def set_http_active_balance(b_id):
    """Sincroniza la cuenta activa vía HTTP para que aparezca en el navegador de inmediato"""
    urls = [
        "https://exnova.com/api/profile/changebalance",
        "https://iqoption.com/api/profile/changebalance"
    ]
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Cookie": f"ssid={EXNOVA_SSID}"
    }
    data = {"balance_id": b_id}
    for u in urls:
        try:
            res = requests.post(u, data=data, headers=headers, timeout=5)
            if res.status_code == 200:
                print(f"[EXNOVA HTTP] Cuenta fijada con éxito a ID {b_id} en {u}")
                break
        except Exception as e:
            pass

def calculate_turbo_expiration(duration_sec=30):
    """Calcula el timestamp exacto que exige el motor Turbo/Blitz"""
    now = int(time.time())
    # Expiración redondeada a la siguiente ventana de vela
    rem = now % 60
    if rem > 30:
        return now - rem + 120
    else:
        return now - rem + 60

def on_message(ws, message):
    global is_connected, balance_val, active_balance_id
    try:
        data = json.loads(message)
        msg_name = data.get("name")
        
        if msg_name == "profile":
            is_connected = True
            balances = data.get("msg", {}).get("balances", [])
            for b in balances:
                if b.get("type") == active_balance_type:
                    balance_val = float(b.get("amount", 0.0))
                    active_balance_id = int(b.get("id"))
                    print(f"[EXNOVA] Autenticado OK | Cuenta {ACCOUNT_TYPE} | ID: {active_balance_id} | Saldo: ${balance_val:.2f}")
                    # Sincronizar inmediatamente
                    set_http_active_balance(active_balance_id)
                    break

        elif msg_name in ["option-opened", "order-placed-temp", "position-opened"]:
            print(f"[EXNOVA] ¡ORDEN DISPARADA CON ÉXITO Y VISIBLE! -> {data.get('msg')}")

        elif msg_name == "option-rejected":
            print(f"[EXNOVA ERROR] Orden rechazada por broker: {data.get('msg')}")

    except Exception as e:
        print(f"[WS ERROR] {e}")

def on_open(ws):
    print("[EXNOVA] WebSocket conectado. Autenticando SSID...")
    ws.send(json.dumps({"name": "ssid", "msg": EXNOVA_SSID}))

def start_ws():
    global ws_app
    ws_app = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
    ws_app.run_forever()

def execute_strike(active="EURUSD", direction="CALL", duration=30):
    global active_balance_id
    if not is_connected or not ws_app or not active_balance_id:
        print("[ERROR] Imposible ejecutar: falta balance_id o conexión")
        return False

    dir_clean = "call" if direction.upper() in ["CALL", "HIGHER", "BUY"] else "put"
    exp_time = calculate_turbo_expiration(duration)
    
    print(f"[STRIKE] Enviando orden real: {dir_clean.upper()} en {active} (${TRADE_AMOUNT}) | Bal ID: {active_balance_id} | Exp: {exp_time}")
    
    # 1. Asegurar la cuenta activa en la API
    set_http_active_balance(active_balance_id)

    # 2. Abrir la posición con parámetros Turbo/Blitz certificados
    payload = {
        "name": "sendMessage",
        "msg": {
            "name": "binary-options.open-option",
            "version": "1.0",
            "body": {
                "user_balance_id": active_balance_id,
                "active_id": 1,         # 1 = EURUSD (76 si fuera EURUSD OTC)
                "option_type_id": 3,    # 3 = Turbo / Blitz
                "direction": dir_clean,
                "expired": exp_time,
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
        print(f"[TG POLL INIT WARN] {e}")

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
                    status_dot = "🟢 Conectado" if is_connected else "🟡 Desconectado"
                    reply = (
                        f"📊 Estado Atleon Executor:\n"
                        f"• Conexión: {status_dot}\n"
                        f"• Saldo: ${balance_val:.2f}\n"
                        f"• Cuenta: {ACCOUNT_TYPE} (ID: {active_balance_id})\n"
                        f"• Monto por trade: ${TRADE_AMOUNT:.2f}"
                    )
                    send_telegram(chat_id, reply)

                elif "ALERTA GHOST STRIKE" in text:
                    direction = "CALL" if "ACCION: CALL" in text else "PUT"
                    send_telegram(chat_id, f"⚡️ Ejecutando orden {direction} EURUSD en vivo...")
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
