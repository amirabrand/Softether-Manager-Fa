/** Formatting helpers. Machine values render mono; these make them short. */

/** The panel language, mirrored here by the i18n provider so these plain
 *  functions can localize their few human words and pick a locale for dates
 *  without needing React. */
let currentLang: "fa" | "en" = "fa";

export function setCurrentLang(lang: "fa" | "en"): void {
  currentLang = lang;
}

const WORDS: Record<string, Record<"fa" | "en", string>> = {
  "never": { fa: "هرگز", en: "never" },
  "just now": { fa: "همین حالا", en: "just now" },
  "{n}m ago": { fa: "{n} دقیقه پیش", en: "{n}m ago" },
  "{n}h ago": { fa: "{n} ساعت پیش", en: "{n}h ago" },
  "{n}d ago": { fa: "{n} روز پیش", en: "{n}d ago" },
};

function word(key: string, n?: number): string {
  const template = WORDS[key]?.[currentLang] ?? key;
  return n === undefined ? template : template.replace("{n}", String(n));
}

const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];

export function formatBytes(n: number | undefined | null): string {
  if (n == null || !isFinite(n) || n < 0) return "—";
  if (n === 0) return "0 B";
  const i = Math.min(UNITS.length - 1, Math.floor(Math.log2(n) / 10));
  const v = n / 2 ** (10 * i);
  return `${v >= 100 ? Math.round(v) : v.toFixed(v >= 10 ? 1 : 2)} ${UNITS[i]}`;
}

export function formatRate(bytesPerSecond: number): string {
  return `${formatBytes(bytesPerSecond * 8 >= 0 ? bytesPerSecond : 0)}/s`;
}

export function formatCount(n: number | undefined | null): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  return String(n);
}

/** "3d 4h", "2h 05m", "45s" — from seconds. */
export function formatDuration(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(Math.floor(seconds % 60)).padStart(2, "0")}s`;
  return `${Math.floor(seconds)}s`;
}

/** SoftEther's zero dates mean "never"; render them as such. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return word("never");
  const d = new Date(iso);
  if (!isFinite(d.getTime()) || d.getFullYear() < 1990) return word("never");
  return d.toLocaleString(currentLang === "fa" ? "fa-IR-u-ca-gregory" : undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return word("never");
  const then = new Date(iso).getTime();
  if (!isFinite(then) || new Date(iso).getFullYear() < 1990) return word("never");
  const s = Math.max(0, (Date.now() - then) / 1000);
  if (s < 60) return word("just now");
  if (s < 3600) return word("{n}m ago", Math.floor(s / 60));
  if (s < 86400) return word("{n}h ago", Math.floor(s / 3600));
  return word("{n}d ago", Math.floor(s / 86400));
}

export function classNames(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/** Trigger a browser download from base64 content. */
export function downloadBase64(filename: string, base64: string, mime = "application/octet-stream") {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Trigger a browser download of a text file. */
export function downloadText(filename: string, content: string, mime = "application/octet-stream") {
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result ?? "");
      resolve(result.slice(result.indexOf(",") + 1));
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}
