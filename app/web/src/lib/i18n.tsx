"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { fa } from "./lang/fa";
import { setCurrentLang } from "./util";

/**
 * Two languages: English and Persian.
 *
 * The English string is the key. English needs no dictionary -- t() returns
 * the key itself -- so only one translation table exists and a string that
 * has not been translated yet falls back to readable English instead of an
 * identifier.
 *
 * The choice is written to localStorage and mirrored onto <html> as lang and
 * dir, which is what turns the whole sheet RTL for Persian. A head script in
 * app/layout.tsx applies the stored choice before first paint, so a Persian
 * reader never sees a left-to-right flash; this provider keeps React state
 * agree with it afterwards.
 */
export type Lang = "fa" | "en";

const STORAGE_KEY = "sem_lang";

export type InterpolationParams = Record<string, string | number>;

function interpolate(template: string, params?: InterpolationParams): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in params ? String(params[key]) : match,
  );
}

interface I18nApi {
  lang: Lang;
  setLang: (l: Lang) => void;
  /** Translate an English string (the key) into the active language. */
  t: (key: string, params?: InterpolationParams) => string;
  isRTL: boolean;
}

const I18nContext = createContext<I18nApi>(null as unknown as I18nApi);

function readStored(): Lang {
  if (typeof window === "undefined") return "fa";
  const v = localStorage.getItem(STORAGE_KEY);
  return v === "en" || v === "fa" ? v : "fa";
}

function apply(lang: Lang): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.setAttribute("lang", lang);
  root.setAttribute("dir", lang === "fa" ? "rtl" : "ltr");
  root.classList.toggle("rtl", lang === "fa");
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => readStored());

  useEffect(() => {
    apply(lang);
    setCurrentLang(lang);
    localStorage.setItem(STORAGE_KEY, lang);
  }, [lang]);

  const api = useMemo<I18nApi>(
    () => ({
      lang,
      setLang: setLangState,
      isRTL: lang === "fa",
      t: (key, params) => {
        if (lang === "en") return interpolate(key, params);
        const table = fa as Record<string, string>;
        const template = Object.prototype.hasOwnProperty.call(table, key) ? table[key] : key;
        return interpolate(template, params);
      },
    }),
    [lang],
  );

  return <I18nContext.Provider value={api}>{children}</I18nContext.Provider>;
}

export const useI18n = () => useContext(I18nContext);

/** `const t = useT();` inside a component, then t("Users"). */
export const useT = () => useI18n().t;
