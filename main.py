import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

class HealthServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Radar Prime Active")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthServer)
    print(f"[HTTP] Health check escuchando en puerto {PORT}")
    server.serve_forever()

def scan_loop():
    print("[RADAR] Radar Prime iniciado. Escaneando pares en Frankfurt...")
    while True:
        time.sleep(60)

if __name__ == "__main__":
    t = threading.Thread(target=start_health_server, daemon=True)
    t.start()
    scan_loop()
