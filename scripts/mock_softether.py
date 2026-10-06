#!/usr/bin/env python3
"""A tiny in-memory SoftEther VPN Server stand-in for UI testing.

Serves the panel's JSON-RPC surface over HTTPS on 127.0.0.1:15555, accepts any
administrator password, and keeps just enough state (hubs, groups, users,
sessions, per-user traffic counters) for the Groups, Users, Sales and quota
screens to behave like the real thing:

    python3 scripts/mock_softether.py            # runs until Ctrl+C

Groups created through the panel show up in EnumGroup, sessions created
through the debug channel show up in EnumSession, and every user carries
cumulative byte counters that creep upward on their own, so usage bars move
without anybody clicking. A small control path (POST /debug, same TLS
listener) seeds the state a demo needs:

    curl -k https://127.0.0.1:15555/debug -d '{"op":"state"}'
    curl -k https://127.0.0.1:15555/debug -d '{"op":"traffic","user":"ali01","send":0,"recv":97000000000}'
    curl -k https://127.0.0.1:15555/debug -d '{"op":"session_add","user":"ali01","n":2}'
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import ssl
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 15555

HUBS = [
    {
        "HubName_str": "VPN",
        "Online_bool": True,
        "HubType_u32": 0,
        "NumUsers_u32": 0,
        "NumSessions_u32": 0,
        "NumGroups_u32": 0,
        "NumMacTables_u32": 0,
        "NumIpTables_u32": 0,
        "NumLogin_u32": 0,
        "LastLoginTime_dt": "2025-01-01T00:00:00.000",
        "CreatedTime_dt": "2025-01-01T00:00:00.000",
    }
]
GROUPS: list[dict] = []
USERS: list[dict] = []
SESSIONS: list[dict] = []
LOCK = threading.Lock()

#: Bytes each user quietly moves between reads, so counters look alive.
CREEP_BYTES = 12 * 1024 * 1024


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000")


def _bump_traffic() -> None:
    """Counters creep: every read leaves the users a little busier."""
    for u in USERS:
        u["_send"] = int(u.get("_send", 0)) + CREEP_BYTES
        u["_recv"] = int(u.get("_recv", 0)) + CREEP_BYTES * 3


def _counter_fields(user: dict) -> dict:
    """The cumulative byte counters, in every shape the panel reads them."""
    send = int(user.get("_send", 0))
    recv = int(user.get("_recv", 0))
    return {
        "Send.UnicastBytes_u64": send,
        "Send.BroadcastBytes_u64": 0,
        "Recv.UnicastBytes_u64": recv,
        "Recv.BroadcastBytes_u64": 0,
        "Ex.Send.UnicastBytes_u64": send,
        "Ex.Send.BroadcastBytes_u64": 0,
        "Ex.Recv.UnicastBytes_u64": recv,
        "Ex.Recv.BroadcastBytes_u64": 0,
    }


def make_cert():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    directory = tempfile.mkdtemp(prefix="se-mock-")
    cert_path = os.path.join(directory, "cert.pem")
    key_path = os.path.join(directory, "key.pem")
    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as fh:
        fh.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
    return cert_path, key_path


CERT, KEY = make_cert()
FINGERPRINT = hashlib.sha256(open(CERT, "rb").read()).hexdigest()


def result_for(method: str, params: dict) -> dict:
    """Stateful canned answers, echoing params for everything else."""
    hub = params.get("HubName_str", "VPN")
    with LOCK:
        if method in ("EnumUser", "GetUser", "EnumSession", "GetHubStatus"):
            _bump_traffic()
        for s in SESSIONS:
            s["_send"] = int(s.get("_send", 0)) + CREEP_BYTES * 2
            s["_recv"] = int(s.get("_recv", 0)) + CREEP_BYTES * 6
        if method == "EnumHub":
            return {"HubList": HUBS, "NumHub_u32": len(HUBS)}
        if method == "GetHub" or method == "GetHubStatus":
            send = sum(int(u.get("_send", 0)) for u in USERS)
            recv = sum(int(u.get("_recv", 0)) for u in USERS)
            return {
                "HubName_str": hub,
                "Name_str": hub,
                "Online_bool": True,
                "HubType_u32": 0,
                "Type_u32": 0,
                "NumUsers_u32": len(USERS),
                "NumGroups_u32": len(GROUPS),
                "NumSessions_u32": len(SESSIONS),
                "LastLoginTime_dt": "2025-01-01T00:00:00.000",
                "CreatedTime_dt": "2025-01-01T00:00:00.000",
                "Send.UnicastBytes_u64": send,
                "Send.BroadcastBytes_u64": 0,
                "Recv.UnicastBytes_u64": recv,
                "Recv.BroadcastBytes_u64": 0,
            }
        if method == "EnumGroup":
            return {"GroupList": GROUPS, "NumGroups_u32": len(GROUPS)}
        if method == "CreateGroup":
            entry = {
                "Name_str": params.get("Name_str", ""),
                "Realname_utf": params.get("Realname_utf", ""),
                "Note_utf": params.get("Note_utf", ""),
                "NumUsers_u32": 0,
            }
            GROUPS[:] = [g for g in GROUPS if g["Name_str"] != entry["Name_str"]]
            GROUPS.append(entry)
            return params
        if method == "GetGroup":
            for g in GROUPS:
                if g["Name_str"] == params.get("Name_str"):
                    out = dict(g)
                    out.pop("NumUsers_u32", None)
                    return out
            return {"Name_str": params.get("Name_str", ""), "Realname_utf": "", "Note_utf": ""}
        if method == "DeleteGroup":
            GROUPS[:] = [g for g in GROUPS if g["Name_str"] != params.get("Name_str")]
            return params
        if method == "EnumUser":
            listed = [dict(u, **_counter_fields(u)) for u in USERS]
            return {"UserList": listed, "NumUsers_u32": len(listed)}
        if method == "GetUser":
            for u in USERS:
                if u["Name_str"].lower() == str(params.get("Name_str", "")).lower():
                    return dict(u, **_counter_fields(u))
            return {}
        if method == "SetUser":
            for i, u in enumerate(USERS):
                if u["Name_str"].lower() == str(params.get("Name_str", "")).lower():
                    USERS[i] = {**u, **{k: v for k, v in params.items()
                                        if not k.startswith("HubName")}}
                    return params
            return params
        if method == "CreateUser":
            USERS.append({
                "Name_str": params.get("Name_str", ""),
                "Realname_utf": params.get("Realname_utf", ""),
                "Note_utf": params.get("Note_utf", ""),
                "GroupName_str": params.get("GroupName_str", ""),
                "AuthType_u32": params.get("AuthType_u32", 1),
                "ExpireTime_dt": params.get("ExpireTime_dt", "1970-01-01T00:00:00.000Z"),
                "NumLogin_u32": 0,
                "_send": 0,
                "_recv": 0,
            })
            return params
        if method == "DeleteUser":
            USERS[:] = [u for u in USERS
                        if u["Name_str"].lower() != str(params.get("Name_str", "")).lower()]
            SESSIONS[:] = [s for s in SESSIONS
                           if s["Username_str"].lower() != str(params.get("Name_str", "")).lower()]
            return params
        if method == "EnumSession":
            listed = [
                {
                    "Name_str": s["Name_str"],
                    "Username_str": s["Username_str"],
                    "ClientIP_ip": s.get("ClientIP_ip", "5.6.7.8"),
                    "Hostname_str": s.get("Hostname_str", "mock-client"),
                    "CreatedTime_dt": s.get("CreatedTime_dt", _now()),
                    "UniqueId_bin": s["UniqueId_bin"],
                    "PacketSize_u64": int(s.get("_send", 0)) + int(s.get("_recv", 0)),
                    "PacketNum_u64": (int(s.get("_send", 0)) + int(s.get("_recv", 0))) // 1400,
                }
                for s in SESSIONS
            ]
            return {"SessionList": listed, "NumSession_u32": len(listed)}
        if method == "DeleteSession":
            SESSIONS[:] = [s for s in SESSIONS if s["Name_str"] != params.get("Name_str")]
            return params
        if method == "GetSessionStatus":
            for s in SESSIONS:
                if s["Name_str"] == params.get("Name_str"):
                    # SoftEther counts from its own side: what it sent is the
                    # client's download.
                    return {
                        "Name_str": s["Name_str"],
                        "HubName_str": hub,
                        "Username_str": s["Username_str"],
                        "ClientIP_ip": s.get("ClientIP_ip", "5.6.7.8"),
                        "CreatedTime_dt": s.get("CreatedTime_dt", _now()),
                        "TotalSendSize_u64": int(s.get("_send", 0)),
                        "TotalRecvSize_u64": int(s.get("_recv", 0)),
                    }
            return {"Name_str": params.get("Name_str", "")}
        if method == "GetServerInfo":
            return {
                "ServerVersion_int": 4,
                "ServerBuildInt": 9807,
                "ServerHostName_str": "mock-server",
                "ServerType_u32": 0,
                "IsInVm_bool": False,
            }
        if method == "GetServerStatus":
            return {
                "ServerType_u32": 0,
                "NumSessionsTotal_u32": len(SESSIONS),
                "NumTcpConnections_u32": len(SESSIONS),
                "NumClients_u32": len(SESSIONS),
                "StartTime_dt": "2025-01-01T00:00:00.000",
                "Traffic_Send_U64": sum(int(u.get("_send", 0)) for u in USERS),
                "Traffic_Recv_U64": sum(int(u.get("_recv", 0)) for u in USERS),
            }
        # Everything else: the params themselves, the shape CreateHub-style RPCs
        # merely round-trip.
        return params


def debug_op(payload: dict) -> dict:
    """Seed/dump the mock's state, for demo bootstrapping and tests."""
    op = payload.get("op", "")
    with LOCK:
        if op == "state":
            return {
                "users": [
                    {
                        "name": u["Name_str"],
                        "send": int(u.get("_send", 0)),
                        "recv": int(u.get("_recv", 0)),
                        "sessions": sum(1 for s in SESSIONS
                                        if s["Username_str"].lower() == u["Name_str"].lower()),
                    }
                    for u in USERS
                ],
                "sessions": [
                    {"name": s["Name_str"], "user": s["Username_str"]} for s in SESSIONS
                ],
            }
        if op == "traffic":
            name = str(payload.get("user", "")).lower()
            for u in USERS:
                if u["Name_str"].lower() == name:
                    if "send" in payload:
                        u["_send"] = int(payload["send"])
                    if "recv" in payload:
                        u["_recv"] = int(payload["recv"])
                    return {"ok": True, "user": u["Name_str"],
                            "send": u["_send"], "recv": u["_recv"]}
            return {"ok": False, "error": "no such user"}
        if op == "session_add":
            name = str(payload.get("user", ""))
            n = int(payload.get("n", 1))
            made = []
            for _ in range(n):
                sid = f"SID-{uuid.uuid4().hex[:12].upper()}"
                SESSIONS.append({
                    "Name_str": sid,
                    "Username_str": name,
                    "ClientIP_ip": payload.get("ip", f"5.6.7.{1 + len(SESSIONS) % 200}"),
                    "Hostname_str": payload.get("hostname", f"client-{len(SESSIONS) + 1}"),
                    "CreatedTime_dt": _now(),
                    "UniqueId_bin": base64.b64encode(uuid.uuid4().bytes).decode(),
                    "_send": 0,
                    "_recv": 0,
                })
                made.append(sid)
            return {"ok": True, "added": made}
        if op == "session_del":
            SESSIONS[:] = [s for s in SESSIONS if s["Name_str"] != payload.get("name")]
            return {"ok": True}
        if op == "sessions_clear":
            SESSIONS[:] = []
            return {"ok": True}
        if op == "reset":
            USERS[:] = []
            SESSIONS[:] = []
            return {"ok": True}
        return {"ok": False, "error": f"unknown op {op!r}"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if self.path == "/debug":
            # The seeding channel: a test tool talking to itself on loopback.
            return self.send_body(200, json.dumps(debug_op(payload)).encode("utf-8"))
        password = self.headers.get("X-VPNADMIN-PASSWORD")
        auth = self.headers.get("Authorization")
        if auth and auth.startswith("Basic "):
            password = base64.b64decode(auth[6:]).decode().split(":", 1)[1]
        if password is None:
            return self.send_body(401, b'{"error":"unauthorized"}')
        body = {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "result": result_for(payload.get("method", ""), payload.get("params", {}) or {}),
        }
        self.send_body(200, json.dumps(body).encode("utf-8"))

    def send_body(self, status: int, raw: bytes):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(CERT, KEY)
httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
httpd.daemon_threads = True
httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
print(f"mock SoftEther RPC on https://127.0.0.1:{PORT} (any admin password accepted)")
print(f"cert fingerprint (sha256): {FINGERPRINT}")
httpd.serve_forever(poll_interval=0.05)
