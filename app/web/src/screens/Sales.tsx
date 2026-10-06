"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Empty, Field, LoadingBlock, PageHead, SectionTitle } from "../components/bits";
import { RenewMenu } from "../components/RenewMenu";
import { api, type Wire } from "../lib/api";
import { DURATION_GROUPS, durationOfGroup, expiryFromMonths } from "../lib/duration";
import { useT } from "../lib/i18n";
import { useToast } from "../lib/toast";
import { useServer } from "../lib/server";
import { formatBytes, formatDate } from "../lib/util";
import { IconCopy, IconPlus, IconSync, IconTag } from "../ui/Icon";

/**
 * What one duration group sells: the price, and what the price buys -- a
 * traffic volume in GB and a concurrent-session ceiling. Zeroes mean
 * unlimited; installs from before the plans existed stored a bare price,
 * and normalize into zeroes here.
 */
type Plan = { price: number; volume_gb: number; max_online: number };

/** The live usage one sold account reports beside the ledger. */
type SaleStatus = {
  hub: string;
  name: string;
  online_now: number;
  max_online: number;
  used_bytes: number;
  volume_bytes: number;
  blocked: boolean;
  over_volume: boolean;
  over_online: boolean;
};

const asPlan = (v: unknown): Plan => {
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    return {
      price: Number(o.price) || 0,
      volume_gb: Number(o.volume_gb) || 0,
      max_online: Number(o.max_online) || 0,
    };
  }
  return { price: Number(v) || 0, volume_gb: 0, max_online: 0 };
};

const statusKey = (hub: unknown, name: unknown) =>
  `${String(hub ?? "")}/${String(name ?? "").toLowerCase()}`;

/**
 * The operator's till.
 *
 * One page answers the whole habit of selling time: the plans (price,
 * volume, online count) live at the side, a sale is one small form, and the
 * ledger underneath remembers every subscription the panel has sold -- who
 * bought it, which duration, how much was charged, and how the account is
 * doing right now: bytes moved against the volume, sessions online against
 * the ceiling. Selling creates (or extends) the VPN user with the group's
 * expiry and applies the plan's limits, so the sale and the server never
 * disagree.
 */
export function Sales() {
  const t = useT();
  const { push } = useToast();
  const { hubs } = useServer();

  // ---- plans ---------------------------------------------------------------
  const [pricing, setPricing] = useState<Record<string, Plan>>({});
  const [currency, setCurrency] = useState("");
  const [pricingDraft, setPricingDraft] = useState<Record<string, Plan>>({});
  const [savingPricing, setSavingPricing] = useState(false);

  // ---- the sale form --------------------------------------------------------
  const [hub, setHub] = useState("");
  const [group, setGroup] = useState("1month");
  const [buyer, setBuyer] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [price, setPrice] = useState("");
  const [volume, setVolume] = useState("");
  const [maxOnline, setMaxOnline] = useState("");
  const [note, setNote] = useState("");
  const [extend, setExtend] = useState(false);
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<null | {
    name: string;
    password: string;
    expire: string;
    volume: number;
    online: number;
  }>(null);

  // ---- ledger ---------------------------------------------------------------
  const [ledger, setLedger] = useState<null | {
    SaleList: Wire[];
    PriceSum_f64: number;
    Status: Record<string, SaleStatus>;
  }>(null);

  const loadLedger = useCallback(async () => {
    const out = await api.sales(200).catch(() => null);
    if (out)
      setLedger(out as { SaleList: Wire[]; PriceSum_f64: number; Status: Record<string, SaleStatus> });
  }, []);

  const applyPlans = useCallback((raw: unknown) => {
    const plans: Record<string, Plan> = {};
    for (const [k, v] of Object.entries((raw as Record<string, unknown>) ?? {})) plans[k] = asPlan(v);
    setPricing(plans);
    setPricingDraft(plans);
    return plans;
  }, []);

  useEffect(() => {
    void (async () => {
      const s = await api.settings().catch(() => null);
      if (s) {
        applyPlans(s.sale_pricing);
        setCurrency(String(s.sale_currency ?? ""));
      }
    })();
    void loadLedger();
  }, [loadLedger, applyPlans]);

  useEffect(() => {
    if (!hub && hubs && hubs.length > 0) setHub(String(hubs[0].HubName_str));
  }, [hubs, hub]);

  const preset = useMemo(() => durationOfGroup(group), [group]);
  const presetMonths = preset?.months ?? 1;
  const fmtNum = useCallback(
    (n: number) => new Intl.NumberFormat("fa-IR-u-ca-gregory").format(n),
    [],
  );

  /** The selected group's plan, from the draft the operator is editing. */
  const planOf = (g: string): Plan => pricingDraft[g] ?? { price: 0, volume_gb: 0, max_online: 0 };

  const pickGroup = (g: string) => {
    setGroup(g);
    const p = planOf(g);
    if (p.price) setPrice(String(p.price));
    setVolume(String(p.volume_gb || 0));
    setMaxOnline(String(p.max_online || 0));
  };

  // When the plans arrive after the page, fill the form once if untouched.
  useEffect(() => {
    const p = pricingDraft[group];
    if (p && price === "" && volume === "" && maxOnline === "") {
      if (p.price) setPrice(String(p.price));
      setVolume(String(p.volume_gb || 0));
      setMaxOnline(String(p.max_online || 0));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pricingDraft]);

  const savePricing = async () => {
    setSavingPricing(true);
    try {
      await api.saveSettings({ sale_pricing: pricingDraft, sale_currency: currency });
      setPricing({ ...pricingDraft });
      push("ok", t("Prices saved."));
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    } finally {
      setSavingPricing(false);
    }
  };

  const suggestPassword = () => {
    const alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789";
    let out = "";
    const buf = new Uint32Array(12);
    crypto.getRandomValues(buf);
    for (const b of buf) out += alphabet[b % alphabet.length];
    return out;
  };

  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    const name = username.trim();
    if (!name) {
      setError(t("A username is required."));
      return;
    }
    setBusy(true);
    try {
      const out = await api.sell(hub, {
        username: name,
        password,
        realname: buyer,
        group,
        months: presetMonths,
        price: Number(price) || 0,
        currency,
        volume_gb: Number(volume) || 0,
        max_online: Number(maxOnline) || 0,
        buyer,
        note,
        extend_if_exists: extend,
      });
      setReceipt({
        name,
        password,
        expire: out.user.ExpireTime_dt,
        volume: Number(volume) || 0,
        online: Number(maxOnline) || 0,
      });
      push("ok", t("Sold {months} months to {name}.", { months: presetMonths, name }));
      setBuyer("");
      setUsername("");
      setPassword("");
      setNote("");
      void loadLedger();
    } catch (e) {
      setError(e instanceof Error ? t(e.message) : String(e));
    } finally {
      setBusy(false);
    }
  };

  const resetUsage = async (st: SaleStatus) => {
    try {
      await api.resetUserTransfer(st.hub, st.name);
      push("ok", t("Usage reset."));
      void loadLedger();
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    }
  };

  const total = ledger ? ledger.PriceSum_f64 : 0;
  const sel = planOf(group);

  const volumeCell = (st?: SaleStatus) => {
    if (!st || (!st.volume_bytes && !st.used_bytes)) return <span className="tsub">—</span>;
    const pct = st.volume_bytes > 0 ? Math.min(100, (st.used_bytes / st.volume_bytes) * 100) : 0;
    const over = st.over_volume || (st.blocked && st.volume_bytes > 0);
    return (
      <div className="volcell">
        <div className={`volbar${over ? " volbar--over" : ""}`}>
          <span className="volbar__fill" style={{ width: `${st.volume_bytes > 0 ? pct : 100}%` }} />
        </div>
        <small>
          {formatBytes(st.used_bytes)}
          {st.volume_bytes > 0 && <> / {formatBytes(st.volume_bytes)}</>}
        </small>
      </div>
    );
  };

  const onlineCell = (st?: SaleStatus) => {
    if (!st) return <span className="tsub">—</span>;
    const over = st.over_online;
    const text =
      st.max_online > 0
        ? `${fmtNum(st.online_now)} / ${fmtNum(st.max_online)}`
        : fmtNum(st.online_now);
    return (
      <span className={`pill ${over ? "pill--warn" : st.online_now > 0 ? "pill--ok" : "pill--idle"}`}>
        {text}
      </span>
    );
  };

  return (
    <div className="page">
      <PageHead
        title={t("Subscription sales")}
        sub={t("Sell time: pick a duration group, charge the price, the account is ready.")}
        actions={<IconTag size={22} />}
      />

      <div className="sales-grid">
        {/* -------- the sale form -------- */}
        <div className="card pad">
          <SectionTitle>{t("New sale")}</SectionTitle>
          {receipt && (
            <div className="alert alert--ok sale-receipt">
              <div>
                <b>{t("Sold.")}</b>{" "}
                {t("{name} — expires {date}", { name: receipt.name, date: formatDate(receipt.expire) })}
                <div className="tsub" style={{ marginTop: 4 }}>
                  {t("Volume")}:{" "}
                  <b>{receipt.volume > 0 ? `${fmtNum(receipt.volume)} GB` : t("unlimited")}</b>
                  {" · "}
                  {t("Online")}:{" "}
                  <b>{receipt.online > 0 ? fmtNum(receipt.online) : t("unlimited")}</b>
                </div>
              </div>
              {receipt.password && (
                <button
                  className="btn btn--sm btn--ghost mono"
                  onClick={() => {
                    void navigator.clipboard?.writeText(receipt.password);
                    push("ok", t("Password copied."));
                  }}
                >
                  <IconCopy size={14} /> {receipt.password}
                </button>
              )}
            </div>
          )}
          {error && <div className="alert alert--warn">{error}</div>}
          <div className="grid2">
            <Field label={t("Hub")}>
              <select className="input" value={hub} onChange={(e) => setHub(e.target.value)}>
                {(hubs ?? []).map((h) => (
                  <option key={String(h.HubName_str)} value={String(h.HubName_str)}>
                    {String(h.HubName_str)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("Duration group")} hint={t("The group sets the security policy; the duration sets the expiry.")}>
              <select className="input" value={group} onChange={(e) => pickGroup(e.target.value)}>
                {DURATION_GROUPS.map((d) => {
                  const p = pricingDraft[d.name];
                  const priceTxt = p ? `${fmtNum(p.price)} ${currency}` : t("no price set");
                  return (
                    <option key={d.name} value={d.name}>
                      {t(d.labelKey)} — {priceTxt}
                    </option>
                  );
                })}
              </select>
            </Field>
            <Field label={t("Buyer name")}>
              <input className="input" value={buyer} onChange={(e) => setBuyer(e.target.value)} />
            </Field>
            <Field
              label={t("VPN username")}
              hint={t("Lowercase letters, digits, dot, dash.")}
            >
              <input
                className="input mono"
                value={username}
                autoCapitalize="none"
                spellCheck={false}
                onChange={(e) => setUsername(e.target.value)}
                placeholder={buyer ? buyer.toLowerCase().replace(/[^a-z0-9._-]+/g, ".") : ""}
              />
            </Field>
            <Field
              label={t("Price")}
              hint={t("Preset from the price list; change it for one sale only.")}
            >
              <input
                className="input mono"
                type="number"
                min={0}
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                placeholder={sel.price ? String(sel.price) : "0"}
              />
            </Field>
            <Field label={t("Password")} hint={t("Leave empty to let the server ask for none — or press the dice.")}>
              <div style={{ display: "flex", gap: "var(--s2)" }}>
                <input
                  className="input mono"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoCapitalize="none"
                  spellCheck={false}
                />
                <button
                  type="button"
                  className="btn btn--ghost"
                  title={t("Generate a password")}
                  onClick={() => setPassword(suggestPassword())}
                >
                  {t("Generate")}
                </button>
              </div>
            </Field>
            <Field
              label={t("Volume limit (GB)")}
              hint={t("0 = unlimited. The account is cut off when the volume runs out.")}
            >
              <input
                className="input mono"
                type="number"
                min={0}
                value={volume}
                onChange={(e) => setVolume(e.target.value)}
                placeholder="0"
              />
            </Field>
            <Field
              label={t("Max online users")}
              hint={t("0 = unlimited. Extra sessions are dropped when the count is passed.")}
            >
              <input
                className="input mono"
                type="number"
                min={0}
                value={maxOnline}
                onChange={(e) => setMaxOnline(e.target.value)}
                placeholder="0"
              />
            </Field>
          </div>
          <Field label={t("Note")}>
            <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <label className="checkline" style={{ marginTop: "var(--s2)" }}>
            <input type="checkbox" checked={extend} onChange={(e) => setExtend(e.target.checked)} />
            <span>{t("If the username already exists, add the months to it instead of refusing.")}</span>
          </label>
          <div className="sale-sum">
            <span>{t("Expiry will be")}</span>
            <b>{formatDate(expiryFromMonths(presetMonths).toISOString())}</b>
            <span>·</span>
            <b>{price ? `${fmtNum(Number(price))} ${currency}` : t("no charge")}</b>
            <span>·</span>
            <span>
              {t("Volume")}: <b>{Number(volume) > 0 ? `${fmtNum(Number(volume))} GB` : t("unlimited")}</b>
            </span>
            <span>·</span>
            <span>
              {t("Online")}: <b>{Number(maxOnline) > 0 ? fmtNum(Number(maxOnline)) : t("unlimited")}</b>
            </span>
          </div>
          <div style={{ display: "flex", gap: "var(--s2)", marginTop: "var(--s3)" }}>
            <button className="btn btn--primary" disabled={busy || !hub} onClick={submit}>
              {busy && <span className="spin" style={{ width: 13, height: 13 }} />}
              <IconPlus size={15} /> {t("Sell and create the user")}
            </button>
          </div>
        </div>

        {/* -------- plans: price, volume, online count -------- */}
        <div className="card pad">
          <SectionTitle>{t("Price list")}</SectionTitle>
          <p className="tsub" style={{ marginTop: -4 }}>
            {t("What each plan includes: the price, the traffic volume and the concurrent online count.")}
          </p>
          <div className="pricelist">
            <div className="pricerow pricerow--head">
              <span />
              <span>{t("Price")}</span>
              <span>{t("Volume (GB)")}</span>
              <span>{t("Online")}</span>
            </div>
            {DURATION_GROUPS.map((d) => {
              const p = pricingDraft[d.name] ?? { price: 0, volume_gb: 0, max_online: 0 };
              const set = (patch: Partial<Plan>) =>
                setPricingDraft((prev) => ({ ...prev, [d.name]: { ...p, ...patch } }));
              return (
                <div key={d.name} className="pricerow">
                  <span className="pricerow__label">
                    {t(d.labelKey)}
                    <i>{d.months} {d.months === 1 ? t("month") : t("months")}</i>
                  </span>
                  <input
                    className="input mono"
                    type="number"
                    min={0}
                    title={t("Price")}
                    value={p.price ?? ""}
                    onChange={(e) => set({ price: Number(e.target.value) || 0 })}
                  />
                  <input
                    className="input mono"
                    type="number"
                    min={0}
                    title={t("Volume limit (GB)")}
                    placeholder="0"
                    value={p.volume_gb ?? ""}
                    onChange={(e) => set({ volume_gb: Number(e.target.value) || 0 })}
                  />
                  <input
                    className="input mono"
                    type="number"
                    min={0}
                    title={t("Max online users")}
                    placeholder="0"
                    value={p.max_online ?? ""}
                    onChange={(e) => set({ max_online: Number(e.target.value) || 0 })}
                  />
                </div>
              );
            })}
            <Field label={t("Currency")}>
              <input className="input" value={currency} onChange={(e) => setCurrency(e.target.value)} />
            </Field>
            <button className="btn btn--primary" disabled={savingPricing} onClick={savePricing}>
              {savingPricing && <span className="spin" style={{ width: 13, height: 13 }} />}
              {t("Save prices")}
            </button>
          </div>
        </div>
      </div>

      {/* -------- the ledger -------- */}
      <div className="card" style={{ marginTop: "var(--s4)" }}>
        <SectionTitle count={ledger?.SaleList.length}>{t("Recent sales")}</SectionTitle>
        {ledger === null ? (
          <LoadingBlock label={t("loading sales")} />
        ) : ledger.SaleList.length === 0 ? (
          <Empty title={t("No sales yet.")}>
            {t("Every subscription sold through this page lands in this ledger.")}
          </Empty>
        ) : (
          <div className="tscroll">
            <table className="dtable">
              <thead>
                <tr>
                  <th>{t("Date")}</th>
                  <th>{t("User")}</th>
                  <th>{t("Group")}</th>
                  <th>{t("Duration")}</th>
                  <th>{t("Price")}</th>
                  <th>{t("Volume")}</th>
                  <th>{t("Online")}</th>
                  <th>{t("Status")}</th>
                  <th>{t("Buyer name")}</th>
                  <th>{t("Note")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {ledger.SaleList.map((s) => {
                  const st = (ledger.Status ?? {})[statusKey(s.HubName, s.UserName)];
                  return (
                    <tr key={String(s.SaleID)}>
                      <td className="tmono">{formatDate(String(s.CreatedDate))}</td>
                      <td className="tname mono">{String(s.UserName)}</td>
                      <td><span className="chip chip--brand">{String(s.GroupName || "—")}</span></td>
                      <td className="tmono">
                        {Number(s.Months)} {Number(s.Months) === 1 ? t("month") : t("months")}
                      </td>
                      <td className="tmono">
                        {fmtNum(Number(s.Price))} {String(s.Currency || "")}
                      </td>
                      <td>{volumeCell(st)}</td>
                      <td>{onlineCell(st)}</td>
                      <td>
                        {st?.blocked ? (
                          <span className="pill pill--err">{t("Blocked")}</span>
                        ) : st?.over_volume ? (
                          <span className="pill pill--err">{t("Volume used up")}</span>
                        ) : st?.over_online ? (
                          <span className="pill pill--warn">{t("Over the online limit")}</span>
                        ) : (
                          <span className="pill pill--ok">{t("OK")}</span>
                        )}
                      </td>
                      <td>{String(s.BuyerName || "—")}</td>
                      <td className="tsub">{String(s.Note || "")}</td>
                      <td>
                        <div style={{ display: "flex", gap: "var(--s1)", alignItems: "center" }}>
                          <RenewMenu
                            hub={String(s.HubName)}
                            name={String(s.UserName)}
                            months={Number(s.Months) || null}
                          />
                          {st && (st.volume_bytes > 0 || st.used_bytes > 0) && (
                            <button
                              className="btn btn--sm btn--ghost"
                              title={t("Reset usage")}
                              aria-label={t("Reset usage")}
                              onClick={() => void resetUsage(st)}
                            >
                              <IconSync size={14} />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {ledger && ledger.SaleList.length > 0 && (
          <div className="sale-sum" style={{ padding: "0 var(--s4) var(--s4)" }}>
            <span>{t("Total charged")}</span>
            <b>{fmtNum(total)} {currency}</b>
          </div>
        )}
      </div>
    </div>
  );
}
