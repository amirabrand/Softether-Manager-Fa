"use client";

import { useCallback, useEffect, useState } from "react";
import { Empty, Field, LoadingBlock, PageHead, SectionTitle } from "../components/bits";
import { api, type Wire } from "../lib/api";
import { useT } from "../lib/i18n";
import { useToast } from "../lib/toast";
import { IconBolt, IconPlus } from "../ui/Icon";

/** The panel's view of the shop bot: config + health + the order/link books. */
type TgConfig = {
  BotUsername_str: string;
  Running_b: boolean;
  Error_utf: string;
  BotToken_mask: string;
  AdminChat_str: string;
  PayNote_utf: string;
  ReminderDays_str: string;
  Enabled_b: boolean;
};

type TgOrder = {
  TgOrderID: number;
  ChatID: string;
  TgName: string;
  HubName: string;
  UserName: string;
  GroupName: string;
  Months: number;
  Price: number;
  Currency: string;
  VolumeGB: number;
  MaxOnline: number;
  Kind: string;
  Status: string;
  Note: string;
  CreatedDate: string;
  DecidedBy: string;
};

type TgLink = {
  TgLinkID: number;
  ChatID: string;
  HubName: string;
  UserName: string;
  TgName: string;
  CreatedDate: string;
};

const fmtDate = (iso: string) => String(iso || "").slice(0, 16).replace("T", " ");
const fmtNum = (n: number) => new Intl.NumberFormat("fa-IR-u-ca-gregory").format(n);

/**
 * The Telegram bot's control room: set the token and the operator chat,
 * watch the poller's health, decide the orders customers placed in the bot
 * (an approval creates or extends the account exactly like the till does)
 * and manage which chats are linked to which VPN accounts.
 */
export function TelegramBot() {
  const t = useT();
  const { push } = useToast();

  const [cfg, setCfg] = useState<TgConfig | null>(null);
  const [token, setToken] = useState("");
  const [adminChat, setAdminChat] = useState("");
  const [payNote, setPayNote] = useState("");
  const [reminderDays, setReminderDays] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [saving, setSaving] = useState(false);

  const [orders, setOrders] = useState<TgOrder[] | null>(null);
  const [pending, setPending] = useState(0);
  const [filter, setFilter] = useState("");
  const [links, setLinks] = useState<TgLink[] | null>(null);

  const [bindUser, setBindUser] = useState("");
  const [bindUrl, setBindUrl] = useState("");
  const [bindBusy, setBindBusy] = useState(false);

  const loadConfig = useCallback(async () => {
    const out = await api.telegramConfig().catch(() => null);
    if (!out) return;
    setCfg(out as TgConfig);
    setAdminChat(String(out.AdminChat_str ?? ""));
    setPayNote(String(out.PayNote_utf ?? ""));
    setReminderDays(String(out.ReminderDays_str ?? ""));
    setEnabled(Boolean(out.Enabled_b));
  }, []);

  const loadOrders = useCallback(async () => {
    const out = await api.telegramOrders(filter).catch(() => null);
    if (!out) return;
    setOrders((out.OrderList ?? []) as TgOrder[]);
    setPending(Number(out.Pending_u32) || 0);
  }, [filter]);

  const loadLinks = useCallback(async () => {
    const out = await api.telegramLinks().catch(() => null);
    if (out) setLinks((out.LinkList ?? []) as TgLink[]);
  }, []);

  useEffect(() => {
    void loadConfig();
    void loadOrders();
    void loadLinks();
  }, [loadConfig, loadOrders, loadLinks]);

  const save = async () => {
    setSaving(true);
    try {
      await api.telegramSave({
        ...(token.trim() ? { bot_token: token.trim() } : {}),
        admin_chat: adminChat.trim(),
        pay_note: payNote,
        reminder_days: reminderDays.trim(),
        enabled,
      });
      setToken("");
      push("ok", t("Bot settings saved."));
      void loadConfig();
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    } finally {
      setSaving(false);
    }
  };

  const decide = async (o: TgOrder, ok: boolean) => {
    try {
      if (ok) {
        await api.telegramApprove(o.TgOrderID);
        push("ok", t("Order approved — the account is ready."));
      } else {
        await api.telegramReject(o.TgOrderID);
        push("ok", t("Order rejected."));
      }
      void loadOrders();
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    }
  };

  const unlink = async (l: TgLink) => {
    try {
      await api.telegramUnlink(l.TgLinkID);
      void loadLinks();
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    }
  };

  const runReminders = async () => {
    try {
      const out = await api.telegramRunReminders();
      push("ok", t("{n} reminder(s) sent.", { n: fmtNum(out.Sent_u32) }));
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    }
  };

  const makeBind = async () => {
    setBindBusy(true);
    setBindUrl("");
    try {
      const out = await api.telegramBindLink(bindUser.trim());
      setBindUrl(out.url);
      push("ok", t("Link created — send it to the customer. It works once and expires in a day."));
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    } finally {
      setBindBusy(false);
    }
  };

  const statusPill = cfg && (
    <span className={`pill ${cfg.Running_b ? "pill--ok" : "pill--idle"}`}>
      {cfg.Running_b ? t("running") : t("stopped")}
    </span>
  );

  return (
    <div className="page">
      <PageHead
        title={t("Telegram bot")}
        sub={t("The shop in Telegram: customers order, you approve, reminders go out on their own.")}
        actions={<IconBolt size={22} />}
      />

      {/* -------- configuration -------- */}
      <div className="card pad" style={{ marginBottom: "var(--s4)" }}>
        <SectionTitle>{t("Bot configuration")}</SectionTitle>
        {cfg === null ? (
          <LoadingBlock label={t("loading")} />
        ) : (
          <>
            <div className="tg-status">
              {statusPill}
              {cfg.BotUsername_str && (
                <span className="tmono">@{cfg.BotUsername_str}</span>
              )}
              {cfg.Error_utf && (
                <span className="pill pill--err" title={cfg.Error_utf}>{t("connection error")}</span>
              )}
              {cfg.BotToken_mask && <span className="tsub mono">{cfg.BotToken_mask}</span>}
            </div>
            <div className="grid2">
              <Field
                label={t("Bot token")}
                hint={t("From @BotFather. Leave empty to keep the saved one.")}
              >
                <input
                  className="input mono"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder={cfg.BotToken_mask || "123456:ABC-DEF..."}
                  autoCapitalize="none"
                  spellCheck={false}
                />
              </Field>
              <Field
                label={t("Admin chat id")}
                hint={t("Your chat id — forward any message of yours to @userinfobot. Orders arrive here.")}
              >
                <input
                  className="input mono"
                  value={adminChat}
                  onChange={(e) => setAdminChat(e.target.value)}
                  placeholder="123456789"
                />
              </Field>
              <Field
                label={t("Reminder days")}
                hint={t("Comma list of days before expiry; 0 adds an expired notice. Empty = off.")}
              >
                <input
                  className="input mono"
                  value={reminderDays}
                  onChange={(e) => setReminderDays(e.target.value)}
                  placeholder="7,3,1"
                />
              </Field>
              <Field label={t("Enabled")}>
                <label className="checkline">
                  <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
                  <span>{t("The bot answers customers and sends reminders.")}</span>
                </label>
              </Field>
            </div>
            <Field
              label={t("Payment note (shown to buyers)")}
              hint={t("Card-to-card instructions, wallet id, anything the buyer must read before paying.")}
            >
              <input className="input" value={payNote} onChange={(e) => setPayNote(e.target.value)} />
            </Field>
            <div style={{ display: "flex", gap: "var(--s2)", marginTop: "var(--s3)" }}>
              <button className="btn btn--primary" disabled={saving} onClick={save}>
                {saving && <span className="spin" style={{ width: 13, height: 13 }} />}
                <IconPlus size={15} /> {t("Save bot settings")}
              </button>
              <button className="btn btn--ghost" onClick={() => void runReminders()}>
                {t("Send reminders now")}
              </button>
            </div>
          </>
        )}
      </div>

      {/* -------- deep-link generator -------- */}
      <div className="card pad" style={{ marginBottom: "var(--s4)" }}>
        <SectionTitle>{t("Connect a customer via link")}</SectionTitle>
        <p className="hint" style={{ marginTop: 0 }}>
          {t(
            "Creates a one-time Telegram link: the customer taps it and their chat is paired with the VPN account — no username typing, and the link dies after one use or a day.",
          )}
        </p>
        <div style={{ display: "flex", gap: "var(--s2)", flexWrap: "wrap" }}>
          <input
            className="input mono"
            style={{ maxWidth: 260 }}
            placeholder={t("VPN username")}
            value={bindUser}
            onChange={(e) => setBindUser(e.target.value)}
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
          />
          <button
            className="btn btn--primary"
            disabled={bindBusy || !bindUser.trim()}
            onClick={() => void makeBind()}
          >
            {bindBusy && <span className="spin" style={{ width: 13, height: 13 }} />}
            {t("Create link")}
          </button>
        </div>
        {bindUrl && (
          <div
            style={{
              display: "flex",
              gap: "var(--s2)",
              marginTop: "var(--s3)",
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <code className="tmono" style={{ wordBreak: "break-all" }}>
              {bindUrl}
            </code>
            <button
              className="btn btn--sm"
              onClick={() => {
                void navigator.clipboard.writeText(bindUrl);
                push("ok", t("Copied."));
              }}
            >
              {t("Copy")}
            </button>
          </div>
        )}
      </div>

      {/* -------- the order book -------- */}
      <div className="card pad" style={{ marginBottom: "var(--s4)" }}>
        <SectionTitle count={orders?.length}>
          {t("Bot orders")}
          {pending > 0 && (
            <span className="chip chip--brand" style={{ marginInlineStart: "var(--s2)" }}>
              {t("{n} pending", { n: fmtNum(pending) })}
            </span>
          )}
        </SectionTitle>
        <div className="chips" style={{ display: "flex", gap: "var(--s2)", flexWrap: "wrap", marginBottom: "var(--s3)" }}>
          {[
            ["", t("All")],
            ["pending", t("Pending")],
            ["approved", t("Approved")],
            ["rejected", t("Rejected")],
          ].map(([v, label]) => (
            <button
              key={v}
              className={`chip${filter === v ? " chip--on" : ""}`}
              onClick={() => setFilter(v)}
            >
              {label}
            </button>
          ))}
        </div>
        {orders === null ? (
          <LoadingBlock label={t("loading")} />
        ) : orders.length === 0 ? (
          <Empty title={t("No orders yet.")}>
            {t("Orders placed in the bot land here; approving one creates or extends the account.")}
          </Empty>
        ) : (
          <div className="tscroll">
            <table className="dtable">
              <thead>
                <tr>
                  <th>#</th>
                  <th>{t("Customer")}</th>
                  <th>{t("VPN username")}</th>
                  <th>{t("Plan")}</th>
                  <th>{t("Price")}</th>
                  <th>{t("Kind")}</th>
                  <th>{t("Date")}</th>
                  <th>{t("Status")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.TgOrderID} style={{ opacity: o.Status === "pending" ? 1 : 0.65 }}>
                    <td className="tmono">{fmtNum(o.TgOrderID)}</td>
                    <td>
                      {o.TgName || "—"}
                      <small className="tsub"> {o.ChatID}</small>
                    </td>
                    <td className="tname mono">{o.UserName}</td>
                    <td>
                      <span className="chip">{o.GroupName || fmtNum(o.Months) + " " + t("months")}</span>
                    </td>
                    <td className="tmono">{fmtNum(o.Price)} {o.Currency}</td>
                    <td>{o.Kind === "renew" ? t("Renewal") : t("New account")}</td>
                    <td className="tmono">{fmtDate(o.CreatedDate)}</td>
                    <td>
                      {o.Status === "pending" ? (
                        <span className="pill pill--warn">{t("Pending")}</span>
                      ) : o.Status === "approved" ? (
                        <span className="pill pill--ok">{t("Approved")}</span>
                      ) : (
                        <span className="pill pill--err">{t("Rejected")}</span>
                      )}
                    </td>
                    <td>
                      {o.Status === "pending" && (
                        <div style={{ display: "flex", gap: "var(--s1)" }}>
                          <button className="btn btn--sm btn--primary" onClick={() => void decide(o, true)}>
                            {t("Approve")}
                          </button>
                          <button className="btn btn--sm btn--ghost" onClick={() => void decide(o, false)}>
                            {t("Reject")}
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* -------- the link book -------- */}
      <div className="card pad">
        <SectionTitle count={links?.length}>{t("Linked accounts")}</SectionTitle>
        {links === null ? (
          <LoadingBlock label={t("loading")} />
        ) : links.length === 0 ? (
          <Empty title={t("No linked accounts.")}>
            {t("When a customer taps «Link account» in the bot, the pairing shows up here.")}
          </Empty>
        ) : (
          <div className="tscroll">
            <table className="dtable">
              <thead>
                <tr>
                  <th>{t("Customer")}</th>
                  <th>{t("Chat id")}</th>
                  <th>{t("VPN username")}</th>
                  <th>{t("Hub")}</th>
                  <th>{t("Date")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {links.map((l) => (
                  <tr key={l.TgLinkID}>
                    <td>{l.TgName || "—"}</td>
                    <td className="tmono">{l.ChatID}</td>
                    <td className="tname mono">{l.UserName}</td>
                    <td><span className="chip">{l.HubName}</span></td>
                    <td className="tmono">{fmtDate(l.CreatedDate)}</td>
                    <td>
                      <button className="btn btn--sm btn--ghost" onClick={() => void unlink(l)}>
                        {t("Unlink")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
