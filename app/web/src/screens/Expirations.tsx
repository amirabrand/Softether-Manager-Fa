"use client";

import { useCallback, useEffect, useState } from "react";
import { Empty, LoadingBlock, PageHead, usePoll } from "../components/bits";
import { RenewMenu } from "../components/RenewMenu";
import { api } from "../lib/api";
import { useT } from "../lib/i18n";
import { formatDate } from "../lib/util";
import { IconClock } from "../ui/Icon";

type Row = {
  hub: string;
  name: string;
  realname: string;
  group: string;
  expire: string;
  days_left: number;
  status: string;
};

type Data = { Expirations: Row[]; Count_u32: number; Expired_u32: number; Critical_u32: number };

/**
 * The expiry calendar: every dated subscription that has ended or ends
 * within the chosen window, soonest first. Renewing from here is one press
 * -- the row's RenewMenu does the arithmetic and the server call.
 */
export function Expirations() {
  const t = useT();
  const [data, setData] = useState<Data | null>(null);
  const [windowDays, setWindowDays] = useState(30);
  const [filter, setFilter] = useState<"all" | "expired" | "active">("all");

  const load = useCallback(async () => {
    setData(await api.expirations(windowDays).catch(() => null));
  }, [windowDays]);
  usePoll(load, "list", []);

  useEffect(() => {
    void load();
  }, [load]);

  const windows = [
    { d: 7, label: t("7 days") },
    { d: 14, label: t("14 days") },
    { d: 30, label: t("30 days") },
    { d: 90, label: t("90 days") },
  ];

  const rows = (data?.Expirations ?? []).filter((r) =>
    filter === "expired" ? r.status === "expired" : filter === "active" ? r.status !== "expired" : true,
  );

  const badge = (r: Row) => {
    if (r.status === "expired")
      return <span className="pill pill--bad">{t("expired {days} days ago", { days: Math.abs(Math.floor(r.days_left)) })}</span>;
    if (r.status === "critical") return <span className="pill pill--warn">{t("{days} days left", { days: Math.max(1, Math.ceil(r.days_left)) })}</span>;
    if (r.status === "soon") return <span className="pill">{t("{days} days left", { days: Math.ceil(r.days_left) })}</span>;
    return <span className="pill pill--ok">{t("{days} days left", { days: Math.ceil(r.days_left) })}</span>;
  };

  return (
    <div className="page">
      <PageHead
        title={t("Upcoming expirations")}
        sub={
          data
            ? t("{count} subscriptions in the window — {expired} expired, {critical} within a week.",
                { count: data.Count_u32, expired: data.Expired_u32, critical: data.Critical_u32 })
            : t("Who runs out of time soonest.")
        }
        actions={<IconClock size={22} />}
      />

      <div className="chiprow" role="group" aria-label={t("Window")}>
        {windows.map((w) => (
          <button
            key={w.d}
            className={`chip${windowDays === w.d ? " chip--on" : ""}`}
            onClick={() => setWindowDays(w.d)}
          >
            {w.label}
          </button>
        ))}
        <span style={{ width: "var(--s2)" }} />
        <button
          className={`chip${filter === "all" ? " chip--on" : ""}`}
          onClick={() => setFilter("all")}
        >
          {t("all")}
        </button>
        <button
          className={`chip${filter === "expired" ? " chip--on" : ""}`}
          onClick={() => setFilter("expired")}
        >
          {t("expired only")}
        </button>
        <button
          className={`chip${filter === "active" ? " chip--on" : ""}`}
          onClick={() => setFilter("active")}
        >
          {t("active only")}
        </button>
      </div>

      {data === null ? (
        <LoadingBlock label={t("loading expirations")} />
      ) : rows.length === 0 ? (
        <Empty title={t("Nothing expires in this window.")}>
          {t("Widen the window above, or enjoy the quiet — whichever fits.")}
        </Empty>
      ) : (
        <>
          {/* desktop table */}
          <div className="only-desktop-b">
            <div className="card tcard">
              <div className="tscroll">
                <table className="dtable">
                  <thead>
                    <tr>
                      <th>{t("User")}</th>
                      <th>{t("Hub")}</th>
                      <th>{t("Group")}</th>
                      <th>{t("Expires")}</th>
                      <th>{t("Status")}</th>
                      <th className="tact" style={{ width: 140 }}>{t("Renew")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={`${r.hub}/${r.name}`}>
                        <td>
                          <span className="tname mono">{r.name}</span>
                          {r.realname ? <span className="tsub">{r.realname}</span> : null}
                        </td>
                        <td><span className="chip chip--brand">{r.hub}</span></td>
                        <td className="tmono">{r.group || "—"}</td>
                        <td className="tmono">{formatDate(r.expire)}</td>
                        <td>{badge(r)}</td>
                        <td className="tact">
                          <RenewMenu hub={r.hub} name={r.name} labelled />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* mobile cards */}
          <div className="rows only-mobile-b">
            {rows.map((r) => (
              <div key={`${r.hub}/${r.name}`} className="row row--static">
                <div className="row__main">
                  <div className="row__name">
                    <span className="mono">{r.name}</span>
                    {badge(r)}
                  </div>
                  <div className="spec">
                    <span className="chip chip--brand">{r.hub}</span>
                    {r.group && <span className="chip"><i>{t("group")}</i>{r.group}</span>}
                    <span className="chip"><i>{t("expires")}</i>{formatDate(r.expire)}</span>
                  </div>
                </div>
                <RenewMenu hub={r.hub} name={r.name} />
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
