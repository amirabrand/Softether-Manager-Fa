"use client";

import { useCallback, useState } from "react";
import { CheckRow, ConfirmSheet, Empty, ErrorAlert, Field, LoadingBlock, SectionTitle, usePoll } from "../../components/bits";
import { QuotaCard } from "../../components/QuotaCard";
import { api, type Wire } from "../../lib/api";
import { navigate } from "../../lib/router";
import { HUB_TYPES } from "../../lib/se";
import { useToast } from "../../lib/toast";
import { useT } from "../../lib/i18n";
import { IconTrash } from "../../ui/Icon";

/**
 * The hub object itself: its switch, size, type and password; what it logs;
 * the message shown to connecting clients; and the two expert option lists
 * (admin options and extended options), edited as what they are -- named
 * integers -- with the raw honesty that implies.
 */
export function HubSettings({ hub, onChanged }: { hub: string; onChanged: () => void }) {
  return (
    <>
      <BasicsCard hub={hub} onChanged={onChanged} />
      <QuotaSection hub={hub} onChanged={onChanged} />
      <LogCard hub={hub} />
      <MessageCard hub={hub} />
      <OptionsCard hub={hub} kind="admin" />
      <OptionsCard hub={hub} kind="ext" />
      <DangerCard hub={hub} />
    </>
  );
}

/** The hub's traffic ceiling. Reached, the hub goes offline -- which is a
 *  change to the header's online pill, so the page is told to re-read. */
function QuotaSection({ hub, onChanged }: { hub: string; onChanged: () => void }) {
  const t = useT();
  return (
    <>
      <SectionTitle>{t("Traffic limit")}</SectionTitle>
      <QuotaCard hub={hub} onChanged={onChanged} />
    </>
  );
}

function BasicsCard({ hub, onChanged }: { hub: string; onChanged: () => void }) {
  const t = useT();
  const [data, setData] = useState<Wire | null>(null);
  const [password, setPassword] = useState("");
  const [dirty, setDirty] = useState(false);
  const { guard } = useToast();

  const load = useCallback(async () => {
    const r = await api.hub(hub).catch(() => null);
    if (r) setData((c) => (dirty ? c : r));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hub, dirty]);
  usePoll(load, 3600_000, [hub]);

  if (!data) return <LoadingBlock />;
  const set = (k: string, v: unknown) => {
    setData((d) => ({ ...(d ?? {}), [k]: v }));
    setDirty(true);
  };

  const save = () =>
    guard(async () => {
      const body: Wire = {
        Online_bool: Boolean(data.Online_bool),
        MaxSession_u32: Number(data.MaxSession_u32) || 0,
        NoEnum_bool: Boolean(data.NoEnum_bool),
        HubType_u32: Number(data.HubType_u32) || 0,
      };
      if (password) body.AdminPasswordPlainText_str = password;
      await api.setHub(hub, body);
      setDirty(false);
      setPassword("");
      onChanged();
    }, t("Hub settings saved."));

  return (
    <>
      <SectionTitle>{t("Hub")}</SectionTitle>
      <div className="card" style={{ padding: "var(--s4)", maxWidth: 640 }}>
        <div className="row2">
          <Field label={t("Max concurrent sessions")} hint={t("0 means unlimited.")}>
            <input className="input mono" type="number" min={0} value={Number(data.MaxSession_u32) || 0} onChange={(e) => set("MaxSession_u32", Number(e.target.value))} />
          </Field>
          <Field label={t("Hub type")} hint={t("Static/dynamic matter only in a cluster.")}>
            <select className="select" value={Number(data.HubType_u32) || 0} onChange={(e) => set("HubType_u32", Number(e.target.value))}>
              {Object.entries(HUB_TYPES).map(([v, l]) => (
                <option key={v} value={v}>{t(l)}</option>
              ))}
            </select>
          </Field>
        </div>
        <CheckRow checked={Boolean(data.NoEnum_bool)} onChange={(v) => set("NoEnum_bool", v)}
          label={t("Hide from anonymous enumeration")} hint={t("Clients listing hubs before signing in do not see this one.")} />
        <Field label={t("Hub admin password")} hint={t("Only sent when you type a new one. Lets someone administer just this hub.")}>
          <input className="input" type="password" value={password} onChange={(e) => { setPassword(e.target.value); setDirty(true); }} autoComplete="off" />
        </Field>
        {dirty && (
          <button className="btn btn--primary" onClick={() => void save()}>{t("Save hub settings")}</button>
        )}
      </div>
    </>
  );
}

const SWITCH_TYPES = [
  { v: 0, l: "one file (no switching)" },
  { v: 1, l: "per second" },
  { v: 2, l: "per minute" },
  { v: 3, l: "per hour" },
  { v: 4, l: "per day" },
  { v: 5, l: "per month" },
];

const PACKET_KINDS = [
  { i: 0, l: "TCP connections" },
  { i: 1, l: "All TCP packets" },
  { i: 2, l: "DHCP" },
  { i: 3, l: "UDP" },
  { i: 4, l: "ICMP" },
  { i: 5, l: "IP" },
  { i: 6, l: "ARP" },
  { i: 7, l: "Ethernet" },
];

function LogCard({ hub }: { hub: string }) {
  const t = useT();
  const [data, setData] = useState<Wire | null>(null);
  const [dirty, setDirty] = useState(false);
  const { guard } = useToast();

  const load = useCallback(async () => {
    const r = await api.hubLog(hub).catch(() => null);
    if (r) setData((c) => (dirty ? c : r));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hub, dirty]);
  usePoll(load, 3600_000, [hub]);

  if (!data) return null;
  const set = (k: string, v: unknown) => {
    setData((d) => ({ ...(d ?? {}), [k]: v }));
    setDirty(true);
  };
  const packetConfig: number[] = Array.isArray(data.PacketLogConfig_u32) ? [...(data.PacketLogConfig_u32 as number[])] : new Array(16).fill(0);

  const save = () =>
    guard(async () => {
      await api.setHubLog(hub, {
        SaveSecurityLog_bool: Boolean(data.SaveSecurityLog_bool),
        SecurityLogSwitchType_u32: Number(data.SecurityLogSwitchType_u32) || 0,
        SavePacketLog_bool: Boolean(data.SavePacketLog_bool),
        PacketLogSwitchType_u32: Number(data.PacketLogSwitchType_u32) || 0,
        PacketLogConfig_u32: packetConfig,
      });
      setDirty(false);
    }, t("Log settings saved."));

  return (
    <>
      <SectionTitle>{t("Logging")}</SectionTitle>
      <div className="card" style={{ padding: "var(--s4)", maxWidth: 640 }}>
        <CheckRow checked={Boolean(data.SaveSecurityLog_bool)} onChange={(v) => set("SaveSecurityLog_bool", v)}
          label={t("Security log")} hint={t("Logins, disconnections, administrative changes.")} />
        {Boolean(data.SaveSecurityLog_bool) && (
          <Field label={t("Rotate security log")}>
            <select className="select" value={Number(data.SecurityLogSwitchType_u32) || 0} onChange={(e) => set("SecurityLogSwitchType_u32", Number(e.target.value))}>
              {SWITCH_TYPES.map((s) => <option key={s.v} value={s.v}>{t(s.l)}</option>)}
            </select>
          </Field>
        )}
        <CheckRow checked={Boolean(data.SavePacketLog_bool)} onChange={(v) => set("SavePacketLog_bool", v)}
          label={t("Packet log")} hint={t("Per-packet records — powerful and heavy. Choose kinds below.")} />
        {Boolean(data.SavePacketLog_bool) && (
          <>
            <Field label={t("Rotate packet log")}>
              <select className="select" value={Number(data.PacketLogSwitchType_u32) || 0} onChange={(e) => set("PacketLogSwitchType_u32", Number(e.target.value))}>
                {SWITCH_TYPES.map((s) => <option key={s.v} value={s.v}>{t(s.l)}</option>)}
              </select>
            </Field>
            {PACKET_KINDS.map((k) => (
              <Field key={k.i} label={t(k.l)}>
                <div className="seg">
                  {["off", "headers", "full"].map((lbl, v) => (
                    <button
                      key={lbl}
                      className={packetConfig[k.i] === v ? "on" : ""}
                      onClick={() => {
                        packetConfig[k.i] = v;
                        set("PacketLogConfig_u32", packetConfig);
                      }}
                    >
                      {t(lbl)}
                    </button>
                  ))}
                </div>
              </Field>
            ))}
          </>
        )}
        {dirty && <button className="btn btn--primary" onClick={() => void save()}>{t("Save log settings")}</button>}
      </div>
    </>
  );
}

function MessageCard({ hub }: { hub: string }) {
  const t = useT();
  const [message, setMessage] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const { guard } = useToast();

  const load = useCallback(async () => {
    const r = await api.hubMsg(hub).catch(() => null);
    if (r) setMessage((c) => (dirty ? c : r.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hub, dirty]);
  usePoll(load, 3600_000, [hub]);

  if (message === null) return null;
  return (
    <>
      <SectionTitle>{t("Message of the day")}</SectionTitle>
      <div className="card" style={{ padding: "var(--s4)", maxWidth: 640 }}>
        <Field label={t("Shown to clients when they connect")} hint={t("Empty shows nothing.")}>
          <textarea className="textarea" rows={4} value={message} onChange={(e) => { setMessage(e.target.value); setDirty(true); }} />
        </Field>
        {dirty && (
          <button
            className="btn btn--primary"
            onClick={() => void guard(() => api.setHubMsg(hub, message), t("Message saved.")).then(() => setDirty(false))}
          >
            {t("Save message")}
          </button>
        )}
      </div>
    </>
  );
}

/**
 * Admin options and extended options are the same wire shape: a list of
 * {name, value} integers. They are expert switches; the honest UI is the
 * list itself, editable in place, with the reference one click away.
 */
function OptionsCard({ hub, kind }: { hub: string; kind: "admin" | "ext" }) {
  const t = useT();
  const [items, setItems] = useState<Wire[] | null>(null);
  const [dirty, setDirty] = useState(false);
  const [open, setOpen] = useState(false);
  const { guard } = useToast();

  const load = useCallback(async () => {
    const r = await (kind === "admin" ? api.adminOptions(hub) : api.extOptions(hub)).catch(() => null);
    if (r) setItems((c) => (dirty ? c : ((r.AdminOptionList as Wire[]) ?? [])));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hub, kind, dirty]);
  usePoll(load, 3600_000, [hub, kind]);

  if (items === null) return null;

  const save = () =>
    guard(async () => {
      const body = { AdminOptionList: items.map((i) => ({ Name_str: i.Name_str, Value_u32: Number(i.Value_u32) || 0 })) };
      await (kind === "admin" ? api.setAdminOptions(hub, body) : api.setExtOptions(hub, body));
      setDirty(false);
    }, t("Options saved."));

  return (
    <>
      <SectionTitle
        count={items.length}
        actions={
          <button className="btn btn--sm" onClick={() => setOpen((o) => !o)}>
            {open ? t("Collapse") : t("Expand")}
          </button>
        }
      >
        {kind === "admin" ? t("Administration options") : t("Extended options")}
      </SectionTitle>
      {open && (
        <div className="card" style={{ padding: "var(--s4)" }}>
          <div className="lede" style={{ marginBottom: "var(--s3)" }}>
            {kind === "admin"
              ? t("Limits on what a hub administrator may do. Non-zero enables the named restriction.")
              : t("Per-hub behaviour switches, exactly as SoftEther names them.")}
          </div>
          <div className="optgrid">
            {items.map((item, index) => (
              <label key={String(item.Name_str)} className="optrow">
                <span className="mono truncate" title={String(item.Name_str)}>{String(item.Name_str)}</span>
                <input
                  className="input mono"
                  type="number"
                  value={Number(item.Value_u32) || 0}
                  onChange={(e) => {
                    const next = [...items];
                    next[index] = { ...item, Value_u32: Number(e.target.value) };
                    setItems(next);
                    setDirty(true);
                  }}
                />
              </label>
            ))}
          </div>
          {items.length === 0 && <Empty title={t("the server reports no options")} />}
          {dirty && (
            <button className="btn btn--primary" style={{ marginTop: "var(--s3)" }} onClick={() => void save()}>
              {t("Save options")}
            </button>
          )}
        </div>
      )}
    </>
  );
}

function DangerCard({ hub }: { hub: string }) {
  const t = useT();
  const [deleting, setDeleting] = useState(false);
  const { push } = useToast();
  return (
    <div className="danger">
      <button className="btn btn--ghost" onClick={() => setDeleting(true)}>
        <IconTrash size={14} /> {t("Delete this hub")}
      </button>
      {deleting && (
        <ConfirmSheet
          title={t("Delete hub {name}?", { name: hub })}
          verb={t("Delete hub")}
          typed={hub}
          body={
            <>
              {t("Every user, group, session, rule and setting inside")} <b>{hub}</b>{" "}
              {t("is destroyed with it. There is no undo.")}
            </>
          }
          onClose={() => setDeleting(false)}
          onConfirm={async () => {
            await api.deleteHub(hub);
            push("ok", t("Hub {name} deleted.", { name: hub }));
            navigate(`/`);
          }}
        />
      )}
    </div>
  );
}
