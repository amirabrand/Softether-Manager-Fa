"use client";

import { useState } from "react";
import { useAuth } from "../lib/auth";
import { useT } from "../lib/i18n";
import { useServer } from "../lib/server";
import { Link, back, navigate, seg, useRoute } from "../lib/router";
import { useUpdate } from "../lib/update";
import {
  BrandMark,
  IconBack,
  IconBolt,
  IconCheck,
  IconChevron,
  IconClock,
  IconHub,
  IconLogs,
  IconMoon,
  IconPanels,
  IconPulse,
  IconSettings,
  IconSignOut,
  IconSun,
  IconSwap,
  IconTable,
  IconTag,
  IconTerminal,
  IconUsers,
} from "../ui/Icon";
import { useTheme } from "../ui/theme";
import { useI18n } from "../lib/i18n";

/**
 * Two shells, one component.
 *
 * >= 960px: a soft sidebar on the canvas — labelled destinations with the
 * hubs listed beneath, collapsible down to a column of icon keys. Expanded
 * is the default; the choice sticks per browser.
 *
 * < 960px: a native-app shell -- a solid top bar that shows where you are,
 * and a bottom tab bar with three destinations.
 */

function readCollapsed(): boolean {
  try {
    return localStorage.getItem("sem_rail") === "min";
  } catch {
    return false;
  }
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const toggleRail = () =>
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem("sem_rail", next ? "min" : "full");
      } catch {
        /* private mode: the choice just does not stick */
      }
      return next;
    });

  return (
    <div className={`shell${collapsed ? " shell--min" : ""}`}>
      <Side collapsed={collapsed} onToggle={toggleRail} />
      <TopBar />
      <main className="shell__scroll">
        <MainBar />
        {children}
      </main>
      <TabBar />
    </div>
  );
}

/* ── the desktop sidebar ────────────────────────────────────────────────── */

/** Nav destinations; the labels go through t() so they follow the language. */
const DESTINATIONS = (t: (key: string) => string, role: string) =>
  role === "reseller"
    ? [{ to: "/reseller", label: t("Reseller desk"), icon: <IconSwap size={19} />, match: () => true }]
    : [
  { to: "/sales", label: t("Subscription sales"), icon: <IconTag size={19} />, match: (p: string) => p === "sales" },
  { to: "/expirations", label: t("Upcoming expirations"), icon: <IconClock size={19} />, match: (p: string) => p === "expirations" },
  { to: "/telegram", label: t("Telegram bot"), icon: <IconBolt size={19} />, match: (p: string) => p === "telegram" },
  { to: "/resellers", label: t("Resellers"), icon: <IconTable size={19} />, match: (p: string) => p === "resellers" },
  { to: "/users", label: t("Users"), icon: <IconUsers size={19} /> , match: (p: string) => p === "users" },
  { to: "/connections", label: t("Connections"), icon: <IconPulse size={19} />, match: (p: string) => p === "connections" },
  { to: "/logs", label: t("Logs"), icon: <IconLogs size={19} />, match: (p: string) => p === "logs" },
  { to: "/console", label: t("API console"), icon: <IconTerminal size={19} />, match: (p: string) => p === "console" },
  { to: "/settings", label: t("Settings"), icon: <IconSettings size={19} />, match: (p: string) => p === "settings" },
    ];

function Side({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const { user, role, logout } = useAuth();
  const { hubs } = useServer();
  const t = useT();
  const route = useRoute();
  const active = route.parts[0] ?? "";
  const activeHub = active === "hub" ? route.parts[1] : null;

  // The hub leaves carry /hub/* while they are visible; collapsed, the
  // Dashboard key represents them.
  const dashOn =
    active === "" || active === "connect" || active === "server-settings" ||
    (collapsed && active === "hub");

  const item = (
    to: string,
    label: string,
    icon: React.ReactNode,
    on: boolean,
  ) => (
    <Link
      key={to}
      to={to}
      className={`side__item${on ? " on" : ""}`}
      title={collapsed ? label : undefined}
      aria-label={label}
    >
      {icon}
      <span className="side__label">{label}</span>
    </Link>
  );

  return (
    <aside className="side">
      <div className="side__top">
        <Link to="/" className="side__brand" aria-label={t("Dashboard")}>
          <BrandMark size={38} />
          <span className="side__label">
            <span className="side__word">SoftEther</span>
            <span className="side__sub">Manager</span>
          </span>
        </Link>
        <button
          className="icon-btn side__fold"
          onClick={onToggle}
          title={collapsed ? t("Expand the sidebar") : t("Collapse the sidebar")}
          aria-label={collapsed ? t("Expand the sidebar") : t("Collapse the sidebar")}
          aria-expanded={!collapsed}
        >
          <IconChevron size={15} />
        </button>
      </div>

      <nav className="side__nav" aria-label={t("Primary")}>
        {item("/", t("Dashboard"), <IconPanels size={19} />, dashOn)}

        {!collapsed && hubs && hubs.length > 0 && (
          <div className="side__group">
            <div className="side__gtitle">{t("Hubs")}</div>
            {hubs.map((h) => {
              const name = String(h.HubName_str);
              return (
                <button
                  key={name}
                  className={`side__leaf${activeHub === name ? " on" : ""}`}
                  data-state={h.Online_bool ? "connected" : "disabled"}
                  onClick={() => navigate(`/hub/${seg(name)}`)}
                  title={`${name} — ${h.Online_bool ? t("online") : t("offline")}`}
                >
                  <span className="side__dot" />
                  <span className="side__label">{name}</span>
                  <span className="side__count">{Number(h.NumSessions_u32) || ""}</span>
                </button>
              );
            })}
          </div>
        )}
        {collapsed && (
          <Link
            to={activeHub ? `/hub/${seg(activeHub)}` : "/"}
            className={`side__item${active === "hub" ? " on" : ""}`}
            title={t("Hubs")}
            aria-label={t("Hubs")}
          >
            <IconHub size={19} />
            <span className="side__label">{t("Hubs")}</span>
          </Link>
        )}

        {DESTINATIONS(t, role).map((d) => item(d.to, d.label, d.icon, d.match(active)))}
      </nav>

      <div className="side__foot">
        <LangToggle />
        <ThemeToggle />
        <button
          className="side__user"
          onClick={logout}
          title={`${user ?? ""} — ${t("sign out")}`}
          aria-label={t("Sign out")}
        >
          <span className="side__coin">{(user ?? "?").slice(0, 1)}</span>
          <span className="side__label truncate">{user}</span>
          <span className="side__out">
            <IconSignOut size={15} />
          </span>
        </button>
      </div>
    </aside>
  );
}

/* ── the utility cluster at the top of the content ──────────────────────── */

function MainBar() {
  const t = useT();
  const route = useRoute();
  const live =
    route.path === "/" || route.parts[0] === "hub" || route.parts[0] === "connections";
  return (
    <div className="mainbar">
      <HealthChip />
      {live && (
        <span className="mchip mchip--live">
          <span className="mchip__dot" style={{ background: "var(--ok)" }} />
          {t("Live")}
        </span>
      )}
      <VersionPill />
    </div>
  );
}

/** One server, one verdict. */
function HealthChip() {
  const { health, probe } = useServer();
  const t = useT();
  if (health === "probing") return null;
  if (health === "online") {
    return (
      <span className="mchip mchip--ok">
        <IconCheck size={13} />
        {t("VPN server up")}
      </span>
    );
  }
  if (health === "unconfigured") {
    return (
      <Link to="/connect" className="mchip">
        {t("not connected")}
      </Link>
    );
  }
  return (
    <span className="mchip mchip--err" title={probe?.error ?? ""}>
      {t("VPN server down")}
    </span>
  );
}

/* ── mobile chrome ──────────────────────────────────────────────────────── */

function TopBar() {
  const t = useT();
  const route = useRoute();
  const p = route.parts;

  const isPushed = p.length > 0 && !(p[0] === "settings" && p.length === 1) && p[0] !== "users";
  let title = t("Dashboard");
  if (p[0] === "users") title = t("Users");
  else if (p[0] === "sales") title = t("Subscription sales");
  else if (p[0] === "expirations") title = t("Upcoming expirations");
  else if (p[0] === "telegram") title = t("Telegram bot");
  else if (p[0] === "resellers" || p[0] === "reseller") title = t("Reseller desk");
  else if (p[0] === "settings") title = t("Settings");
  else if (p[0] === "connect") title = t("Connect");
  else if (p[0] === "server-settings") title = t("Server settings");
  else if (p[0] === "connections") title = t("Connections");
  else if (p[0] === "logs") title = t("Logs");
  else if (p[0] === "console") title = t("Console");
  else if (p[0] === "hub" && p[1]) {
    title = p[2] === "user" && p[3] ? p[3] : p[1];
  }

  return (
    <header className="topbar">
      {isPushed && p.length > 0 ? (
        <button className="topbar__back" onClick={back} aria-label={t("Back")}>
          <IconBack size={20} />
        </button>
      ) : (
        <BrandMark size={28} />
      )}
      <span className="topbar__title">{title}</span>
      <LangToggle compact />
      <ThemeToggle />
      <VersionPill compact />
    </header>
  );
}

function TabBar() {
  const t = useT();
  const route = useRoute();
  const { role } = useAuth();
  const active = route.parts[0] ?? "";
  const tab = (match: (p: string) => boolean, to: string, label: string, icon: React.ReactNode) => (
    <Link to={to} className="tab" aria-current={match(active) ? "page" : undefined}>
      {icon}
      <span>{label}</span>
    </Link>
  );
  if (role === "reseller") {
    return (
      <nav className="tabbar" aria-label={t("Primary")}>
        {tab(() => true, "/reseller", t("Reseller desk"), <IconSwap />)}
      </nav>
    );
  }
  return (
    <nav className="tabbar" aria-label={t("Primary")}>
      {tab((p) => p === "" || p === "hub" || p === "connect", "/", t("Dashboard"), <IconPanels />)}
      {tab((p) => p === "users", "/users", t("Users"), <IconUsers />)}
      {tab((p) => p === "settings", "/settings", t("Settings"), <IconSettings />)}
    </nav>
  );
}

/* ── shared chrome ──────────────────────────────────────────────────────── */

/** The language switch: shows the language you would get by clicking it. */
export function LangToggle({ compact }: { compact?: boolean }) {
  const { lang, setLang } = useI18n();
  const toEnglish = lang === "fa";
  return (
    <button
      className="icon-btn lang-btn"
      onClick={() => setLang(toEnglish ? "en" : "fa")}
      title={toEnglish ? "Switch to English" : "تغییر به فارسی"}
      aria-label={toEnglish ? "Switch to English" : "تغییر به فارسی"}
    >
      <span className="lang-btn__t">{compact ? (toEnglish ? "EN" : "فا") : toEnglish ? "English" : "فارسی"}</span>
    </button>
  );
}

export function ThemeToggle() {
  const { resolved, toggle } = useTheme();
  const t = useT();
  const dark = resolved === "dark";
  return (
    <button
      className="icon-btn"
      onClick={toggle}
      title={dark ? t("Switch to light") : t("Switch to dark")}
      aria-label={dark ? t("Switch to light theme") : t("Switch to dark theme")}
    >
      {dark ? <IconSun size={17} /> : <IconMoon size={17} />}
    </button>
  );
}

/** The version is a button, because the only thing anyone wants to do with a
 *  version number is find out whether it is the current one. */
export function VersionPill({ compact }: { compact?: boolean }) {
  const { status, open, applying } = useUpdate();
  const t = useT();
  const available = Boolean(status?.check?.update_available);
  const v = String(status?.check?.current_version ?? "");

  return (
    <button
      className={`verpill${available ? " verpill--new" : ""}`}
      onClick={open}
      title={
        applying
          ? t("An update is running")
          : available
            ? t("Version {v} is available", { v: String(status?.check?.latest?.version) })
            : t("Check for updates")
      }
    >
      {applying && <span className="spin" style={{ width: 11, height: 11 }} />}
      <span className="truncate">{compact ? v.replace(/^v/, "") : v || "—"}</span>
      {available && <span className="verpill__dot" />}
    </button>
  );
}

export { seg };
