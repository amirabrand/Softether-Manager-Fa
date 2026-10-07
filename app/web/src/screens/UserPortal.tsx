"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { BrandMark } from "../ui/Icon";
import { LangToggle } from "../components/AppShell";
import { useT } from "../lib/i18n";

/**
 * The customer portal (/#/portal).
 *
 * A shop window for VPN users -- they sign in with the credentials their VPN
 * client already uses, and see their own status, usage, expiry and connection
 * files. Deliberately separate from the admin `api.ts`: a portal session
 * lives in its own storage slot and never touches the operator's token, and
 * its 401s clear nothing but the portal session.
 */

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";
const KEY = "sem_portal_token";

interface PortalMe {
  username: string;
  hub: string;
  online: boolean;
  created: string | null;
  expire: string | null;
  last_login: string | null;
  note: string;
  group: string;
  num_login: number;
  quota: {
    has_limit: boolean;
    limit_bytes: number;
    limit: number;
    unit: string;
    metric: string;
    enabled: boolean;
    upload_bytes: number;
    download_bytes: number;
    used_bytes: number;
    remaining_bytes: number | null;
    percent: number;
    exceeded: boolean;
    blocked: boolean;
  } | null;
  hosts: { host: string; port: number; note: string }[];
  bot_username: string;
  panel_version: string;
}

class PortalAuthError extends Error {}

async function portalFetch<T>(path: string, token: string | null, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (init?.body) headers["Content-Type"] = "application/json";
  const res = await fetch(`${BASE}api/v1/portal${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = "";
    try {
      const data = await res.json();
      detail = typeof data?.detail === "string" ? data.detail : String(data?.detail?.message ?? "");
    } catch {
      /* not json */
    }
    if (res.status === 401 && !path.startsWith("/login")) throw new PortalAuthError(detail);
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return (await res.json()) as T;
}

function download(name: string, content: string) {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined || isNaN(n)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = Math.max(0, n);
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "—";
    return `${d.toLocaleDateString("fa-IR")} · ${d.toLocaleTimeString("fa-IR", {
      hour: "2-digit",
      minute: "2-digit",
    })}`;
  } catch {
    return "—";
  }
}

function daysLeft(iso: string | null): number | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  return Math.ceil((d.getTime() - Date.now()) / 86_400_000);
}

export function UserPortal() {
  const t = useT();
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<PortalMe | null>(null);
  const [booted, setBooted] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState("");
  const [dlNote, setDlNote] = useState("");
  const [tgLink, setTgLink] = useState("");
  const [tgPending, setTgPending] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (tok: string) => {
    try {
      const data = await portalFetch<PortalMe>("/me", tok);
      setMe(data);
    } catch (err) {
      if (err instanceof PortalAuthError) {
        localStorage.removeItem(KEY);
        setToken(null);
        setMe(null);
        setTgLink("");
        setError(t("Session ended. Sign in again."));
      } else {
        setError(err instanceof Error ? err.message : t("Something went wrong"));
      }
    }
  }, [t]);

  useEffect(() => {
    const saved = localStorage.getItem(KEY);
    if (saved) {
      setToken(saved);
      load(saved).finally(() => setBooted(true));
    } else {
      setBooted(true);
    }
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [load]);

  useEffect(() => {
    if (!token) return;
    timer.current = setInterval(() => load(token), 60_000);
    return () => {
      if (timer.current) clearInterval(timer.current);
      timer.current = null;
    };
  }, [token, load]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const out = await portalFetch<{ token: string }>("/login", null, {
        method: "POST",
        body: JSON.stringify({ username: username.trim(), password }),
      });
      localStorage.setItem(KEY, out.token);
      setToken(out.token);
      setTgLink("");
      setPassword("");
      await load(out.token);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "";
      if (msg.startsWith("Wrong username")) setError(t("Wrong username or password."));
      else if (msg.startsWith("Too many attempts"))
        setError(t("Too many attempts. Wait a few minutes and try again."));
      else if (msg.startsWith("The portal is turned off")) setError(t("The portal is turned off."));
      else setError(t("Something went wrong"));
    } finally {
      setBusy(false);
    }
  };

  const getFile = async (kind: "ovpn" | "vpn") => {
    if (!token) return;
    setDlNote("");
    try {
      const out = await portalFetch<{ filename: string; content: string; embedded: boolean }>(
        `/connection-file?kind=${kind}`,
        token,
      );
      download(out.filename, out.content);
      if (!out.embedded) setDlNote(t("On first connect the client asks for this username and password."));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Something went wrong"));
    }
  };

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(text);
      setTimeout(() => setCopied(""), 2000);
    } catch {
      /* clipboard unavailable */
    }
  };

  // The one-time deep link: minted on intent (a tap), never on a timer,
  // so a dashboard left open does not fill the bind-code table.
  const ensureTgLink = useCallback(async (): Promise<string> => {
    if (tgLink) return tgLink;
    if (!token) return "";
    setTgPending(true);
    try {
      const out = await portalFetch<{ url: string }>("/telegram-link", token);
      setTgLink(out.url);
      return out.url;
    } catch {
      return "";
    } finally {
      setTgPending(false);
    }
  }, [tgLink, token]);

  const connectBot = async () => {
    const url = await ensureTgLink();
    const target = url || (me ? `https://t.me/${me.bot_username}` : "");
    if (target) window.open(target, "_blank", "noopener,noreferrer");
  };

  const copyBotLink = async () => {
    const url = await ensureTgLink();
    if (url) await copy(url);
  };

  if (!booted) return <div className="loading" />;

  // ------------------------------------------------------------------ login
  if (!token || !me) {
    return (
      <div className="auth">
        <div className="auth__lang">
          <LangToggle />
        </div>
        <div className="auth__card">
          <div className="auth__mark">
            <BrandMark size={38} />
            <div>
              <div className="auth__t">{t("Customer portal")}</div>
              <div className="auth__s">{t("Sign in to your VPN account")}</div>
            </div>
          </div>
          <div className="plate auth__box">
            {error && <div className="alert alert--err">{error}</div>}
            <form onSubmit={submit}>
              <div className="field">
                <label htmlFor="pu">{t("Username")}</label>
                <input
                  id="pu"
                  className="input mono"
                  autoFocus
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="pp">{t("Password")}</label>
                <input
                  id="pp"
                  className="input"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  enterKeyHint="go"
                  required
                  minLength={1}
                />
              </div>
              <button className="btn btn--primary btn--block" disabled={busy} type="submit">
                {busy && <span className="spin" />}
                {t("Sign in")}
              </button>
            </form>
          </div>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------- dashboard
  const left = daysLeft(me.expire);
  const q = me.quota;

  return (
    <div className="portal">
      <div className="portal__head">
        <div className="auth__mark">
          <BrandMark size={34} />
          <div>
            <div className="auth__t">{t("Customer portal")}</div>
            <div className="auth__s mono">{me.username}</div>
          </div>
        </div>
        <div className="portal__tools">
          <LangToggle />
          <button
            className="btn btn--ghost btn--sm"
            onClick={() => {
              localStorage.removeItem(KEY);
              setToken(null);
              setMe(null);
              setTgLink("");
              setError(null);
            }}
          >
            {t("Sign out")}
          </button>
        </div>
      </div>

      {error && (
        <div className="alert alert--err">
          {error}{" "}
          <button className="linkish" onClick={() => setError(null)}>
            {t("Close")}
          </button>
        </div>
      )}

      <div className="portal__status">
        <span className={`pill ${me.online ? "pill--ok" : "pill--busy"}`}>
          {me.online ? t("Online") : t("Offline")}
        </span>
        {q?.blocked && <span className="pill pill--bad">{t("Blocked by traffic limit")}</span>}
        {left !== null && left < 0 && <span className="pill pill--bad">{t("Expired")}</span>}
      </div>

      <div className="portal__grid">
        <div className="plate portal__card">
          <div className="label">{t("Expiry")}</div>
          <div className="portal__big">
            {me.expire ? fmtDate(me.expire) : t("Never expires")}
          </div>
          {left !== null && left >= 0 && (
            <div className={`micro ${left <= 7 ? "portal--warn" : "muted"}`}>
              {t("{days} days left").replace("{days}", String(left))}
            </div>
          )}
        </div>

        <div className="plate portal__card">
          <div className="label">{t("Traffic usage")}</div>
          {q ? (
            <>
              <div className="portal__big mono">{fmtBytes(q.used_bytes)}</div>
              <div className="portal__bar" role="progressbar">
                <div
                  className={`portal__bar-f ${q.percent >= 100 ? "portal--over" : ""}`}
                  style={{ width: `${Math.min(100, Math.max(2, q.percent))}%` }}
                />
              </div>
              <div className="micro muted mono">
                {q.has_limit
                  ? `${fmtBytes(q.used_bytes)} / ${fmtBytes(q.limit_bytes)} · %${Math.round(q.percent)}`
                  : `${fmtBytes(q.used_bytes)} · ${t("No traffic limit set")}`}
              </div>
            </>
          ) : (
            <div className="portal__big mono">{fmtBytes(null)}</div>
          )}
        </div>

        <div className="plate portal__card">
          <div className="label">{t("Last login")}</div>
          <div className="portal__mid">{fmtDate(me.last_login)}</div>
          <div className="micro muted">
            {t("Logins")}: <span className="mono">{me.num_login}</span>
          </div>
        </div>
      </div>

      <div className="plate portal__card">
        <div className="label">{t("Connection files")}</div>
        <div className="portal__btns">
          <button className="btn btn--primary" onClick={() => getFile("ovpn")}>
            {t("Download OpenVPN profile (.ovpn)")}
          </button>
          <button className="btn" onClick={() => getFile("vpn")}>
            {t("Download SoftEther file (.vpn)")}
          </button>
        </div>
        {dlNote && <div className="hint">{dlNote}</div>}
      </div>

      {me.hosts.length > 0 && (
        <div className="plate portal__card">
          <div className="label">{t("Connection addresses")}</div>
          <div className="chiprow">
            {me.hosts.map((h) => (
              <button
                key={`${h.host}:${h.port}`}
                className="chip mono"
                onClick={() => copy(`${h.host}:${h.port}`)}
                title={h.note}
              >
                {h.host}:{h.port}
                {copied === `${h.host}:${h.port}` ? " ✓" : ""}
              </button>
            ))}
          </div>
          <div className="micro muted">{t("Tap an address to copy it")}</div>
        </div>
      )}

      {me.bot_username && (
        <div className="plate portal__card">
          <div className="label">{t("Telegram bot")}</div>
          <div className="portal__btns">
            <button className="btn btn--primary" disabled={tgPending} onClick={connectBot}>
              {tgPending && <span className="spin" />}
              {t("Connect your Telegram")}
            </button>
            <button className="btn" disabled={tgPending} onClick={copyBotLink}>
              {tgLink && copied === tgLink ? t("Copied.") : t("Copy link")}
            </button>
          </div>
          <div className="micro muted">
            {t("One tap opens the bot and pairs this account — expiry reminders arrive in Telegram.")}
          </div>
        </div>
      )}
    </div>
  );
}
