"use client";

import { useCallback, useEffect, useState } from "react";
import { Empty, Field, LoadingBlock, PageHead, SectionTitle } from "../components/bits";
import { api, type Wire } from "../lib/api";
import { useT } from "../lib/i18n";
import { useToast } from "../lib/toast";
import { IconCard, IconPlus } from "../ui/Icon";

/** One payment: a card-to-card receipt someone reported, or an Oxapay invoice. */
type PayReq = {
  PayReqID: number;
  Kind: string;
  Amount: number;
  Currency: string;
  Status: string;
  Buyer: string;
  ChatID: string;
  UserID: number;
  ResellerName_utf: string;
  OrderID: number;
  Ref: string;
  TrackID: string;
  PayLink: string;
  GwAmount: number;
  Asset: string;
  Note: string;
  CreatedDate: string;
  DecidedDate: string;
  DecidedBy: string;
};

type PayConfig = {
  CardNumber_str: string;
  CardHolder_str: string;
  CardBank_str: string;
  OxapayEnabled_b: boolean;
  OxapayApiKey_mask: string;
  OxapayCurrency_str: string;
  OxapayRate_f64: number;
  PublicBaseUrl_str: string;
};

const fmtDate = (iso: string) => String(iso || "").slice(0, 16).replace("T", " ");
const fmtNum = (n: number) => new Intl.NumberFormat("fa-IR-u-ca-gregory").format(n);

/**
 * The payments room: card-to-card receipts awaiting the operator's nod and
 * Oxapay invoices waiting for the chain. Approving a payment does its whole
 * job -- a linked Telegram order turns into a delivered account, a linked
 * reseller wallet fills up -- so this page is where money becomes product.
 * The configuration card underneath holds the card facts buyers transfer
 * against and the gateway's key, currency and rate.
 */
export function Payments() {
  const t = useT();
  const { push } = useToast();

  const [rows, setRows] = useState<PayReq[] | null>(null);
  const [pending, setPending] = useState(0);
  const [filter, setFilter] = useState("");

  // record-a-receipt form
  const [c2cAmount, setC2cAmount] = useState("");
  const [c2cBuyer, setC2cBuyer] = useState("");
  const [c2cRef, setC2cRef] = useState("");
  const [c2cNote, setC2cNote] = useState("");

  // oxapay invoice form
  const [oxaAmount, setOxaAmount] = useState("");
  const [oxaBuyer, setOxaBuyer] = useState("");
  const [lastLink, setLastLink] = useState("");

  const [cfg, setCfg] = useState<PayConfig | null>(null);
  const [cardNumber, setCardNumber] = useState("");
  const [cardHolder, setCardHolder] = useState("");
  const [cardBank, setCardBank] = useState("");
  const [oxaEnabled, setOxaEnabled] = useState(false);
  const [oxaKey, setOxaKey] = useState("");
  const [oxaCurrency, setOxaCurrency] = useState("USDT");
  const [oxaRate, setOxaRate] = useState("");
  const [publicUrl, setPublicUrl] = useState("");
  const [savingCfg, setSavingCfg] = useState(false);

  const loadRows = useCallback(async () => {
    const out = await api.paymentsList(filter).catch(() => null);
    if (!out) return;
    setRows((out.PayReqList ?? []) as PayReq[]);
    setPending(Number(out.Pending_u32) || 0);
  }, [filter]);

  const loadConfig = useCallback(async () => {
    const out = await api.paymentsConfig().catch(() => null);
    if (!out) return;
    setCfg(out);
    setCardNumber(String(out.CardNumber_str ?? ""));
    setCardHolder(String(out.CardHolder_str ?? ""));
    setCardBank(String(out.CardBank_str ?? ""));
    setOxaEnabled(Boolean(out.OxapayEnabled_b));
    setOxaCurrency(String(out.OxapayCurrency_str ?? "USDT"));
    setOxaRate(String(out.OxapayRate_f64 ?? 0));
    setPublicUrl(String(out.PublicBaseUrl_str ?? ""));
  }, []);

  useEffect(() => {
    void loadRows();
    void loadConfig();
  }, [loadRows, loadConfig]);

  const recordC2C = async () => {
    if (!(Number(c2cAmount) > 0)) return;
    try {
      await api.paymentRecordC2C({
        amount: Number(c2cAmount),
        buyer: c2cBuyer.trim(),
        ref: c2cRef.trim(),
        note: c2cNote.trim(),
      });
      push("ok", t("Receipt recorded — waiting for confirmation."));
      setC2cAmount("");
      setC2cBuyer("");
      setC2cRef("");
      setC2cNote("");
      void loadRows();
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    }
  };

  const createOxa = async () => {
    if (!(Number(oxaAmount) > 0)) return;
    try {
      const out = await api.paymentCreateOxa({
        amount: Number(oxaAmount),
        buyer: oxaBuyer.trim(),
      });
      const link = String((out.PayReq ?? {}).PayLink ?? "");
      setLastLink(link);
      push("ok", t("Invoice created."));
      setOxaAmount("");
      setOxaBuyer("");
      void loadRows();
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    }
  };

  const decide = async (p: PayReq, ok: boolean) => {
    try {
      if (ok) await api.paymentApprove(p.PayReqID);
      else await api.paymentReject(p.PayReqID);
      push("ok", ok ? t("Payment approved.") : t("Payment rejected."));
      void loadRows();
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    }
  };

  const check = async (p: PayReq) => {
    try {
      const out = await api.paymentCheck(p.PayReqID);
      push(
        out.Decided_b ? "ok" : "info",
        out.Decided_b ? t("Payment approved.") : t("Gateway says: {s}", { s: out.GatewayStatus_utf }),
      );
      void loadRows();
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    }
  };

  const saveCfg = async () => {
    setSavingCfg(true);
    try {
      await api.paymentsSaveConfig({
        card_number: cardNumber.trim(),
        card_holder: cardHolder.trim(),
        card_bank: cardBank.trim(),
        oxapay_enabled: oxaEnabled,
        ...(oxaKey.trim() ? { oxapay_api_key: oxaKey.trim() } : {}),
        oxapay_currency: oxaCurrency.trim() || "USDT",
        oxapay_rate: Number(oxaRate) || 0,
        public_base_url: publicUrl.trim(),
      });
      setOxaKey("");
      push("ok", t("Payment settings saved."));
      void loadConfig();
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    } finally {
      setSavingCfg(false);
    }
  };

  const kindChip = (p: PayReq) =>
    p.Kind === "oxapay" ? (
      <span className="chip">🪙 {t("oxapay")}</span>
    ) : (
      <span className="chip">💳 {t("card-to-card")}</span>
    );

  const linkCell = (p: PayReq) => {
    if (p.OrderID > 0) return `${t("linked order")} #${fmtNum(p.OrderID)}`;
    if (p.UserID > 0)
      return `${t("linked wallet")}: ${p.ResellerName_utf || "#" + p.UserID}`;
    return "—";
  };

  return (
    <div className="page">
      <PageHead
        title={t("Payments")}
        sub={t("Card-to-card receipts customers report and Oxapay invoices land here; approving one completes its job.")}
        actions={<IconCard size={22} />}
      />

      {/* -------- the payment book -------- */}
      <div className="card pad" style={{ marginBottom: "var(--s4)" }}>
        <SectionTitle count={rows?.length}>
          {t("Payment requests")}
          {pending > 0 && (
            <span className="chip chip--brand" style={{ marginInlineStart: "var(--s2)" }}>
              {t("{n} pending", { n: fmtNum(pending) })}
            </span>
          )}
        </SectionTitle>
        <div style={{ display: "flex", gap: "var(--s2)", flexWrap: "wrap", marginBottom: "var(--s3)" }}>
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
        {rows === null ? (
          <LoadingBlock label={t("loading")} />
        ) : rows.length === 0 ? (
          <Empty title={t("No payments yet.")}>
            {t("Card-to-card receipts land here until you confirm them; Oxapay invoices settle on their own webhook or your status check.")}
          </Empty>
        ) : (
          <div className="tscroll">
            <table className="dtable">
              <thead>
                <tr>
                  <th>#</th>
                  <th>{t("Method")}</th>
                  <th>{t("Amount")}</th>
                  <th>{t("Buyer")}</th>
                  <th>{t("Tracking reference")}</th>
                  <th>{t("Linked to")}</th>
                  <th>{t("Date")}</th>
                  <th>{t("Status")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.PayReqID} style={{ opacity: p.Status === "pending" ? 1 : 0.65 }}>
                    <td className="tmono">{fmtNum(p.PayReqID)}</td>
                    <td>{kindChip(p)}</td>
                    <td className="tmono">
                      {fmtNum(p.Amount)} {p.Currency}
                      {p.Kind === "oxapay" && p.GwAmount > 0 && (
                        <small className="tsub"> = {fmtNum(p.GwAmount)} {p.Asset}</small>
                      )}
                    </td>
                    <td>
                      {p.Buyer || "—"}
                      {p.ChatID ? <small className="tsub"> · {p.ChatID}</small> : null}
                    </td>
                    <td className="tmono">
                      {p.Ref || p.TrackID || "—"}
                      {p.PayLink && (
                        <>
                          {" "}
                          <a className="chip" href={p.PayLink} target="_blank" rel="noreferrer">
                            {t("Open payment link")}
                          </a>
                        </>
                      )}
                    </td>
                    <td>{linkCell(p)}</td>
                    <td className="tmono">{fmtDate(p.CreatedDate)}</td>
                    <td>
                      {p.Status === "pending" ? (
                        <span className="pill pill--warn">{t("Pending")}</span>
                      ) : p.Status === "approved" ? (
                        <span className="pill pill--ok">{t("Approved")}</span>
                      ) : (
                        <span className="pill pill--err">{t("Rejected")}</span>
                      )}
                    </td>
                    <td>
                      {p.Status === "pending" && (
                        <div style={{ display: "flex", gap: "var(--s1)", flexWrap: "wrap" }}>
                          <button className="btn btn--sm btn--primary" onClick={() => void decide(p, true)}>
                            {t("Approve")}
                          </button>
                          <button className="btn btn--sm btn--ghost" onClick={() => void decide(p, false)}>
                            {t("Reject")}
                          </button>
                          {p.Kind === "oxapay" && (
                            <button className="btn btn--sm btn--ghost" onClick={() => void check(p)}>
                              {t("Check gateway status")}
                            </button>
                          )}
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

      {/* -------- record a receipt / create an invoice -------- */}
      <div className="grid2" style={{ marginBottom: "var(--s4)", gap: "var(--s3)" }}>
        <div className="card pad">
          <SectionTitle>{t("Record card-to-card receipt")}</SectionTitle>
          <p className="tsub">{t("Transcribe a buyer's transfer here; it stays pending until you approve it above.")}</p>
          <div className="grid2">
            <Field label={t("Amount (shop currency)")}>
              <input className="input mono" type="number" min={1} value={c2cAmount}
                     onChange={(e) => setC2cAmount(e.target.value)} placeholder="400000" />
            </Field>
            <Field label={t("Buyer name")}>
              <input className="input" value={c2cBuyer} onChange={(e) => setC2cBuyer(e.target.value)} />
            </Field>
            <Field label={t("Tracking reference")}>
              <input className="input mono" value={c2cRef} onChange={(e) => setC2cRef(e.target.value)} />
            </Field>
            <Field label={t("Note")}>
              <input className="input" value={c2cNote} onChange={(e) => setC2cNote(e.target.value)} />
            </Field>
          </div>
          <div style={{ marginTop: "var(--s3)" }}>
            <button className="btn btn--primary" disabled={!(Number(c2cAmount) > 0)} onClick={recordC2C}>
              <IconPlus size={15} /> {t("Record receipt")}
            </button>
          </div>
        </div>

        <div className="card pad">
          <SectionTitle>{t("Create Oxapay invoice")}</SectionTitle>
          <p className="tsub">
            {t("The amount converts through the rate below; the gateway collects it in {c}.", { c: cfg?.OxapayCurrency_str || "USDT" })}
          </p>
          <div className="grid2">
            <Field label={t("Amount (shop currency)")}>
              <input className="input mono" type="number" min={1} value={oxaAmount}
                     onChange={(e) => setOxaAmount(e.target.value)} placeholder="540000" />
            </Field>
            <Field label={t("Buyer name")}>
              <input className="input" value={oxaBuyer} onChange={(e) => setOxaBuyer(e.target.value)} />
            </Field>
          </div>
          <div style={{ display: "flex", gap: "var(--s2)", marginTop: "var(--s3)", flexWrap: "wrap" }}>
            <button className="btn btn--primary" disabled={!(Number(oxaAmount) > 0)} onClick={createOxa}>
              <IconPlus size={15} /> {t("Create invoice")}
            </button>
            {lastLink && (
              <a className="btn btn--ghost" href={lastLink} target="_blank" rel="noreferrer">
                {t("Open payment link")}
              </a>
            )}
          </div>
        </div>
      </div>

      {/* -------- configuration -------- */}
      <div className="card pad">
        <SectionTitle>{t("Payment settings")}</SectionTitle>
        {cfg === null ? (
          <LoadingBlock label={t("loading")} />
        ) : (
          <>
            <p className="tsub">{t("Shown to buyers in the bot and on the reseller desk.")}</p>
            <div className="grid2">
              <Field label={t("Card number")}>
                <input className="input mono" value={cardNumber} onChange={(e) => setCardNumber(e.target.value)}
                       placeholder="6037-9975-XXXX-XXXX" autoCapitalize="none" spellCheck={false} />
              </Field>
              <Field label={t("Card holder")}>
                <input className="input" value={cardHolder} onChange={(e) => setCardHolder(e.target.value)} />
              </Field>
              <Field label={t("Bank")}>
                <input className="input" value={cardBank} onChange={(e) => setCardBank(e.target.value)} />
              </Field>
              <Field label={t("Enable Oxapay")} hint={t("Merchants accept crypto with Oxapay; get the API key at oxapay.com.")}>
                <label className="checkline">
                  <input type="checkbox" checked={oxaEnabled} onChange={(e) => setOxaEnabled(e.target.checked)} />
                  <span>{t("Crypto invoices through oxapay.com.")}</span>
                </label>
              </Field>
              <Field label={t("Oxapay API key")} hint={t("Leave empty to keep the saved one.")}>
                <input className="input mono" value={oxaKey} onChange={(e) => setOxaKey(e.target.value)}
                       placeholder={cfg.OxapayApiKey_mask || "merchant key"} autoCapitalize="none" spellCheck={false} />
              </Field>
              <Field label={t("Gateway currency")} hint={t("USDT, BTC, TRX…")}>
                <input className="input mono" value={oxaCurrency} onChange={(e) => setOxaCurrency(e.target.value)} />
              </Field>
              <Field label={t("Rate — shop currency per 1 gateway unit")}
                     hint={t("Example: 54000 means every USDT counts as 54,000. Amounts convert automatically.")}>
                <input className="input mono" type="number" min={0} value={oxaRate}
                       onChange={(e) => setOxaRate(e.target.value)} />
              </Field>
              <Field label={t("Public site URL")} hint={t("Used to build the gateway's callback, e.g. https://shop.example.com")}>
                <input className="input mono" value={publicUrl} onChange={(e) => setPublicUrl(e.target.value)}
                       placeholder="https://shop.example.com" autoCapitalize="none" spellCheck={false} />
              </Field>
            </div>
            <div style={{ marginTop: "var(--s3)" }}>
              <button className="btn btn--primary" disabled={savingCfg} onClick={saveCfg}>
                {savingCfg && <span className="spin" style={{ width: 13, height: 13 }} />}
                <IconPlus size={15} /> {t("Save payment settings")}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
