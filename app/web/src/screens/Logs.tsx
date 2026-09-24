"use client";

import { useCallback, useState } from "react";
import { Empty, LoadingBlock, PageHead, SectionTitle, usePoll } from "../components/bits";
import { Terminal } from "../components/Terminal";
import { api, type Wire } from "../lib/api";
import { useT } from "../lib/i18n";
import { Link } from "../lib/router";
import { useToast } from "../lib/toast";
import { formatBytes, timeAgo } from "../lib/util";
import { IconLogs } from "../ui/Icon";

/**
 * The server's log files: server log, per-hub security and packet logs.
 * Reading is chunked -- ReadLogFile pages by offset -- and rendered in the
 * same terminal well the installer uses.
 */

const CHUNK_HINT = 256 * 1024; // what one ReadLogFile answer tends to carry

export function Logs() {
  const [files, setFiles] = useState<Wire[] | null>(null);
  const [current, setCurrent] = useState<Wire | null>(null);
  const [text, setText] = useState("");
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const { push } = useToast();
  const t = useT();

  const load = useCallback(async () => {
    const r = await api.logFiles().catch(() => null);
    if (r) setFiles((r.LogFiles as Wire[]) ?? []);
  }, []);
  usePoll(load, "list", []);

  const read = async (file: Wire, fromOffset: number, append: boolean) => {
    setLoading(true);
    try {
      const r = await api.readLog(String(file.FilePath_str), fromOffset);
      setCurrent(file);
      setText((t) => (append ? t + r.text : r.text));
      setOffset(fromOffset + (Number((r as Wire).bytes) || r.text.length));
    } catch (e) {
      push("err", e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const tail = (file: Wire) => {
    // Start near the end for big files: the last chunk is what an operator
    // almost always wants first.
    const size = Number(file.FileSize_u32) || 0;
    const start = size > CHUNK_HINT ? size - CHUNK_HINT : 0;
    setText("");
    void read(file, start, false);
  };

  return (
    <div className="page">
      <PageHead title={t("Logs")} sub={<Link to="/" className="linkish">← {t("dashboard")}</Link>} />
      <div className="logsplit">
        <div>
          <SectionTitle count={files?.length}>{t("Files")}</SectionTitle>
          {files === null ? (
            <LoadingBlock />
          ) : files.length === 0 ? (
            <Empty title={t("no log files")}>{t("Logging may be switched off, or nothing has happened yet.")}</Empty>
          ) : (
            <div className="rows">
              {files.map((f) => (
                <button
                  key={String(f.FilePath_str)}
                  className={`row${current?.FilePath_str === f.FilePath_str ? " row--on" : ""}`}
                  onClick={() => tail(f)}
                >
                  <div className="row__main">
                    <div className="row__name">
                      <IconLogs size={14} />
                      <span className="mono truncate">{String(f.FilePath_str)}</span>
                    </div>
                    <div className="spec">
                      <span className="chip"><i>{t("size")}</i>{formatBytes(Number(f.FileSize_u32))}</span>
                      <span className="chip"><i>{t("updated")}</i>{timeAgo(f.UpdatedTime_dt as string)}</span>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="logsplit__view">
          {current ? (
            <>
              <Terminal lines={text ? text.split("\n") : []} live={false} label={String(current.FilePath_str)} />
              <div style={{ display: "flex", gap: "var(--s2)", marginTop: "var(--s2)" }}>
                <button className="btn btn--sm" disabled={loading} onClick={() => void read(current, offset, true)}>
                  {loading && <span className="spin" />} {t("Read more")}
                </button>
                <button className="btn btn--sm" disabled={loading} onClick={() => tail(current)}>
                  {t("Jump to tail")}
                </button>
              </div>
            </>
          ) : (
            <Empty title={t("pick a file")}>{t("The last chunk of the file loads first; page forward from there.")}</Empty>
          )}
        </div>
      </div>
    </div>
  );
}
