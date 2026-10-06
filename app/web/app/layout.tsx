import type { Metadata, Viewport } from "next";
import "../src/styles.css";

export const metadata: Metadata = {
  title: "AMIRITPANEL",
  description: "AMIRITPANEL — self-hosted VPN management and shop panel.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#16181d" },
    { media: "(prefers-color-scheme: light)", color: "#e3e7ee" },
  ],
};

/**
 * Fonts are vendored files in public/, declared here with *relative* URLs so
 * they resolve wherever the panel is mounted -- the CSS bundle would rewrite
 * them to absolute /_next paths, which a secret-prefix install cannot serve.
 * Vazirmatn carries the Persian (Arabic-script) glyphs; Manrope and Geist
 * cover the Latin UI and the monospace figures.
 */
const fontFaces = `
@font-face{font-family:"Manrope";font-style:normal;font-display:swap;font-weight:200 800;
src:url("./fonts/manrope-latin-wght-normal.woff2") format("woff2-variations")}
@font-face{font-family:"Geist Mono";font-style:normal;font-display:swap;font-weight:100 900;
src:url("./fonts/geist-mono-latin-wght-normal.woff2") format("woff2-variations")}
@font-face{font-family:"Vazirmatn";font-style:normal;font-display:swap;font-weight:100 900;
src:url("./fonts/vazirmatn-wght-normal.woff2") format("woff2-variations")}
`;

/**
 * Resolve the stored theme before first paint, so a light-theme user never
 * sees a dark flash (or the reverse). Mirrors src/ui/theme.tsx.
 */
const themeScript = `
try{var t=localStorage.getItem("sem_theme");
if(t==="light"||t==="dark"){document.documentElement.setAttribute("data-theme",t);
document.documentElement.style.colorScheme=t;}}catch(e){}
document.documentElement.classList.add("preload");
requestAnimationFrame(function(){requestAnimationFrame(function(){
document.documentElement.classList.remove("preload")})});
`;

/**
 * Resolve the stored language before first paint, so a Persian reader never
 * sees a left-to-right flash. Mirrors src/lib/i18n.tsx. Default is Persian.
 */
const langScript = `
try{var l=localStorage.getItem("sem_lang");if(l!=="en"&&l!=="fa")l="fa";
document.documentElement.setAttribute("lang",l);
document.documentElement.setAttribute("dir",l==="fa"?"rtl":"ltr");
document.documentElement.classList.toggle("rtl",l==="fa");}catch(e){}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fa" dir="rtl" suppressHydrationWarning>
      <head>
        <meta name="color-scheme" content="dark light" />
        <meta name="mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
        <meta name="apple-mobile-web-app-title" content="AMIRITPANEL" />
        {/* Relative hrefs for the same reason as the fonts: the panel may be
            mounted under a secret prefix, and "./" resolves wherever it is. */}
        <link rel="icon" type="image/png" sizes="256x256" href="./favicon.png" />
        <link rel="apple-touch-icon" sizes="180x180" href="./apple-touch-icon.png" />
        <style dangerouslySetInnerHTML={{ __html: fontFaces }} />
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        <script dangerouslySetInnerHTML={{ __html: langScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
