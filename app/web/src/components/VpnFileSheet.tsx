"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Wire } from "../lib/api";
import { useT } from "../lib/i18n";
import { useToast } from "../lib/toast";
import { downloadText } from "../lib/util";
import { IconDownload } from "../ui/Icon";
import { Sheet } from "../ui/Sheet";
import { CheckRow, ErrorAlert, Field } from "./bits";

/**
 * Hand a user their connection: a ready-to-import OpenVPN profile (.ovpn)
 * by default, or a SoftEther VPN Client .vpn file when the operator picks
 * the client-native format.
 *
 * The address defaults to how *you* reached this panel -- the panel lives on
 * the VPN server, so that address usually is the server -- with the DDNS name
 * offered when the server has one. The names come from the templates in
 * Settings and stay editable here. For OpenVPN the credential needs the
 * plain password (the protocol carries it that way); for SoftEther the
 * panel's stored hash is enough.
 */
export function VpnFileSheet({
  hub,
  name,
  onClose,
}: {
  hub: string;
  name: string;
  onClose: () => void;
}) {
  const [kind, setKind] = useState<"ovpn" | "vpn">("ovpn");
  const [host, setHost] = useState(() => window.location.hostname);
  const [port, setPort] = useState<number>(1194);
  const [ports, setPorts] = useState<number[]>([]);
  const [customPort, setCustomPort] = useState(false);
  const [ddnsFqdn, setDdnsFqdn] = useState("");
  const [embed, setEmbed] = useState(true);
  const [credential, setCredential] = useState<{ available: boolean } | null>(null);
  const [password, setPassword] = useState("");
  const [accountName, setAccountName] = useState("");
  const [filename, setFilename] = useState("");
  const [templates, setTemplates] = useState<{ account: string; file: string } | null>(null);
  const [subs, setSubs] = useState<{ host: string; port: number; note: string }[]>([]);
  const namesTouched = useRef({ account: false, file: false });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<{ state: "idle" | "busy" | "yes" | "no" | "unknown" }>({
    state: "idle",
  });
  const { push } = useToast();
  const t = useT();

  useEffect(() => {
    void api
      .listeners()
      .then((r) => {
        const enabled = ((r.ListenerList as Wire[]) ?? [])
          .filter((l) => l.Enables_bool)
          .map((l) => Number(l.Ports_u32))
          .sort((a, b) => a - b);
        setPorts(enabled);
      })
      .catch(() => {});
    void api
      .ddns()
      .then((r) => setDdnsFqdn(String(r.CurrentFqdn_str || "")))
      .catch(() => {});
    void api
      .publicSubdomains()
      .then((r) =>
        setSubs(
          (r.items as Wire[])
            .map((i) => ({ host: String(i.host || ""), port: Number(i.port || 1194), note: String(i.note || "") }))
            .filter((s) => s.host),
        ),
      )
      .catch(() => {});
    void api
      .vpnTemplate()
      .then((r) => {
        setEmbed(Boolean(r.embed_password_default));
        setTemplates({
          account: String(r.account_name_template || "{hub} - {username}"),
          file: String(r.filename_template || "{hub}-{username}"),
        });
      })
      .catch(() => setTemplates({ account: "{hub} - {username}", file: "{hub}-{username}" }));
    void api
      .userCredentialState(hub, name)
      .then(setCredential)
      .catch(() => setCredential({ available: false }));
  }, [hub, name]);

  // The naming templates follow the live host/port until the operator types
  // their own name -- then their text wins.
  const render = (template: string) =>
    template
      .replaceAll("{hub}", hub)
      .replaceAll("{username}", name)
      .replaceAll("{host}", host.trim())
      .replaceAll("{port}", String(port));
  useEffect(() => {
    if (!templates) return;
    if (!namesTouched.current.account) setAccountName(render(templates.account));
    if (!namesTouched.current.file) setFilename(render(templates.file));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templates, host, port]);

  const portChoices = useMemo(() => {
    const base = ports.length ? ports : [443, 992, 1194, 5555];
    // Each format has a natural first choice: OpenVPN's own 1194, the
    // SoftEther client's firewall-friendly 443. Make sure it is offered
    // even when the server does not listen there yet.
    const preferred = kind === "ovpn" ? 1194 : 443;
    return base.includes(preferred) ? base : [preferred, ...base].sort((a, b) => a - b);
  }, [ports, kind]);
  const needsPassword = kind === "vpn" && embed && credential !== null && !credential.available && !password;
  // OpenVPN profiles carry the plain password, so the stored SoftEther hash
  // is not usable there -- only a typed password embeds.
  const ovpnWillEmbed = kind === "ovpn" && embed && Boolean(password);

  // A typed password is checked against the user's real credential (the
  // same hash the server keeps) -- a mismatch here is a guaranteed "user
  // authentication failed" for whoever receives the file, so the sheet
  // says so before the file leaves the panel.
  useEffect(() => {
    const checkable = embed && password.length > 0;
    if (!checkable) {
      setCheck({ state: "idle" });
      return;
    }
    setCheck({ state: "busy" });
    const timer = setTimeout(() => {
      void api
        .userCredentialCheck(hub, name, password)
        .then((r) =>
          setCheck({ state: r.match === null ? "unknown" : r.match ? "yes" : "no" }),
        )
        .catch(() => setCheck({ state: "unknown" }));
    }, 450);
    return () => clearTimeout(timer);
  }, [hub, name, password, embed]);

  const switchKind = (next: "ovpn" | "vpn") => {
    if (next === kind) return;
    setKind(next);
    if (!customPort) setPort(next === "ovpn" ? 1194 : 443);
  };

  const download = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.userVpnFile(hub, name, {
        host: host.trim(),
        port,
        kind,
        embed_password: embed,
        password: embed ? password || undefined : undefined,
        account_name: accountName.trim() || undefined,
        filename: filename.trim() || undefined,
      });
      downloadText(r.filename, r.content);
      push("ok", t("{file} downloaded.", { file: r.filename }));
      if (r.password_mismatch) {
        push("err", t("The embedded password does not match this user's current password — connections with this file will fail. Get the file again with the right password."));
      } else if (kind === "ovpn" && embed && r.embedded === false) {
        push("info", t("The credential was not embedded — the client will ask for it on first connect."));
      }
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet
      title={t("Download connection file")}
      subtitle={t("{name} · hub {hub}", { name, hub })}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>{t("Cancel")}</button>
          <button className="btn btn--primary" onClick={download} disabled={busy || !host.trim() || !port}>
            {busy ? <span className="spin" /> : <IconDownload size={15} />}
            {kind === "ovpn" ? t("Download .ovpn") : t("Download .vpn")}
          </button>
        </>
      }
    >
      <div style={{ display: "grid", gap: "var(--s1)" }}>
        {error && <ErrorAlert>{error}</ErrorAlert>}
        <Field
          label={t("File type")}
          hint={t("OpenVPN runs on every device; SoftEther's client is the server's native one.")}
        >
          <div style={{ display: "flex", gap: "var(--s1)", flexWrap: "wrap" }}>
            <button
              type="button"
              className={kind === "ovpn" ? "btn btn--primary" : "btn"}
              onClick={() => switchKind("ovpn")}
            >
              OpenVPN (‎.ovpn)
            </button>
            <button
              type="button"
              className={kind === "vpn" ? "btn btn--primary" : "btn"}
              onClick={() => switchKind("vpn")}
            >
              SoftEther Client (‎.vpn)
            </button>
          </div>
        </Field>
        <div className="lede" style={{ marginBottom: "var(--s2)" }}>
          {kind === "ovpn"
            ? t("The profile imports into any OpenVPN app — OpenVPN Connect, OpenVPN for Android and the rest — pointed at this server and signed in as")
            : t("The file imports straight into SoftEther VPN Client — one double-click and the connection exists, pointed at this server and signed in as")} <b className="mono">{name}</b>.
        </div>
        {subs.length > 0 && (
          <Field label={t("Connection addresses")} hint={t("Pick one to fill the address below.")}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s1)" }}>
              {subs.map((s) => (
                <button
                  key={s.host}
                  type="button"
                  className="btn"
                  style={{ fontFamily: "var(--mono, monospace)" }}
                  onClick={() => {
                    setHost(s.host);
                    const p = s.port || port;
                    setPort(p);
                    if (!portChoices.includes(p)) setCustomPort(true);
                  }}
                >
                  {s.host}:{s.port}
                  {s.note ? ` — ${s.note}` : ""}
                </button>
              ))}
            </div>
          </Field>
        )}
        <Field
          label={t("Server address")}
          hint={
            ddnsFqdn ? (
              <>
                {t("What the client will dial.")}{" "}
                <button className="linkish" onClick={() => setHost(ddnsFqdn)} type="button">
                  {t("Use the DDNS name ({fqdn})", { fqdn: ddnsFqdn })}
                </button>
              </>
            ) : (
              t("What the client will dial — this machine's public address.")
            )
          }
        >
          <input
            className="input mono"
            value={host}
            onChange={(e) => setHost(e.target.value)}
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            inputMode="url"
          />
        </Field>
        <Field
          label={t("Port")}
          hint={
            kind === "ovpn"
              ? t("OpenVPN's standard port is 1194 over UDP; any other port is served over TCP.")
              : t("Any listening SoftEther port; 443 crosses the most networks.")
          }
        >
          {customPort ? (
            <input
              className="input mono"
              type="number"
              min={1}
              max={65535}
              value={port}
              onChange={(e) => setPort(Number(e.target.value))}
              inputMode="numeric"
            />
          ) : (
            <select
              className="select"
              value={String(port)}
              onChange={(e) => {
                if (e.target.value === "custom") setCustomPort(true);
                else setPort(Number(e.target.value));
              }}
            >
              {portChoices.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
              <option value="custom">{t("other…")}</option>
            </select>
          )}
        </Field>
        <div className="row2">
          <Field label={t("File name")}>
            <input
              className="input mono"
              value={filename}
              onChange={(e) => {
                namesTouched.current.file = true;
                setFilename(e.target.value);
              }}
              spellCheck={false}
            />
          </Field>
          {kind === "vpn" && (
            <Field label={t("Connection name in the client")}>
              <input
                className="input mono"
                value={accountName}
                onChange={(e) => {
                  namesTouched.current.account = true;
                  setAccountName(e.target.value);
                }}
                spellCheck={false}
              />
            </Field>
          )}
        </div>
        <CheckRow
          checked={embed}
          onChange={setEmbed}
          label={t("Embed the password in the file")}
          hint={
            kind === "ovpn"
              ? ovpnWillEmbed
                ? t("The password goes into the file as plain text — OpenVPN's protocol needs it that way. Anyone holding the file can connect.")
                : t("OpenVPN can only carry the plain password — the stored hash will not do. Type the password to embed it; otherwise the file asks on first connect.")
              : credential === null
                ? t("Checking whether the panel holds this user's credential…")
                : credential.available
                  ? t("The panel holds this user's credential — it goes in as SoftEther's own hash, no typing needed. Anyone holding the file can connect.")
                  : t("The panel has not seen this user's password and could not recover it from the server — type it once below and it will be remembered.")
          }
        />
        {embed && (
          <Field
            label={
              kind === "ovpn"
                ? t("Password")
                : credential?.available
                  ? t("Password (only to replace the stored one)")
                  : t("Password")
            }
            hint={
              kind === "ovpn"
                ? t("Carried in the profile exactly as the OpenVPN protocol sends it.")
                : t("Stored hashed, the way the client stores it — never in plain text.")
            }
          >
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="off"
            />
          </Field>
        )}
        {embed && password.length > 0 && (
          <p className={`hint ${check.state === "no" ? "hint--err" : ""}`}>
            {check.state === "busy" && t("Checking the password against the user's account…")}
            {check.state === "yes" && t("The password matches this user's current credential.")}
            {check.state === "no" &&
              t("This password does NOT match the user's current one — a file with it will be refused at connect. Use the password from the sale receipt, or update the user first.")}
          </p>
        )}
        {needsPassword && (
          <p className="hint hint--err">{t("Without the password the download will be refused — or untick embedding to ship the file without a credential.")}</p>
        )}
        {kind === "ovpn" && embed && !password && (
          <p className="hint">{t("No password typed — the file will ask for the username and password on first connect.")}</p>
        )}
      </div>
    </Sheet>
  );
}
