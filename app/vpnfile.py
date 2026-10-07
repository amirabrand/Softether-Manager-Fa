"""Generating SoftEther VPN Client connection-setting files (.vpn).

A .vpn file is the client's own export format: a SoftEther config-syntax
document with a ``ClientOption`` block (where to connect) and a
``ClientAuth`` block (who is connecting). The client imports it in one click,
so the panel can hand a user everything they need to connect.

Every ClientOption the client itself reads (Cedar's ``CiLoadClientOption``)
is generatable here, driven by :data:`DEFAULT_OPTIONS` -- the operator shapes
them once in the Settings editor and every generated file follows.

The password, when embedded, is stored the way the client itself stores it:
``HashedPassword = SHA-0(password + UPPERCASE(username))``, base64. SHA-0 is
the withdrawn 1993 predecessor of SHA-1 -- identical except that the message
schedule lacks SHA-1's one-bit rotation -- and hashlib does not carry it, so
a small implementation lives here. It is verified against the published
SHA-0 test vector on import; SoftEther's is the only reason anyone still
computes it. The server stores the very same hash per user (``HashedKey`` in
vpn_server.config), which is why a credential recovered from the server's
configuration can be embedded without ever knowing the plaintext.

A proxy password travels RC4-scrambled: the client keys RC4 with
``sizeof(char *)`` bytes of the literal ``"EncryptPassword"`` -- eight bytes,
``b"EncryptP"``, on every 64-bit build. SoftEther's own source marks the
truncation "This is not a bug! Do not try to fix it!!", so it is reproduced
here verbatim.
"""
from __future__ import annotations

import base64
import io
import os
import re
import struct
import zipfile
from typing import Any


def sha0(data: bytes) -> bytes:
    """The withdrawn SHA-0: SHA-1 without the rotl(1) in the schedule."""
    h = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0]
    message = data + b"\x80"
    message += b"\x00" * ((56 - len(message) % 64) % 64)
    message += struct.pack(">Q", len(data) * 8)

    def rotl(value: int, count: int) -> int:
        return ((value << count) | (value >> (32 - count))) & 0xFFFFFFFF

    for offset in range(0, len(message), 64):
        w = list(struct.unpack(">16I", message[offset : offset + 64]))
        for i in range(16, 80):
            # SHA-1 would rotate this by one bit; SHA-0 does not.
            w.append(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16])
        a, b, c, d, e = h
        for i in range(80):
            if i < 20:
                f, k = (b & c) | (~b & d), 0x5A827999
            elif i < 40:
                f, k = b ^ c ^ d, 0x6ED9EBA1
            elif i < 60:
                f, k = (b & c) | (b & d) | (c & d), 0x8F1BBCDC
            else:
                f, k = b ^ c ^ d, 0xCA62C1D6
            a, b, c, d, e = (
                (rotl(a, 5) + f + e + k + w[i]) & 0xFFFFFFFF,
                a,
                rotl(b, 30),
                c,
                d,
            )
        h = [(x + y) & 0xFFFFFFFF for x, y in zip(h, (a, b, c, d, e))]
    return struct.pack(">5I", *h)


# The published SHA-0 test vector; a wrong implementation must fail loudly at
# import, not hand users credentials that never authenticate.
assert sha0(b"abc").hex() == "0164b8a914cd2a5e74c4f7ff082c4d97f1edf880"


def hashed_password(password: str, username: str) -> str:
    """SoftEther's stored credential: SHA-0 over password + uppercased user."""
    return base64.b64encode(sha0(password.encode("utf-8") + username.upper().encode("utf-8"))).decode("ascii")


def _rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    out = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out.append(byte ^ s[(s[i] + s[j]) & 0xFF])
    return bytes(out)


#: The client's proxy-password key: "EncryptPassword" truncated to pointer
#: size on 64-bit builds. Kept byte-for-byte, quirk and all.
_PROXY_PASSWORD_KEY = b"EncryptP"


def scrambled_proxy_password(password: str) -> str:
    """A proxy password the way the client writes one: RC4, base64."""
    return base64.b64encode(_rc4(_PROXY_PASSWORD_KEY, password.encode("utf-8"))).decode("ascii")


#: Server-side user auth type -> the client's ClientAuth.AuthType.
#: 0 anonymous, 1 standard password (hashed), 2 RADIUS/NT (plaintext),
#: 3 client certificate (the certificate itself stays on the client).
_CLIENT_AUTH_FOR_USER = {0: 0, 1: 1, 2: 3, 3: 3, 4: 2, 5: 2}


# ---------------------------------------------------------------------------
# the operator-editable options
# ---------------------------------------------------------------------------

#: Every root/ClientOption field an operator can shape, with its type and the
#: client's own default. Hostname, Port, HubName, AccountName and the auth
#: block are generation parameters, not options. Keys the client would not
#: read are refused, so a typo cannot produce a file that silently ignores it.
DEFAULT_OPTIONS: dict[str, Any] = {
    # root
    "CheckServerCert": False,
    "StartupAccount": False,
    # tunnel
    "MaxConnection": 1,
    "UseEncrypt": True,
    "UseCompress": False,
    "HalfConnection": False,
    "NoUdpAcceleration": False,
    "PortUDP": 0,
    # reliability
    "NumRetry": 4294967295,
    "RetryInterval": 15,
    "AdditionalConnectionInterval": 1,
    "ConnectionDisconnectSpan": 0,
    # proxy: 0 direct, 1 HTTP, 2 SOCKS4, 3 SOCKS5
    "ProxyType": 0,
    "ProxyName": "",
    "ProxyPort": 0,
    "ProxyUsername": "",
    "ProxyPassword": "",
    # client behaviour
    "DeviceName": "VPN",
    "NoTls1": False,
    "NoRoutingTracking": False,
    "DisableQoS": False,
    "HideStatusWindow": False,
    "HideNicInfoWindow": False,
    "RequireBridgeRoutingMode": False,
    "RequireMonitorMode": False,
    "CustomHttpHeader": "",
}

#: Bounds for the numeric options; anything outside is clamped, not refused.
_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "MaxConnection": (1, 32),
    "PortUDP": (0, 65535),
    "NumRetry": (0, 4294967295),
    "RetryInterval": (5, 3600),
    "AdditionalConnectionInterval": (1, 3600),
    "ConnectionDisconnectSpan": (0, 4294967295),
    "ProxyType": (0, 3),
    "ProxyPort": (0, 65535),
}


def normalize_options(raw: Any) -> dict[str, Any]:
    """Unknown keys dropped, values coerced to the field's type and bounds."""
    out = dict(DEFAULT_OPTIONS)
    if not isinstance(raw, dict):
        return out
    for key, default in DEFAULT_OPTIONS.items():
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(default, bool):
            out[key] = bool(value)
        elif isinstance(default, int):
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            low, high = _INT_BOUNDS.get(key, (0, 4294967295))
            out[key] = max(low, min(high, number))
        else:
            out[key] = str(value)
    return out


# ---------------------------------------------------------------------------
# naming templates
# ---------------------------------------------------------------------------

#: Placeholders usable in the account-name and file-name templates.
NAME_VARIABLES = ("hub", "username", "host", "port")

DEFAULT_ACCOUNT_NAME_TEMPLATE = "{hub} - {username}"
DEFAULT_FILENAME_TEMPLATE = "{hub}-{username}"


def render_name(template: str, *, hub: str, username: str, host: str = "", port: int | str = "") -> str:
    """Fill a naming template; unknown braces are left alone rather than
    crashing on an operator's stray ``{``."""
    out = template or ""
    for key, value in (("hub", hub), ("username", username), ("host", host), ("port", str(port))):
        out = out.replace("{" + key + "}", value)
    return out.strip()


def safe_filename(name: str, ext: str = ".vpn") -> str:
    keep = "".join(c if c.isalnum() or c in "-_." else "_" for c in name).strip("._") or "connection"
    # Whatever extension the name carried, the caller's kind wins.
    for known in (".vpn", ".ovpn"):
        if keep.lower().endswith(known):
            keep = keep[: -len(known)]
            break
    return f"{keep}{ext}"


# ---------------------------------------------------------------------------
# OpenVPN profiles (.ovpn)
# ---------------------------------------------------------------------------

#: TCP MSS clamp (``mssfix``) written into every generated .ovpn profile.
#
# A customer's TCP sessions ride the OpenVPN tunnel, which itself rides the
# BackPack tunnel: a full-size inner segment (MSS 1460 -> 1500-byte IP packet)
# plus the OpenVPN/tunnel overhead lands past the path MTU, oversized UDP
# datagrams get fragmented (or dropped outright by CGNAT/PPPoE paths) and
# downloads stall mid-transfer while small requests keep working -- the exact
# symptom of a missing clamp. mssfix 1300 keeps the wire packet at ~1392
# bytes, safely under every MTU in the chain (1500 ethernet, 1492 PPPoE,
# ~1430 mobile), in both directions (it rewrites the MSS announced in every
# SYN and SYN-ACK crossing the tunnel).
#
# 0 disables the directive; ``SEM_OVPN_MSSFIX`` overrides the value.
_raw_mssfix = os.environ.get("SEM_OVPN_MSSFIX", "1300").strip()
try:
    OVPN_MSSFIX = max(0, int(_raw_mssfix))
except ValueError:
    OVPN_MSSFIX = 1300


def extract_remote_access_config(zip_bytes: bytes) -> str:
    """The layer-3 (remote access) profile out of the ZIP the server's
    ``MakeOpenVpnConfigFile`` returns.

    The server's own sample is the trustworthy starting point: it already
    carries the ciphers the server actually runs and the server certificate
    inline, so the finished profile verifies against the very server it
    dials. Only the bundle's L3 member is wanted -- the L2 bridge sample and
    the trilingual readme are not part of a user's download.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as bundle:
        names = [n for n in bundle.namelist() if n.endswith("openvpn_remote_access_l3.ovpn")]
        if not names:
            raise ValueError("the server's OpenVPN bundle has no remote-access profile")
        return bundle.read(names[0]).decode("utf-8", "replace")


def build_ovpn_file(
    *,
    base: str,
    host: str,
    port: int,
    hub: str,
    username: str,
    password: str = "",
    embed: bool = True,
) -> str:
    """One importable OpenVPN profile (.ovpn) for this user.

    The panel rewrites *where* to connect and *who* is connecting; everything
    else -- ciphers, keepalive, the inline CA -- is the server's own sample.

    SoftEther's OpenVPN transport picks the hub through the username:
    ``user@hub``; a user of the server's default hub needs no suffix. Port
    1194 dials UDP, which is OpenVPN's own standard; any other port rides a
    TCP listener (SoftEther serves OpenVPN over TCP on every listener).

    Every profile also carries the panel's TCP MSS clamp (``mssfix``,
    :data:`OVPN_MSSFIX`) -- without it bulk downloads stall mid-transfer on
    MTU-constrained paths, because full-size segments plus the tunnel
    overhead exceed what the chain can carry.

    The credential, when embedded, travels as the inline
    ``<auth-user-pass>`` block -- plain text, because that is what the
    OpenVPN protocol itself carries. With ``embed`` off (or no password),
    the sample's bare ``auth-user-pass`` stays and the client asks on first
    connect.
    """
    proto = "udp" if int(port) == 1194 else "tcp"
    auth_name = username if (hub or "").strip().lower() == "default" else f"{username}@{hub}"
    text = base.replace("\r\n", "\n").lstrip("\ufeff")
    text = re.sub(r"(?m)^\s*proto\s+(?:udp|tcp)\s*$", lambda _m: f"proto {proto}", text)
    text = re.sub(r"(?m)^\s*remote\s+\S+\s+\d+\s*$", lambda _m: f"remote {host} {port}", text)
    if embed:
        block = f"<auth-user-pass>\n{auth_name}\n{password}\n</auth-user-pass>"
        text = re.sub(r"(?m)^\s*auth-user-pass\s*$", lambda _m: block, text)
    if OVPN_MSSFIX:
        # One clamp, ours: drop whatever mssfix the sample carried (none,
        # today), then append the panel's own so the value lives in exactly
        # one place and survives the server changing its sample.
        text = re.sub(r"(?m)^\s*mssfix\b[^\n]*$", "", text)
        text = text.rstrip() + (
            "\n\n# MSS clamp: keeps TCP segments small enough for the tunnel\n"
            "# overhead -- without it bulk downloads stall mid-transfer.\n"
            f"mssfix {OVPN_MSSFIX}\n"
        )
    header = (
        "# AMIRITPANEL - OpenVPN connection profile\n"
        f"# Hub: {hub}    User: {username}\n"
        "# Import into any OpenVPN client (OpenVPN Connect, OpenVPN for Android, ...).\n"
        "\n"
    )
    return header + text.strip() + "\n"


# ---------------------------------------------------------------------------
# the document
# ---------------------------------------------------------------------------


def cfg_escape(s: str) -> str:
    """SoftEther's own config-string escaping (``CfgEscape`` in Mayaqua/Cfg.c):
    control characters, space, tab and ``$`` itself become ``$XX`` -- two
    uppercase hex digits -- everything else is left as-is. An empty string is
    the single character ``$``.

    Without this a name with a space -- "VPN - Sattar" -- writes literally
    and the client's own parser splits it on the space; SoftEther writes it
    ``VPN$20-$20Sattar``.
    """
    if not s:
        return "$"
    out = []
    for ch in s:
        code = ord(ch)
        if code <= 31 or ch in (" ", "\t", "$"):
            out.append(f"${code:02X}")
        else:
            out.append(ch)
    return "".join(out)


def _value(v: Any) -> str:
    """One config value, the way the client's CfgSave writes it."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return cfg_escape(str(v))


def _document(
    *,
    host: Any,
    port: Any,
    hub: Any,
    username: Any,
    account: Any,
    auth_type: Any,
    hashed: Any,
    plain: Any,
    options: dict[str, Any],
    proxy_password: Any,
) -> str:
    o = options
    root = [
        ("bool", "CheckServerCert", o["CheckServerCert"]),
        ("uint64", "CreateDateTime", 0),
        ("uint64", "LastConnectDateTime", 0),
        ("bool", "StartupAccount", o["StartupAccount"]),
        ("uint64", "UpdateDateTime", 0),
    ]
    client_auth = [
        ("uint", "AuthType", auth_type),
        ("byte", "HashedPassword", hashed),
        ("string", "PlainPassword", plain),
        ("string", "Username", username),
    ]
    client_option = [
        ("string", "AccountName", account),
        ("uint", "AdditionalConnectionInterval", o["AdditionalConnectionInterval"]),
        ("uint", "ConnectionDisconnectSpan", o["ConnectionDisconnectSpan"]),
        ("string", "CustomHttpHeader", o["CustomHttpHeader"]),
        ("string", "DeviceName", o["DeviceName"]),
        ("bool", "DisableQoS", o["DisableQoS"]),
        ("bool", "HalfConnection", o["HalfConnection"]),
        ("bool", "HideNicInfoWindow", o["HideNicInfoWindow"]),
        ("bool", "HideStatusWindow", o["HideStatusWindow"]),
        ("string", "Hostname", host),
        ("string", "HubName", hub),
        ("uint", "MaxConnection", o["MaxConnection"]),
        ("bool", "NoRoutingTracking", o["NoRoutingTracking"]),
        ("bool", "NoTls1", o["NoTls1"]),
        ("bool", "NoUdpAcceleration", o["NoUdpAcceleration"]),
        ("uint", "NumRetry", o["NumRetry"]),
        ("uint", "Port", port),
        ("uint", "PortUDP", o["PortUDP"]),
        ("string", "ProxyName", o["ProxyName"]),
        ("byte", "ProxyPassword", proxy_password),
        ("uint", "ProxyPort", o["ProxyPort"]),
        ("uint", "ProxyType", o["ProxyType"]),
        ("string", "ProxyUsername", o["ProxyUsername"]),
        ("bool", "RequireBridgeRoutingMode", o["RequireBridgeRoutingMode"]),
        ("bool", "RequireMonitorMode", o["RequireMonitorMode"]),
        ("uint", "RetryInterval", o["RetryInterval"]),
        ("bool", "UseCompress", o["UseCompress"]),
        ("bool", "UseEncrypt", o["UseEncrypt"]),
    ]

    lines = [
        "# VPN Client VPN Connection Setting File",
        "# ",
        "# This file is exported from the AMIRITPANEL panel.",
        "# The contents of this file can be edited using a text editor.",
        "# ",
        "# When this file is imported to the Client Connection Manager it can be used immediately.",
        "",
        "declare root",
        "{",
    ]
    for kind, name, value in root:
        lines.append(f"\t{kind} {name} {_value(value)}")
    lines.append("")
    lines.append("\tdeclare ClientAuth")
    lines.append("\t{")
    for kind, name, value in client_auth:
        lines.append(f"\t\t{kind} {name} {_value(value)}")
    lines.append("\t}")
    lines.append("\tdeclare ClientOption")
    lines.append("\t{")
    for kind, name, value in client_option:
        lines.append(f"\t\t{kind} {name} {_value(value)}")
    lines.append("\t}")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def build_vpn_file(
    *,
    host: str,
    port: int,
    hub: str,
    username: str,
    user_auth_type: int,
    account_name: str = "",
    password: str = "",
    password_hash: str = "",
    options: dict[str, Any] | None = None,
) -> str:
    """One importable .vpn document, CRLF-terminated like the client's own.

    The credential is optional: with neither ``password`` nor
    ``password_hash`` the auth block is left empty and the client asks on
    first connect -- the right shape for a file that travels over channels
    the operator does not control. ``password_hash`` is the stored SoftEther
    hash (base64), embeddable without ever holding the plaintext.
    """
    opts = normalize_options(options)
    auth_type = _CLIENT_AUTH_FOR_USER.get(user_auth_type, 1)
    hashed = ""
    plain = ""
    if password:
        if auth_type == 2:
            plain = password
        else:
            auth_type = 1
            hashed = hashed_password(password, username)
    elif password_hash and auth_type != 2:
        auth_type = 1
        hashed = password_hash

    lines = _document(
        host=host,
        port=port,
        hub=hub,
        username=username,
        account=account_name or render_name(DEFAULT_ACCOUNT_NAME_TEMPLATE, hub=hub, username=username),
        auth_type=auth_type,
        hashed=hashed,
        plain=plain,
        options=opts,
        proxy_password=scrambled_proxy_password(opts["ProxyPassword"]) if opts["ProxyPassword"] else "",
    )
    # The client writes its files CRLF with a UTF-8 BOM; imported files are
    # accepted either way, but matching exactly costs nothing.
    return "﻿" + lines.replace("\n", "\r\n")


def template_text(options: dict[str, Any] | None = None) -> str:
    """The document with its parameter names visible: ``{host}``, ``{port}``…

    This is documentation, not a .vpn file -- the Settings editor shows it so
    an operator sees exactly what the panel writes with the options they
    chose, and where each per-user value lands.
    """
    opts = normalize_options(options)
    return _document(
        host="{host}",
        port="{port}",
        hub="{hub}",
        username="{username}",
        account="{account_name}",
        auth_type="{auth_type}",
        hashed="{hashed_password}",
        plain="{plain_password}",
        options=opts,
        proxy_password="{scrambled}" if opts["ProxyPassword"] else "",
    )
