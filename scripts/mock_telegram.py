#!/usr/bin/env python3
"""A Bot API mock for the panel's Telegram bot -- demo + end-to-end tests.

Speaks the handful of methods the bot uses (getMe, getUpdates long poll,
sendMessage, editMessageText, answerCallbackQuery) and answers everything
else with ok. Updates are pushed in through ``POST /debug``; everything the
bot sent is read back from ``GET /debug`` so tests can assert on messages.

    python3 mock_telegram.py [port]     # default :15855

    POST /debug {"updates": [...]}      # queue updates for getUpdates
    POST /debug {"op": "clear"}         # wipe the sent log
    GET  /debug                         # [{"method", "payload"}, ...]
"""
import json
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPDATES: "queue.Queue[dict]" = queue.Queue()
SENT: list[dict] = []
LOCK = threading.Lock()
MSG_ID = 1000


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: N802 -- quiet
        pass

    def _read(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode() if n else ""
        return json.loads(raw) if raw else {}

    def _reply(self, result) -> None:
        body = json.dumps({"ok": True, "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        path, payload = self.path, self._read()
        global MSG_ID
        if path.startswith("/bot") and path.endswith("/getUpdates"):
            try:
                upd = UPDATES.get(timeout=20)
                self._reply([upd])
            except queue.Empty:
                self._reply([])
        elif path.endswith("/getMe"):
            self._reply({"id": 7, "is_bot": True, "username": "sem_shop_bot",
                         "first_name": "SEM Shop"})
        elif path.endswith("/sendMessage"):
            with LOCK:
                MSG_ID += 1
                SENT.append({"method": "sendMessage", "payload": payload, "mid": MSG_ID})
            self._reply({"message_id": MSG_ID, "chat": payload.get("chat")})
        elif path.endswith("/editMessageText"):
            with LOCK:
                SENT.append({"method": "editMessageText", "payload": payload})
            self._reply(True)
        elif path.endswith("/answerCallbackQuery"):
            with LOCK:
                SENT.append({"method": "answerCallbackQuery", "payload": payload})
            self._reply(True)
        elif path.endswith("/debug"):
            op = payload.get("op")
            if op == "clear":
                with LOCK:
                    SENT.clear()
            for u in payload.get("updates") or []:
                UPDATES.put(u)
            self._reply({"queued": True})
        else:
            self._reply({})

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/debug":
            with LOCK:
                out = list(SENT)
            self._reply(out)
        else:
            self._reply({})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 15855
    print(f"mock Telegram Bot API on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
