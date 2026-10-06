"use client";

import { useCallback, useState } from "react";
import { ConfirmSheet, Empty, ErrorAlert, Field, LoadingBlock, SectionTitle, usePoll } from "../../components/bits";
import { PolicyEditor, extractPolicy } from "../../components/PolicyEditor";
import { api, type Wire } from "../../lib/api";
import { DURATION_GROUPS } from "../../lib/duration";
import { useToast } from "../../lib/toast";
import { useT } from "../../lib/i18n";
import { formatCount } from "../../lib/util";
import { IconPlus, IconUsers, IconBolt } from "../../ui/Icon";
import { Sheet } from "../../ui/Sheet";

/**
 * Groups: a name, and a policy its members inherit. Users point at groups
 * from their own page; here the group itself is managed.
 */
export function HubGroups({ hub }: { hub: string }) {
  const t = useT();
  const [groups, setGroups] = useState<Wire[] | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [durationSheet, setDurationSheet] = useState(false);

  const load = useCallback(async () => {
    const r = await api.groups(hub).catch(() => null);
    if (r) setGroups((r.GroupList as Wire[]) ?? []);
  }, [hub]);
  usePoll(load, "list", [hub]);

  return (
    <>
      <SectionTitle
        count={groups?.length}
        actions={
          <>
            <button className="btn btn--ghost btn--sm" onClick={() => setDurationSheet(true)}>
              <IconBolt size={14} /> {t("Duration groups")}
            </button>
            <button className="btn btn--primary btn--sm" onClick={() => setCreating(true)}>
              <IconPlus size={14} /> {t("New group")}
            </button>
          </>
        }
      >
        {t("Groups")}
      </SectionTitle>

      {groups === null ? (
        <LoadingBlock label={t("loading groups")} />
      ) : groups.length === 0 ? (
        <Empty
          title={t("no groups")}
          action={
            <button className="btn btn--primary" onClick={() => setCreating(true)}>
              <IconPlus size={15} /> {t("Create a group")}
            </button>
          }
        >
          {t(
            "A group carries one security policy for many users: bandwidth tiers, an access cut-off for a whole team, one switch instead of fifty.",
          )}
        </Empty>
      ) : (
        <div className="grid hubgrid">
          {groups.map((g) => (
            <button key={String(g.Name_str)} className="card hubtile" onClick={() => setEditing(String(g.Name_str))}>
              <div className="hubtile__head">
                <span className="hubtile__icon"><IconUsers size={17} /></span>
                <span className="hubtile__name truncate mono">{String(g.Name_str)}</span>
              </div>
              {g.Realname_utf ? <div className="micro truncate">{String(g.Realname_utf)}</div> : null}
              <div className="hubtile__stats">
                <span className="stat"><span className="stat__n">{formatCount(Number(g.NumUsers_u32))}</span><span className="micro">{t("members")}</span></span>
              </div>
            </button>
          ))}
        </div>
      )}

      {durationSheet && (
        <DurationGroupsSheet
          hub={hub}
          existingNames={(groups ?? []).map((g) => String(g.Name_str))}
          onClose={() => setDurationSheet(false)}
          onDone={() => {
            setDurationSheet(false);
            void load();
          }}
        />
      )}
      {(creating || editing) && (
        <GroupSheet
         
          hub={hub}
          name={editing}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSaved={() => {
            setCreating(false);
            setEditing(null);
            void load();
          }}
          onDelete={(n) => {
            setEditing(null);
            setDeleting(n);
          }}
        />
      )}
      {deleting && (
        <ConfirmSheet
          title={t("Delete group {name}?", { name: deleting })}
          verb={t("Delete group")}
          body={<>{t("Members are not deleted; they simply stop belonging to any group.")}</>}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await api.deleteGroup(hub, deleting);
            void load();
          }}
        />
      )}
    </>
  );
}

function GroupSheet({ hub,
  name,
  onClose,
  onSaved,
  onDelete,
}: {
    hub: string;
  name: string | null;
  onClose: () => void;
  onSaved: () => void;
  onDelete: (name: string) => void;
}) {
  const editing = Boolean(name);
  const [groupName, setGroupName] = useState(name ?? "");
  const [realname, setRealname] = useState("");
  const [note, setNote] = useState("");
  const [policy, setPolicy] = useState<Wire>({ UsePolicy_bool: false });
  const [loaded, setLoaded] = useState(!editing);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { push } = useToast();
  const t = useT();

  usePoll(
    async () => {
      if (!editing || loaded) return;
      const g = await api.group(hub, name as string).catch(() => null);
      if (g) {
        setRealname(String(g.Realname_utf ?? ""));
        setNote(String(g.Note_utf ?? ""));
        setPolicy(extractPolicy(g));
        setLoaded(true);
      }
    },
    3600_000,
    [name],
  );

  const save = async () => {
    setError(null);
    setBusy(true);
    try {
      const body: Wire = {
        Realname_utf: realname,
        Note_utf: note,
        ...policy,
      };
      if (editing) {
        await api.setGroup(hub, name as string, body);
      } else {
        body.Name_str = groupName.trim();
        await api.createGroup(hub, body);
      }
      push("ok", editing ? t("Group saved.") : t("Group {name} created.", { name: groupName.trim() }));
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet
      title={editing ? t("Group {name}", { name: name as string }) : t("New group")}
      subtitle={t("hub {hub}", { hub })}
      onClose={onClose}
      wide
      footer={
        <>
          {editing && (
            <button className="btn btn--danger" onClick={() => onDelete(name as string)} style={{ marginInlineEnd: "auto" }}>
              {t("Delete")}
            </button>
          )}
          <button className="btn" onClick={onClose}>{t("Cancel")}</button>
          <button className="btn btn--primary" onClick={save} disabled={busy || (!editing && !groupName.trim()) || !loaded}>
            {busy && <span className="spin" />}
            {editing ? t("Save") : t("Create group")}
          </button>
        </>
      }
    >
      {error && <ErrorAlert>{error}</ErrorAlert>}
      {!loaded ? (
        <LoadingBlock />
      ) : (
        <div style={{ display: "grid", gap: "var(--s1)" }}>
          {!editing && (
            <Field label={t("Group name")}>
              <input className="input mono" value={groupName} onChange={(e) => setGroupName(e.target.value)}
                autoFocus autoCapitalize="none" spellCheck={false} />
            </Field>
          )}
          <div className="row2">
            <Field label={t("Display name")}>
              <input className="input" value={realname} onChange={(e) => setRealname(e.target.value)} />
            </Field>
            <Field label={t("Note")}>
              <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
            </Field>
          </div>
          <PolicyEditor value={policy} onChange={setPolicy} subject="group" />
        </div>
      )}
    </Sheet>
  );
}

/**
 * One action for the subscription plans a VPN operator sells every day:
 * 1, 2, 3, 6 and 9 months, and a year. The missing presets start checked;
 * ones that already exist are shown but not touchable, so a second run is
 * always a no-op rather than a duplicate. Created groups carry the Persian
 * realname the tiles display and a bilingual note for the record.
 */
function DurationGroupsSheet({
  hub,
  existingNames,
  onClose,
  onDone,
}: {
  hub: string;
  existingNames: string[];
  onClose: () => void;
  onDone: () => void;
}) {
  const existing = new Set(existingNames);
  const missing = DURATION_GROUPS.filter((d) => !existing.has(d.name));
  const [selected, setSelected] = useState<Set<string>>(() => new Set(missing.map((d) => d.name)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { push } = useToast();
  const t = useT();

  const toggle = (name: string) => {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const create = async () => {
    setError(null);
    setBusy(true);
    let made = 0;
    const failed: string[] = [];
    for (const d of DURATION_GROUPS) {
      if (!selected.has(d.name)) continue;
      try {
        await api.createGroup(hub, {
          Name_str: d.name,
          Realname_utf: d.fa,
          Note_utf: `Subscription duration: ${d.months} month(s). / گروه اشتراک ${d.months} ماهه.`,
        });
        made += 1;
      } catch (e) {
        failed.push(`${d.name}: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
    setBusy(false);
    if (made > 0) {
      push("ok", t("Duration groups created."));
      onDone();
    } else if (failed.length === 0) {
      onClose();
    } else {
      setError(failed.join(" · "));
    }
  };

  const picked = selected.size;

  return (
    <Sheet
      title={t("Duration groups")}
      subtitle={t("hub {hub}", { hub })}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>{t("Cancel")}</button>
          <button className="btn btn--primary" onClick={create} disabled={busy || picked === 0}>
            {busy && <span className="spin" />}
            {picked === 0
              ? t("Nothing to create")
              : t("Create {count} group(s)", { count: picked })}
          </button>
        </>
      }
    >
      <div style={{ display: "grid", gap: "var(--s1)" }}>
        {error && <ErrorAlert>{error}</ErrorAlert>}
        <p className="micro">
          {t(
            "Creates the standard subscription groups on this hub. Picking one of them for a user fills in a matching expiry date automatically.",
          )}
        </p>
        {DURATION_GROUPS.map((d) => {
          const isExisting = existing.has(d.name);
          const isChecked = selected.has(d.name);
          return (
            <label key={d.name} className="checkrow">
              <input
                type="checkbox"
                checked={isChecked}
                disabled={isExisting || busy}
                onChange={() => toggle(d.name)}
              />
              <span>
                <span className="t">
                  <span className="mono">{d.name}</span>
                  {" · "}
                  {t(d.labelKey)}
                  {isExisting ? ` · ${t("already exists")}` : ""}
                </span>
                <span className="s">{d.fa} · {d.months} {d.months === 1 ? "month" : "months"}</span>
              </span>
            </label>
          );
        })}
        {missing.length === 0 && (
          <p className="micro">{t("All duration groups already exist on this hub.")}</p>
        )}
      </div>
    </Sheet>
  );
}
