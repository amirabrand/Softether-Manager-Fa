"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useT } from "../lib/i18n";
import { useToast } from "../lib/toast";
import { DURATION_GROUPS } from "../lib/duration";
import { formatDate } from "../lib/util";
import { IconRefresh } from "../ui/Icon";

/**
 * One-click renewal: press, pick a duration, the expiry moves.
 *
 * The new date grows from the later of now and the current expiry, so an
 * active subscription keeps its paid days and an expired one starts fresh.
 * The small variant fits a table row; the labelled variant reads as a
 * button wherever there is room.
 */
export function RenewMenu({
  hub,
  name,
  months,
  onRenewed,
  labelled = false,
}: {
  hub: string;
  name: string;
  /** The user's duration group, when the row knows it -- offered first. */
  months?: number | null;
  onRenewed?: (newExpire: string) => void;
  labelled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const t = useT();
  const { push } = useToast();

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const doRenew = async (m: number) => {
    setBusy(true);
    try {
      const out = await api.renew(hub, name, m);
      const when = formatDate(out.ExpireTime_dt);
      push("ok", t("Renewed {name} — new expiry {date}", { name, date: when }));
      setOpen(false);
      onRenewed?.(out.ExpireTime_dt);
    } catch (e) {
      push("err", e instanceof Error ? t(e.message) : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="renew" ref={box}>
      <button
        className={`btn btn--sm ${labelled ? "btn--ghost" : "btn--ghost"}`}
        disabled={busy}
        title={t("Renew subscription")}
        aria-label={t("Renew subscription")}
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
      >
        {busy ? <span className="spin" style={{ width: 12, height: 12 }} /> : <IconRefresh size={14} />}
        {labelled && <span>{t("Renew")}</span>}
      </button>
      {open && (
        <div className="renew__menu" role="menu">
          <div className="renew__title">{t("Extend by")}</div>
          {DURATION_GROUPS.map((d) => (
            <button
              key={d.name}
              className={`renew__item${months === d.months ? " renew__item--sug" : ""}`}
              role="menuitem"
              disabled={busy}
              onClick={(e) => {
                e.stopPropagation();
                void doRenew(d.months);
              }}
            >
              <span>{t(d.labelKey)}</span>
              {months === d.months && <span className="renew__sug">{t("group duration")}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
