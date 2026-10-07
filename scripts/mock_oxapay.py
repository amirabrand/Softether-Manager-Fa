#!/usr/bin/env python3
"""An Oxapay merchant-API mock for the panel's payments -- demo + E2E.

Speaks the two merchant calls the panel makes:

    POST /merchants/request/invoice    -> {status:"success", trackId, payLink}
    POST /merchants/inquiry/{trackId}  -> {status:"success", payment:{status,...}}

and answers the panel's webhook address like the real gateway would: the
debug channel marks an invoice paid and POSTs the callback the invoice was
created with (unless the caller asked it to stay quiet).

    python3 mock_oxapay.py [port]      # default :15856

    POST /debug {"op":"pay","trackId":...}            # pay + fire webhook
    POST /debug {"op":"pay","trackId":...,"notify":false}  # pay silently
    POST /debug {"op":"clear"}                        # wipe
    GET  /debug                                       # invoices + callbacks sent
"""
import json
import random
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

INVOICES: dict[str, dict] = {}
CALLBACKS: list[dict] = []
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: N802 -- quiet
        pass

    def _read(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode() if n else ""
        return json.loads(raw) if raw else {}

    def _reply(self, obj, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        path, payload = self.path, self._read()
        if path == "/merchants/request/invoice":
            with LOCK:
                track = str(random.randint(10**7, 10**8 - 1))
                inv = {
                    "trackId": track,
                    "amount": payload.get("amount"),
                    "currency": payload.get("currency"),
                    "order_id": payload.get("order_id") or "",
                    "callback_url": payload.get("callback_url") or "",
                    "status": "waiting",
                }
                INVOICES[track] = inv
            self._reply({"status": "success", "trackId": track,
                         "payLink": f"http://127.0.0.1:{PORT}/pay/{track}"})
        elif path.startswith("/merchants/inquiry/"):
            track = path.rsplit("/", 1)[-1]
            with LOCK:
                inv = INVOICES.get(track)
            if not inv:
                self._reply({"status": "error", "message": "invoice not found"}, 404)
                return
            self._reply({"status": "success",
                         "payment": {k: inv[k] for k in
                                     ("trackId", "amount", "currency", "status", "order_id")}})
        elif path == "/debug":
            op = payload.get("op")
            if op == "clear":
                with LOCK:
                    INVOICES.clear()
                    CALLBACKS.clear()
                self._reply({"cleared": True})
                return
            if op == "pay":
                track = str(payload.get("trackId") or "")
                with LOCK:
                    inv = INVOICES.get(track)
                    if inv is None:
                        self._reply({"status": "error", "message": "no such track"}, 404)
                        return
                    inv["status"] = "paid"
                    cb = inv["callback_url"]
                if cb and payload.get("notify", True):
                    body = json.dumps({"type": "payment", "payType": "invoice",
                                       "trackId": track, "orderId": inv["order_id"],
                                       "amount": inv["amount"], "currency": inv["currency"],
                                       "status": "paid"}).encode()
                    try:
                        req = urllib.request.Request(cb, data=body, method="POST",
                                                     headers={"Content-Type": "application/json"})
                        with urllib.request.urlopen(req, timeout=10) as r:
                            result = {"code": r.status}
                    except Exception as e:  # noqa: BLE001
                        result = {"error": str(e)}
                    with LOCK:
                        CALLBACKS.append({"to": cb, "result": result})
                    self._reply({"paid": True, "callback": result})
                    return
                self._reply({"paid": True, "callback": None})
                return
            self._reply({"status": "error", "message": "unknown op"}, 400)
        else:
            self._reply({"status": "error", "message": "not found"}, 404)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/debug":
            with LOCK:
                self._reply({"invoices": dict(INVOICES), "callbacks": list(CALLBACKS)})
        elif self.path.startswith("/pay/"):
            track = self.path.rsplit("/", 1)[-1]
            with LOCK:
                inv = INVOICES.get(track)
            status = inv["status"] if inv else "unknown"
            html = (f"<!doctype html><html lang='fa' dir='rtl'><body>"
                    f"<h1>صفحهٔ پرداخت آزمایشی آکساپی</h1>"
                    f"<p>trackId: {track} — وضعیت: {status}</p></body></html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        else:
            self._reply({"status": "error", "message": "not found"}, 404)


PORT = 15856

if __name__ == "__main__":
    if len(sys.argv) > 1:
        PORT = int(sys.argv[1])
    print(f"mock Oxapay merchant API on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
