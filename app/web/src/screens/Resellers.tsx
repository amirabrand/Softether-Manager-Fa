"use client";

import { useCallback, useEffect, useState } from "react";
import { Empty, Field, LoadingBlock, PageHead, SectionTitle } from "../components/bits";
import { api } from "../lib/api";
import { useT } from "../lib/i18n";
import { useToast } from "../lib/toast";
import { IconPlus, IconUsers } from "../ui/Icon";

type ResellerRow = {
  UserID: number;
  Username: string;
  Rate: number;
  CreatedDate: string;
  IsDeleted: number;
  Balance_f64: number;
  Purchases_u32: number;
};

const fmtNum = (n: number) => new Intl.NumberFormat("fa-IR-u-ca-gregory").format(n);
const fmtDate = (iso: string) => String(iso || "").slice(0, 10);

/**
 * The operator's reseller book: create reseller sign-ins, set each one's
 * rate (the percent of the list price they pay), and run their wallets --
 * top-ups and clawbacks both land in the reseller's statement.
 */
export function Resellers() {
  const t = useT();
  const { push } = useToast();

  const [rows, setRows] = useState<ResellerRow[] | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [rate, setRate] = useState("80");
  const [busy, setBusy] = useState(false);
  const [amounts, setAmounts] = useState<Record<number, string>>({});
  const [rates, setRates] = useState<Record<number, string>>({});

  const load = useCallback(async () => {
    const out = await api.resellersList().catch(() => null);
    if (out) setRows(out.ResellerList as ResellerRow[]);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    setBusy(true);
    try {
      await api.resellerCreate({
        username: username.trim(),
        password,
        rate: Number(rate) || 100,
      });
      push("ok", t("Reseller created."));
      setUsername("");
      setPassword("");
      setRate("80");
      void load();
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    } finally {
      setBusy(false);
    }
  };

  const topup = async (r: ResellerRow, sign: 1 | -1) => {
    const amount = Number(amounts[r.UserID] || 0);
    if (!amount) return;
    try {
      await api.resellerTopup(r.UserID, {
        amount: sign * Math.abs(amount),
        note: sign > 0 ? "شارژ توسط ادمین" : "کسر توسط ادمین",
      });
      push("ok", t("Wallet updated."));
      setAmounts((prev) => ({ ...prev, [r.UserID]: "" }));
      void load();
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    }
  };

  const saveRate = async (r: ResellerRow) => {
    const value = Number(rates[r.UserID]);
    if (!(value >= 1)) return;
    try {
      await api.resellerUpdate(r.UserID, { rate: value });
      push("ok", t("Rate saved."));
      void load();
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    }
  };

  const toggle = async (r: ResellerRow) => {
    try {
      await api.resellerUpdate(r.UserID, { is_deleted: !r.IsDeleted });
      void load();
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    }
  };

  return (
    <div className="page">
      <PageHead
        title={t("Resellers")}
        sub={t("Reseller sign-ins buy accounts from a prepaid wallet at their own rate.")}
        actions={<IconUsers size={22} />}
      />

      <div className="card pad" style={{ marginBottom: "var(--s4)" }}>
        <SectionTitle>{t("New reseller")}</SectionTitle>
        <div className="coupon-new">
          <input
            className="input mono"
            value={username}
            autoCapitalize="none"
            spellCheck={false}
            placeholder={t("Username")}
            onChange={(e) => setUsername(e.target.value)}
          />
          <input
            className="input mono"
            value={password}
            autoCapitalize="none"
            spellCheck={false}
            placeholder={t("Password")}
            onChange={(e) => setPassword(e.target.value)}
          />
          <input
            className="input mono"
            type="number"
            min={1}
            max={100}
            value={rate}
            onChange={(e) => setRate(e.target.value)}
            title={t("Rate — percent of the list price this reseller pays.")}
          />
          <button
            className="btn btn--primary"
            disabled={busy || username.trim().length < 3 || password.length < 4}
            onClick={create}
          >
            {busy && <span className="spin" style={{ width: 13, height: 13 }} />}
            <IconPlus size={15} /> {t("Add reseller")}
          </button>
        </div>
        <p className="tsub">{t("The rate applies to every plan: 80 means this reseller pays 80% of the list price.")}</p>
      </div>

      <div className="card pad">
        <SectionTitle count={rows?.length}>{t("Reseller accounts")}</SectionTitle>
        {rows === null ? (
          <LoadingBlock label={t("loading")} />
        ) : rows.length === 0 ? (
          <Empty title={t("No resellers yet.")}>
            {t("Create one, top up its wallet, and hand over the sign-in — it buys and delivers accounts itself.")}
          </Empty>
        ) : (
          <div className="tscroll">
            <table className="dtable">
              <thead>
                <tr>
                  <th>{t("Username")}</th>
                  <th>{t("Balance")}</th>
                  <th>{t("Rate")}</th>
                  <th>{t("Purchases")}</th>
                  <th>{t("Created")}</th>
                  <th>{t("Wallet")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.UserID} style={{ opacity: r.IsDeleted ? 0.5 : 1 }}>
                    <td className="tname mono">
                      {r.Username}
                      {r.IsDeleted ? <span className="pill pill--err">{t("Disabled")}</span> : null}
                    </td>
                    <td className="tmono">{fmtNum(r.Balance_f64)}</td>
                    <td>
                      <div style={{ display: "flex", gap: "var(--s1)", alignItems: "center" }}>
                        <input
                          className="input mono"
                          type="number"
                          min={1}
                          max={100}
                          style={{ width: 72 }}
                          value={rates[r.UserID] ?? String(r.Rate)}
                          onChange={(e) => setRates((p) => ({ ...p, [r.UserID]: e.target.value }))}
                        />
                        <button
                          className="btn btn--sm btn--ghost"
                          title={t("Save rate")}
                          onClick={() => void saveRate(r)}
                        >
                          {t("Save rate")}
                        </button>
                      </div>
                    </td>
                    <td className="tmono">{fmtNum(r.Purchases_u32)}</td>
                    <td className="tmono">{fmtDate(r.CreatedDate)}</td>
                    <td>
                      <div style={{ display: "flex", gap: "var(--s1)", alignItems: "center" }}>
                        <input
                          className="input mono"
                          type="number"
                          min={0}
                          style={{ width: 110 }}
                          placeholder={t("Amount")}
                          value={amounts[r.UserID] ?? ""}
                          onChange={(e) => setAmounts((p) => ({ ...p, [r.UserID]: e.target.value }))}
                        />
                        <button
                          className="btn btn--sm btn--primary"
                          title={t("Top up")}
                          onClick={() => void topup(r, 1)}
                        >
                          +
                        </button>
                        <button
                          className="btn btn--sm btn--ghost"
                          title={t("Deduct")}
                          onClick={() => void topup(r, -1)}
                        >
                          −
                        </button>
                      </div>
                    </td>
                    <td>
                      <button className="btn btn--sm btn--ghost" onClick={() => void toggle(r)}>
                        {r.IsDeleted ? t("Enable") : t("Disable")}
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
