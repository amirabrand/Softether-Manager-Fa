"use client";

import { useCallback, useEffect, useState } from "react";
import { Empty, Field, LoadingBlock, PageHead, SectionTitle } from "../components/bits";
import { api, type Wire } from "../lib/api";
import { useT } from "../lib/i18n";
import { useToast } from "../lib/toast";
import { IconCopy, IconPlus, IconSwap } from "../ui/Icon";

type Price = {
  group: string;
  months: number;
  list_price: number;
  price: number;
  volume_gb: number;
  max_online: number;
};

type Purchase = {
  username: string;
  password: string;
  expire: string;
  price: number;
};

const fmtNum = (n: number) => new Intl.NumberFormat("fa-IR-u-ca-gregory").format(n);
const fmtDate = (iso: string) => String(iso || "").slice(0, 16).replace("T", " ");

/**
 * The reseller's desk: a prepaid wallet, a price list at the reseller's own
 * rate, and one form that turns credit into accounts -- one at a time or a
 * batch at once. Credentials land right here; the wallet statement sits
 * underneath so every debit has a line to point at.
 */
export function Reseller() {
  const t = useT();
  const { push } = useToast();

  const [ov, setOv] = useState<null | {
    Username_str: string;
    Rate_f64: number;
    Balance_f64: number;
    Currency_str: string;
    Prices: Price[];
  }>(null);
  const [group, setGroup] = useState("");
  const [count, setCount] = useState("1");
  const [prefix, setPrefix] = useState("");
  const [busy, setBusy] = useState(false);
  const [batch, setBatch] = useState<null | {
    Purchases: Purchase[];
    Total_f64: number;
    Balance_f64: number;
    Currency_str: string;
  }>(null);
  const [purchases, setPurchases] = useState<null | {
    SaleList: Wire[];
    Count_u32: number;
    Sum_f64: number;
  }>(null);
  const [txs, setTxs] = useState<null | { TxList: Wire[]; Balance_f64: number }>(null);

  // wallet top-up: card-to-card request + oxapay invoice + my payment requests
  const [pc, setPc] = useState<null | {
    CardNumber_str: string;
    CardHolder_str: string;
    CardBank_str: string;
    PayNote_utf: string;
    OxapayEnabled_b: boolean;
    OxapayCurrency_str: string;
    Currency_str: string;
  }>(null);
  const [topAmount, setTopAmount] = useState("");
  const [topRef, setTopRef] = useState("");
  const [topBusy, setTopBusy] = useState(false);
  const [oxaAmount, setOxaAmount] = useState("");
  const [oxaBusy, setOxaBusy] = useState(false);
  const [oxaLink, setOxaLink] = useState("");
  const [myPays, setMyPays] = useState<null | { PayReqList: Wire[]; Pending_u32: number }>(null);

  const loadAll = useCallback(async () => {
    const out = await api.resellerOverview().catch(() => null);
    if (out) {
      setOv(out as never);
      setGroup((g) => g || String((out as { Prices: Price[] }).Prices[0]?.group ?? ""));
    }
    void loadPurchases();
    void loadWallet();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadPurchases = useCallback(async () => {
    const out = await api.resellerPurchases().catch(() => null);
    if (out) setPurchases(out);
  }, []);

  const loadWallet = useCallback(async () => {
    const out = await api.resellerWallet().catch(() => null);
    if (out) setTxs(out);
  }, []);

  const loadPays = useCallback(async () => {
    const out = await api.resellerPayConfig().catch(() => null);
    if (out) setPc(out);
    const mine = await api.resellerPayments().catch(() => null);
    if (mine) setMyPays(mine);
  }, []);

  useEffect(() => {
    void loadAll();
    void loadPays();
  }, [loadAll, loadPays]);

  const topUp = async () => {
    if (!(Number(topAmount) > 0)) return;
    setTopBusy(true);
    try {
      await api.resellerTopupC2C({
        amount: Number(topAmount),
        ref: topRef.trim(),
      });
      push("ok", t("Top-up requested — waiting for the operator's confirmation."));
      setTopAmount("");
      setTopRef("");
      void loadPays();
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    } finally {
      setTopBusy(false);
    }
  };

  const topUpOxa = async () => {
    if (!(Number(oxaAmount) > 0)) return;
    setOxaBusy(true);
    try {
      const out = await api.resellerTopupOxa({ amount: Number(oxaAmount) });
      setOxaLink(String((out.PayReq ?? {}).PayLink ?? ""));
      push("ok", t("Invoice created."));
      setOxaAmount("");
      void loadPays();
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    } finally {
      setOxaBusy(false);
    }
  };

  const price = (g: string) => (ov?.Prices ?? []).find((p) => p.group === g) ?? null;
  const sel = price(group);
  const total = sel ? sel.price * (Number(count) || 0) : 0;
  const cur = ov?.Currency_str ?? "";

  const buy = async () => {
    if (!sel || !(Number(count) >= 1)) return;
    setBusy(true);
    setError(null);
    try {
      const out = await api.resellerPurchase({
        group,
        count: Number(count) || 1,
        prefix: prefix.trim(),
      });
      setBatch(out as never);
      push("ok", t("{n} account(s) created.", { n: fmtNum((out as { Purchases: Purchase[] }).Purchases.length) }));
      void loadAll();
    } catch (e) {
      setError(e instanceof Error ? t(e.message) : String(e));
    } finally {
      setBusy(false);
    }
  };

  const [error, setError] = useState<string | null>(null);

  const copyAll = () => {
    if (!batch) return;
    const text = batch.Purchases.map(
      (p) => `${p.username} : ${p.password}  (${p.expire})`,
    ).join("\n");
    void navigator.clipboard?.writeText(text);
    push("ok", t("Credentials copied."));
  };

  return (
    <div className="page">
      <PageHead
        title={t("Reseller desk")}
        sub={t("Buy accounts from your wallet, at your own rate, one or a batch at a time.")}
        actions={<IconSwap size={22} />}
      />

      {/* -------- wallet + purchase -------- */}
      <div className="sales-grid">
        <div className="card pad">
          <SectionTitle>{t("Buy accounts")}</SectionTitle>
          {error && <div className="alert alert--warn">{error}</div>}
          <div className="grid2">
            <Field label={t("Plan")}>
              <select className="input" value={group} onChange={(e) => setGroup(e.target.value)}>
                {(ov?.Prices ?? []).map((p) => (
                  <option key={p.group} value={p.group}>
                    {p.months} {p.months === 1 ? t("month") : t("months")} — {fmtNum(p.price)} {cur}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("How many")} hint={t("Up to 50 accounts in one batch.")}>
              <input
                className="input mono"
                type="number"
                min={1}
                max={50}
                value={count}
                onChange={(e) => setCount(e.target.value)}
              />
            </Field>
            <Field
              label={t("Username prefix (optional)")}
              hint={t("English letters, digits, dot, dash. Example: ali → ali, ali01, ali02…")}
            >
              <input
                className="input mono"
                value={prefix}
                autoCapitalize="none"
                spellCheck={false}
                onChange={(e) => setPrefix(e.target.value)}
                placeholder="ali"
              />
            </Field>
            <Field label={t("Total cost")}>
              <div className="sale-sum" style={{ marginTop: 0 }}>
                <b>
                  {fmtNum(total)} {cur}
                </b>
              </div>
            </Field>
          </div>
          <div style={{ display: "flex", gap: "var(--s2)", marginTop: "var(--s3)" }}>
            <button
              className="btn btn--primary"
              disabled={busy || !sel || !(Number(count) >= 1)}
              onClick={buy}
            >
              {busy && <span className="spin" style={{ width: 13, height: 13 }} />}
              <IconPlus size={15} /> {t("Buy and create")}
            </button>
          </div>

          {batch && (
            <div className="alert alert--ok" style={{ marginTop: "var(--s3)" }}>
              <div>
                <b>
                  {t("{n} account(s) created.", { n: fmtNum(batch.Purchases.length) })}
                </b>{" "}
                {t("Total")}: {fmtNum(batch.Total_f64)} {batch.Currency_str} · {t("wallet now")}:{" "}
                {fmtNum(batch.Balance_f64)}
              </div>
              <button className="btn btn--sm btn--ghost" onClick={copyAll}>
                <IconCopy size={14} /> {t("Copy all")}
              </button>
            </div>
          )}
          {batch && (
            <div className="tscroll" style={{ marginTop: "var(--s2)" }}>
              <table className="dtable">
                <thead>
                  <tr>
                    <th>{t("VPN username")}</th>
                    <th>{t("Password")}</th>
                    <th>{t("Expires")}</th>
                  </tr>
                </thead>
                <tbody>
                  {batch.Purchases.map((p) => (
                    <tr key={p.username}>
                      <td className="tmono tname">{p.username}</td>
                      <td>
                        <button
                          className="btn btn--sm btn--ghost mono"
                          onClick={() => {
                            void navigator.clipboard?.writeText(p.password);
                            push("ok", t("Password copied."));
                          }}
                        >
                          <IconCopy size={13} /> {p.password}
                        </button>
                      </td>
                      <td className="tmono">{p.expire}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card pad">
          <SectionTitle>{t("Your wallet")}</SectionTitle>
          <div className="wallet-balance">
            <small>{t("Balance")}</small>
            <b>
              {ov ? fmtNum(ov.Balance_f64) : "…"} <i>{cur}</i>
            </b>
          </div>
          <p className="tsub" style={{ marginTop: "var(--s2)" }}>
            {t("Your rate is {rate}% of the list price.", { rate: fmtNum(ov?.Rate_f64 ?? 100) })}
          </p>
          <div className="pricelist" style={{ marginTop: "var(--s3)" }}>
            {(ov?.Prices ?? []).map((p) => (
              <div key={p.group} className="pricerow pricerow--read">
                <span className="pricerow__label">
                  {p.months} {p.months === 1 ? t("month") : t("months")}
                  <i>
                    {t("Volume")}: {p.volume_gb > 0 ? `${fmtNum(p.volume_gb)} GB` : t("unlimited")}
                  </i>
                </span>
                <b className="tmono">
                  {fmtNum(p.price)} {cur}
                </b>
              </div>
            ))}
          </div>
          {txs && txs.TxList.length > 0 && (
            <div style={{ marginTop: "var(--s4)" }}>
              <SectionTitle>{t("Wallet statement")}</SectionTitle>
              <div className="tscroll">
                <table className="dtable">
                  <thead>
                    <tr>
                      <th>{t("Date")}</th>
                      <th>{t("Note")}</th>
                      <th>{t("Amount")}</th>
                      <th>{t("Balance")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {txs.TxList.map((tx) => (
                      <tr key={String(tx.WalletTxID)}>
                        <td className="tmono">{fmtDate(String(tx.CreatedDate))}</td>
                        <td>{String(tx.Note || "—")}</td>
                        <td className={`tmono ${Number(tx.Amount) < 0 ? "tbad" : "tok"}`}>
                          {Number(tx.Amount) > 0 ? "+" : "−"}
                          {fmtNum(Math.abs(Number(tx.Amount)))}
                        </td>
                        <td className="tmono">{fmtNum(Number(tx.BalanceAfter))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* -------- wallet top-up: card-to-card + oxapay -------- */}
      <div className="card pad" style={{ marginTop: "var(--s4)" }}>
        <SectionTitle count={myPays?.Pending_u32 || undefined}>{t("Wallet top-up")}</SectionTitle>
        {pc === null ? (
          <LoadingBlock label={t("loading")} />
        ) : (
          <div className="grid2" style={{ gap: "var(--s4)" }}>
            <div>
              <SectionTitle>{t("Top up by card-to-card")}</SectionTitle>
              <p className="tsub">
                {t("Transfer to the card below, then report the tracking reference here; the operator confirms it and your wallet fills up.")}
              </p>
              {(pc.CardNumber_str || pc.CardHolder_str || pc.PayNote_utf) && (
                <div className="alert alert--info mono" dir="ltr" style={{ textAlign: "left" }}>
                  {pc.CardNumber_str && <div><b>{pc.CardNumber_str}</b></div>}
                  {pc.CardHolder_str && <div>{pc.CardHolder_str}</div>}
                  {pc.CardBank_str && <div>{pc.CardBank_str}</div>}
                  {pc.PayNote_utf && <div style={{ marginTop: 6 }}>{pc.PayNote_utf}</div>}
                </div>
              )}
              <div className="grid2">
                <Field label={t("Amount (shop currency)")}>
                  <input className="input mono" type="number" min={1} value={topAmount}
                         onChange={(e) => setTopAmount(e.target.value)} placeholder="500000" />
                </Field>
                <Field label={t("Tracking reference")}>
                  <input className="input mono" value={topRef} onChange={(e) => setTopRef(e.target.value)} />
                </Field>
              </div>
              <div style={{ marginTop: "var(--s3)" }}>
                <button className="btn btn--primary" disabled={topBusy || !(Number(topAmount) > 0)} onClick={topUp}>
                  {topBusy && <span className="spin" style={{ width: 13, height: 13 }} />}
                  <IconPlus size={15} /> {t("Request top-up")}
                </button>
              </div>
            </div>

            {pc.OxapayEnabled_b && (
              <div>
                <SectionTitle>{t("Top up with Oxapay")}</SectionTitle>
                <p className="tsub">
                  {t("Pay the invoice; your wallet fills automatically once the network confirms.")}
                </p>
                <div className="grid2">
                  <Field label={t("Amount (shop currency)")}>
                    <input className="input mono" type="number" min={1} value={oxaAmount}
                           onChange={(e) => setOxaAmount(e.target.value)} placeholder="540000" />
                  </Field>
                  <Field label={t("Gateway currency")}>
                    <div className="sale-sum" style={{ marginTop: 0 }}>
                      <b className="tmono">{pc.OxapayCurrency_str}</b>
                    </div>
                  </Field>
                </div>
                <div style={{ display: "flex", gap: "var(--s2)", marginTop: "var(--s3)", flexWrap: "wrap" }}>
                  <button className="btn btn--primary" disabled={oxaBusy || !(Number(oxaAmount) > 0)} onClick={topUpOxa}>
                    {oxaBusy && <span className="spin" style={{ width: 13, height: 13 }} />}
                    <IconPlus size={15} /> {t("Create invoice")}
                  </button>
                  {oxaLink && (
                    <a className="btn btn--ghost" href={oxaLink} target="_blank" rel="noreferrer">
                      {t("Open payment link")}
                    </a>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
        {myPays && myPays.PayReqList.length > 0 && (
          <div style={{ marginTop: "var(--s4)" }}>
            <SectionTitle>{t("Your payment requests")}</SectionTitle>
            <div className="tscroll">
              <table className="dtable">
                <thead>
                  <tr>
                    <th>{t("Date")}</th>
                    <th>{t("Method")}</th>
                    <th>{t("Amount")}</th>
                    <th>{t("Status")}</th>
                  </tr>
                </thead>
                <tbody>
                  {myPays.PayReqList.map((p) => (
                    <tr key={String(p.PayReqID)}>
                      <td className="tmono">{fmtDate(String(p.CreatedDate))}</td>
                      <td>{p.Kind === "oxapay" ? `🪙 ${t("oxapay")}` : `💳 ${t("card-to-card")}`}</td>
                      <td className="tmono">
                        {fmtNum(Number(p.Amount))} {String(p.Currency || "")}
                      </td>
                      <td>
                        {p.Status === "pending" ? (
                          <span className="pill pill--warn">{t("Pending")}</span>
                        ) : p.Status === "approved" ? (
                          <span className="pill pill--ok">{t("Approved")}</span>
                        ) : (
                          <span className="pill pill--err">{t("Rejected")}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* -------- purchase history -------- */}
      <div className="card" style={{ marginTop: "var(--s4)" }}>
        <SectionTitle count={purchases?.Count_u32}>{t("Your purchases")}</SectionTitle>
        {purchases === null ? (
          <LoadingBlock label={t("loading")} />
        ) : purchases.SaleList.length === 0 ? (
          <Empty title={t("Nothing bought yet.")}>
            {t("Every account you buy lands here with its expiry and price.")}
          </Empty>
        ) : (
          <div className="tscroll">
            <table className="dtable">
              <thead>
                <tr>
                  <th>{t("Date")}</th>
                  <th>{t("VPN username")}</th>
                  <th>{t("Group")}</th>
                  <th>{t("Duration")}</th>
                  <th>{t("Price")}</th>
                  <th>{t("Volume")}</th>
                </tr>
              </thead>
              <tbody>
                {purchases.SaleList.map((s) => (
                  <tr key={String(s.SaleID)}>
                    <td className="tmono">{fmtDate(String(s.CreatedDate))}</td>
                    <td className="tname mono">{String(s.UserName)}</td>
                    <td>
                      <span className="chip">{String(s.GroupName || "—")}</span>
                    </td>
                    <td className="tmono">
                      {fmtNum(Number(s.Months))} {Number(s.Months) === 1 ? t("month") : t("months")}
                    </td>
                    <td className="tmono">
                      {fmtNum(Number(s.Price))} {String(s.Currency || "")}
                    </td>
                    <td className="tmono">
                      {Number(s.VolumeBytes) > 0
                        ? `${fmtNum(Math.round(Number(s.VolumeBytes) / 1024 ** 3))} GB`
                        : t("unlimited")}
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
