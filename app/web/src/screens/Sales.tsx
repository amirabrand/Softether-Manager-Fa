"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Empty, Field, LoadingBlock, PageHead, SectionTitle } from "../components/bits";
import { api, type Wire } from "../lib/api";
import { DURATION_GROUPS, durationOfGroup, expiryFromMonths } from "../lib/duration";
import { useT } from "../lib/i18n";
import { useToast } from "../lib/toast";
import { useServer } from "../lib/server";
import { formatDate } from "../lib/util";
import { IconCopy, IconPlus, IconTag } from "../ui/Icon";

/**
 * The operator's till.
 *
 * One page answers the whole habit of selling time: the price list lives at
 * the side, a sale is one small form, and the ledger underneath remembers
 * every subscription the panel has sold -- who bought it, which duration,
 * how much was charged. Selling creates (or extends) the VPN user with the
 * group's expiry, so the sale and the server never disagree.
 */
export function Sales() {
  const t = useT();
  const { push } = useToast();
  const { hubs } = useServer();

  // ---- pricing ------------------------------------------------------------
  const [pricing, setPricing] = useState<Record<string, number>>({});
  const [currency, setCurrency] = useState("");
  const [pricingDraft, setPricingDraft] = useState<Record<string, number>>({});
  const [savingPricing, setSavingPricing] = useState(false);

  // ---- the sale form --------------------------------------------------------
  const [hub, setHub] = useState("");
  const [group, setGroup] = useState("1month");
  const [buyer, setBuyer] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [price, setPrice] = useState("");
  const [note, setNote] = useState("");
  const [extend, setExtend] = useState(false);
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<null | { name: string; password: string; expire: string }>(null);

  // ---- ledger ---------------------------------------------------------------
  const [ledger, setLedger] = useState<{ SaleList: Wire[]; PriceSum_f64: number } | null>(null);

  const loadLedger = useCallback(async () => {
    const out = await api.sales(200).catch(() => null);
    if (out) setLedger(out as { SaleList: Wire[]; PriceSum_f64: number });
  }, []);

  useEffect(() => {
    void (async () => {
      const s = await api.settings().catch(() => null);
      if (s) {
        const p: Record<string, number> = {};
        for (const [k, v] of Object.entries(s.sale_pricing ?? {})) p[k] = Number(v) || 0;
        setPricing(p);
        setPricingDraft(p);
        setCurrency(String(s.sale_currency ?? ""));
      }
    })();
    void loadLedger();
  }, [loadLedger]);

  useEffect(() => {
    if (!hub && hubs && hubs.length > 0) setHub(String(hubs[0].HubName_str));
  }, [hubs, hub]);

  const preset = useMemo(() => durationOfGroup(group), [group]);
  const presetMonths = preset?.months ?? 1;
  const fmtPrice = useCallback(
    (n: number) => new Intl.NumberFormat("fa-IR-u-ca-gregory").format(n),
    [],
  );

  const pickGroup = (g: string) => {
    setGroup(g);
    const p = durationOfGroup(g);
    if (p && pricingDraft[g] != null) setPrice(String(pricingDraft[g]));
  };

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
        buyer,
        note,
        extend_if_exists: extend,
      });
      setReceipt({
        name,
        password,
        expire: out.user.ExpireTime_dt,
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

  const total = ledger ? ledger.PriceSum_f64 : 0;

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
                {DURATION_GROUPS.map((d) => (
                  <option key={d.name} value={d.name}>
                    {t(d.labelKey)} — {pricingDraft[d.name] != null ? `${fmtPrice(pricingDraft[d.name])} ${currency}` : t("no price set")}
                  </option>
                ))}
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
                placeholder={pricing[group] != null ? String(pricing[group]) : "0"}
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
            <b>{price ? `${fmtPrice(Number(price))} ${currency}` : t("no charge")}</b>
          </div>
          <div style={{ display: "flex", gap: "var(--s2)", marginTop: "var(--s3)" }}>
            <button className="btn btn--primary" disabled={busy || !hub} onClick={submit}>
              {busy && <span className="spin" style={{ width: 13, height: 13 }} />}
              <IconPlus size={15} /> {t("Sell and create the user")}
            </button>
          </div>
        </div>

        {/* -------- pricing -------- */}
        <div className="card pad">
          <SectionTitle>{t("Price list")}</SectionTitle>
          <div className="pricelist">
            {DURATION_GROUPS.map((d) => (
              <label key={d.name} className="pricerow">
                <span className="pricerow__label">
                  {t(d.labelKey)}
                  <i>{d.months} {d.months === 1 ? t("month") : t("months")}</i>
                </span>
                <input
                  className="input mono"
                  type="number"
                  min={0}
                  value={pricingDraft[d.name] ?? ""}
                  onChange={(e) =>
                    setPricingDraft((p) => ({ ...p, [d.name]: Number(e.target.value) || 0 }))
                  }
                />
              </label>
            ))}
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
                  <th>{t("Buyer name")}</th>
                  <th>{t("Note")}</th>
                </tr>
              </thead>
              <tbody>
                {ledger.SaleList.map((s) => (
                  <tr key={String(s.SaleID)}>
                    <td className="tmono">{formatDate(String(s.CreatedDate))}</td>
                    <td className="tname mono">{String(s.UserName)}</td>
                    <td><span className="chip chip--brand">{String(s.GroupName || "—")}</span></td>
                    <td className="tmono">
                      {Number(s.Months)} {Number(s.Months) === 1 ? t("month") : t("months")}
                    </td>
                    <td className="tmono">
                      {fmtPrice(Number(s.Price))} {String(s.Currency || "")}
                    </td>
                    <td>{String(s.BuyerName || "—")}</td>
                    <td className="tsub">{String(s.Note || "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {ledger && ledger.SaleList.length > 0 && (
          <div className="sale-sum" style={{ padding: "0 var(--s4) var(--s4)" }}>
            <span>{t("Total charged")}</span>
            <b>{fmtPrice(total)} {currency}</b>
          </div>
        )}
      </div>
    </div>
  );
}
