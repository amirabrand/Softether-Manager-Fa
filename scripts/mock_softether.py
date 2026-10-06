#!/usr/bin/env python3
"""A tiny in-memory SoftEther VPN Server stand-in for UI testing.

Serves the panel's JSON-RPC surface over HTTPS on 127.0.0.1:15555, accepts any
administrator password, and keeps just enough state (hubs, groups, users) for
the Groups and Users screens to behave like the real thing:

    python3 scripts/mock_softether.py            # runs until Ctrl+C

Groups created through the panel show up in EnumGroup, so the duration-group
flow can be exercised end to end without a real VPN server.
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
    if method == "EnumHub":
        return {"HubList": HUBS, "NumHub_u32": len(HUBS)}
    if method == "GetHub" or method == "GetHubStatus":
        return {
            "HubName_str": hub,
            "Name_str": hub,
            "Online_bool": True,
            "HubType_u32": 0,
            "Type_u32": 0,
            "NumUsers_u32": len(USERS),
            "NumGroups_u32": len(GROUPS),
            "NumSessions_u32": 0,
            "LastLoginTime_dt": "2025-01-01T00:00:00.000",
            "CreatedTime_dt": "2025-01-01T00:00:00.000",
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
        return {"UserList": USERS, "NumUsers_u32": len(USERS)}
    if method == "GetUser":
        for u in USERS:
            if u["Name_str"].lower() == str(params.get("Name_str", "")).lower():
                return dict(u)
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
        })
        return params
    if method == "EnumSession":
        return {"SessionList": []}
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
            "NumSessionsTotal_u32": 0,
            "NumTcpConnections_u32": 0,
            "NumClients_u32": 0,
            "StartTime_dt": "2025-01-01T00:00:00.000",
            "Traffic_Send_U64": 0,
            "Traffic_Recv_U64": 0,
        }
    # Everything else: the params themselves, the shape CreateHub-style RPCs
    # merely round-trip.
    return params


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
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
