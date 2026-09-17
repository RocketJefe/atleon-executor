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
                    print(f"[EXNOVA] Autenticado OK. Balance ({ACCOUNT_TYPE}): ${balance_val:.2f}")
        elif msg_name == "option-opened":
            print(f"[EXNOVA] Orden confirmada: {data.get('msg')}")
    except Exception as e:
        print(f"[WS ERROR] {e}")

def on_open(ws):
    print("[EXNOVA] WebSocket conectado. Enviando SSID...")
    ws.send(json.dumps({"name": "ssid", "msg": EXNOVA_SSID}))

def start_ws():
    global ws_app
    ws_app = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
    ws_app.run_forever()

def send_telegram(chat_id, text):
    try:
        r = requests.post(f"{TG_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=5)
        print(f"[TG OUT] Mensaje enviado a {chat_id}: {r.status_code}")
    except Exception as e:
        print(f"[TG ERROR] {e}")

def run_polling():
    print(f"[TG] Iniciando polling para bot token: {BOT_TOKEN[:10]}...")
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
                print(f"[TG WARN] Respuesta no OK: {res}")
                time.sleep(2)
                continue

            for item in res.get("result", []):
                last_update_id = item["update_id"]
                msg = item.get("message") or item.get("channel_post")
                if not msg or "text" not in msg:
                    continue

                chat_id = msg["chat"]["id"]
                text = msg["text"].strip()
                print(f"[TG IN] Recibido: {text}")

                if text.startswith("/status"):
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

        except requests.exceptions.RequestException as re:
            print(f"[TG TIMEOUT/NETWORK] Conexión lenta o reintentando: {re}")
            time.sleep(3)
        except Exception as e:
            print(f"[POLL UNEXPECTED ERROR] {e}")
            time.sleep(3)

if __name__ == "__main__":
    # 1. Health check en hilo
    threading.Thread(target=run_health, daemon=True).start()
    # 2. Exnova WebSocket en hilo
    threading.Thread(target=start_ws, daemon=True).start()
    # 3. Polling en proceso principal para evitar que muera
    time.sleep(2)
    run_polling()
