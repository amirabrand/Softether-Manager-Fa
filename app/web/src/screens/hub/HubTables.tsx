"use client";

import { useCallback, useState } from "react";
import { Empty, LoadingBlock, SectionTitle, usePoll } from "../../components/bits";
import { api, type Wire } from "../../lib/api";
import { useToast } from "../../lib/toast";
import { useT } from "../../lib/i18n";
import { timeAgo } from "../../lib/util";
import { IconTrash } from "../../ui/Icon";
import { formatMac } from "./HubSecureNat";

/**
 * The hub's switching state: which MAC and IP addresses it has learned on
 * which session. Deleting an entry forces the hub to relearn it -- the fix
 * for a stale entry after a machine moved.
 */
export function HubTables({ hub }: { hub: string }) {
  const t = useT();
  const [tab, setTab] = useState<"mac" | "ip">("mac");
  const [mac, setMac] = useState<Wire[] | null>(null);
  const [ip, setIp] = useState<Wire[] | null>(null);
  const { guard } = useToast();

  const load = useCallback(async () => {
    if (tab === "mac") {
      const r = await api.macTable(hub).catch(() => null);
      if (r) setMac((r.MacTable as Wire[]) ?? []);
    } else {
      const r = await api.ipTable(hub).catch(() => null);
      if (r) setIp((r.IpTable as Wire[]) ?? []);
    }
  }, [hub, tab]);
  usePoll(load, "detail", [hub, tab]);

  const removeMac = (key: number) =>
    guard(async () => {
      await api.deleteMac(hub, key);
      await load();
    }, t("MAC entry deleted."));
  const removeIp = (key: number) =>
    guard(async () => {
      await api.deleteIp(hub, key);
      await load();
    }, t("IP entry deleted."));

  const rows = tab === "mac" ? mac : ip;

  return (
    <>
      <SectionTitle
        count={rows?.length}
        actions={
          <div className="seg">
            <button className={tab === "mac" ? "on" : ""} onClick={() => setTab("mac")}>{t("MAC table")}</button>
            <button className={tab === "ip" ? "on" : ""} onClick={() => setTab("ip")}>{t("IP table")}</button>
          </div>
        }
      >
        {t("Address tables")}
      </SectionTitle>

      {rows === null ? (
        <LoadingBlock />
      ) : rows.length === 0 ? (
        <Empty title={t("table is empty")}>{t("Entries appear as sessions carry traffic.")}</Empty>
      ) : (
        <div className="card tcard">
          <div className="tscroll">
            <table className="dtable">
              <thead>
                <tr>
                  <th>{tab === "mac" ? t("MAC address") : t("IP address")}</th>
                  {tab === "mac" && <th>{t("VLAN")}</th>}
                  {tab === "ip" && <th>{t("Source")}</th>}
                  <th>{t("Session")}</th>
                  <th>{t("Learned")}</th>
                  <th>{t("Updated")}</th>
                  <th className="tact" style={{ width: 60 }} aria-label={t("Delete")} />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={String(r.Key_u32)}>
                    <td className="tmono">{tab === "mac" ? formatMac(String(r.MacAddress_bin ?? "")) : String(r.IpAddress_ip)}</td>
                    {tab === "mac" && <td className="tmono">{Number(r.VlanId_u32) || "—"}</td>}
                    {tab === "ip" && <td>{r.DhcpAllocated_bool ? t("DHCP") : t("learned")}</td>}
                    <td className="tmono">
                      {String(r.SessionName_str ?? "—")}
                      {r.RemoteItem_bool ? <span className="tsub">{t("on {host}", { host: String(r.RemoteHostname_str) })}</span> : null}
                    </td>
                    <td className="tmono">{timeAgo(r.CreatedTime_dt as string)}</td>
                    <td className="tmono">{timeAgo(r.UpdatedTime_dt as string)}</td>
                    <td className="tact">
                      <button
                        className="btn btn--sm btn--ghost"
                        onClick={() => void (tab === "mac" ? removeMac(Number(r.Key_u32)) : removeIp(Number(r.Key_u32)))}
                        aria-label={t("Delete entry")}
                      >
                        <IconTrash size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}
