import { useState } from "react";
import { assemble, apiUrl, createSession, type AssembleResult, type SessionInfo } from "./api";
import { Controls } from "./components/Controls";
import { DropZone } from "./components/DropZone";
import { PagePreview } from "./components/PagePreview";
import { WarningsPanel } from "./components/WarningsPanel";
import "./styles.css";

export function App() {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [prefix, setPrefix] = useState("");
  const [margin, setMargin] = useState(22);
  const [pages, setPages] = useState("");
  const [result, setResult] = useState<AssembleResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [needsSplit, setNeedsSplit] = useState(false);
  const [busy, setBusy] = useState(false);

  async function handleFiles(files: File[]) {
    setError(null); setResult(null); setNeedsSplit(false);
    try {
      const info = await createSession(files);
      setSession(info);
      setPrefix(info.prefix);
    } catch (e: any) {
      setSession(null);
      setError(typeof e.detail === "string" ? e.detail : "Upload validation failed");
    }
  }

  async function handleAssemble() {
    if (!session) return;
    setBusy(true); setError(null);
    try {
      const res = await assemble(session.session_id, {
        prefix,
        margin,
        pages: pages.trim() || undefined,
      });
      setResult(res); setNeedsSplit(false);
    } catch (e: any) {
      setResult(null);
      if (e.detail?.needs_split) {
        setNeedsSplit(true);
        setError(e.detail.message ?? "This count needs a manual page split.");
      } else {
        setError(e.detail?.error ?? "Assembly failed");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="app">
      <h1>Sheet Music Assembler</h1>
      <DropZone onFiles={handleFiles} />
      {session && (
        <p className="status">{session.num_pieces} pieces · 1–{session.num_pieces} ✓</p>
      )}
      {error && <p className="error">{error}</p>}
      {session && (
        <Controls
          prefix={prefix} margin={margin} pages={pages}
          needsSplit={needsSplit} disabled={busy}
          onPrefix={setPrefix} onMargin={setMargin} onPages={setPages}
          onAssemble={handleAssemble}
        />
      )}
      {result && (
        <>
          <p className="status">
            {result.counts.length} page(s): {result.counts.join(", ")}
          </p>
          <WarningsPanel warnings={result.warnings} />
          <a className="download" href={apiUrl(result.pdf_url)} download>Download PDF</a>
          <PagePreview pageUrls={result.page_urls} />
        </>
      )}
    </main>
  );
}
