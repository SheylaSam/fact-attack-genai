import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, ReactNode } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  ArrowUp,
  BookOpen,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  Copy,
  ExternalLink,
  FileText,
  FlaskConical,
  Layers3,
  LoaderCircle,
  Menu,
  MessageSquare,
  Moon,
  Minus,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Square,
  Sun,
  Trash2,
  Upload,
  X,
  Zap,
} from "lucide-react";
import { api, date, download, report } from "./api";
import {
  evidenceStance,
  readableReasoning,
  resultView,
  splitFocus,
} from "./presentation";
import type {
  EvidencePreview,
  Job,
  Mode,
  PdfSelection,
  Source,
  Status,
  Trace,
} from "./types";

// A quote with only its key sentence marked; the box scrolls so that sentence is in view.
function EvidenceQuote({
  quote,
  focus,
  marked,
}: {
  quote: string;
  focus?: string | null;
  marked: boolean;
}) {
  const box = useRef<HTMLQuoteElement>(null);
  const parts = splitFocus(quote, focus);
  useEffect(() => {
    const element = box.current;
    const mark = element?.querySelector("mark");
    if (!element || !mark || !parts?.before) return;
    element.scrollTop = Math.max(
      0,
      (mark as HTMLElement).offsetTop - element.offsetTop - 24,
    );
  }, [quote, focus]);
  if (!marked) return <blockquote>{quote}</blockquote>;
  if (!parts)
    return (
      <blockquote ref={box}>
        {focus === null ? (
          quote
        ) : (
          <mark className="evidence-highlight">{quote}</mark>
        )}
      </blockquote>
    );
  return (
    <blockquote ref={box} className="has-focus">
      <span className="evidence-context">{parts.before}</span>
      <mark className="evidence-highlight">{parts.focus}</mark>
      <span className="evidence-context">{parts.after}</span>
    </blockquote>
  );
}

const EXAMPLES = [
  {
    tag: "Empfehlung prüfen",
    text: "Der Bundesrat empfiehlt, die Änderung des Zivildienstgesetzes anzunehmen.",
    icon: ShieldCheck,
  },
  {
    tag: "Argument zuordnen",
    text: "Das Komitee warnt vor negativen Folgen der Vorlage.",
    icon: MessageSquare,
  },
  {
    tag: "Beleg finden",
    text: "Die Initiative verlangt eine Begrenzung der ständigen Wohnbevölkerung auf zehn Millionen.",
    icon: Search,
  },
];
const NODE_LABELS: Record<string, string> = {
  start: "Prüfung vorbereitet",
  decide: "Nächsten Schritt bestimmen",
  act: "Quellen durchsuchen",
  ask: "Vorlage klären",
  verify: "Behauptung prüfen",
  check_evidence: "Originalbelege validieren",
  nachsuche: "Weitere Textstellen suchen",
  finish: "Prüfung abgeschlossen",
};
const TOOL_LABELS: Record<string, string> = {
  suche_vorlagen: "Passende Vorlage finden",
  suche_passagen: "Relevante Textstellen suchen",
  suche_passagen_vektor: "Semantisch nachsuchen",
  pruefen: "Behauptung mit Apertus prüfen",
  rueckfrage: "Vorlage klären",
  teilaussage_fragen: "Aussage eingrenzen",
  quelle_fehlt: "Quellenauswahl prüfen",
};

function statusText(job: Job) {
  if (job.status === "running") return "Wird geprüft";
  if (job.status === "waiting") return "Rückfrage offen";
  if (job.status === "cancelled") return "Abgebrochen";
  if (job.status === "interrupted") return "Unterbrochen";
  if (job.status === "failed") return "Technischer Fehler";
  return job.result ? resultView(job.result).title : "Abgeschlossen";
}

function IconButton({
  children,
  label,
  onClick,
  className = "",
  disabled = false,
}: {
  children: ReactNode;
  label: string;
  onClick: () => void;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className={`icon-button ${className}`}
      title={label}
      aria-label={label}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}

function PdfModal({
  source,
  page,
  evidence,
  onClose,
}: {
  source: Source;
  page: number;
  evidence?: PdfSelection["evidence"];
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [highlight, setHighlight] = useState<EvidencePreview | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [loading, setLoading] = useState(!!evidence);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  const checkId = evidence?.checkId;
  const evidenceIndex = evidence?.index;
  useEffect(() => {
    setHighlight(null);
    setPreviewError("");
    if (!checkId || evidenceIndex === undefined) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    api<EvidencePreview>(
      `/checks/${checkId}/evidence/${evidenceIndex}/preview`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setHighlight(value);
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted) setPreviewError(error.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [checkId, evidenceIndex]);
  return (
    <dialog
      ref={ref}
      className="pdf-modal"
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal-head">
        <div>
          <span className="eyebrow">ORIGINALQUELLE</span>
          <h2>
            {source.kind === "official"
              ? `Abstimmung · ${date(source.date)}`
              : source.name}
          </h2>
        </div>
        <IconButton label="PDF schliessen" onClick={onClose}>
          <X size={20} />
        </IconButton>
      </div>
      <div className="pdf-note">
        <ShieldCheck size={15} /> Original-PDF · PDF-Seite {page} · Gedruckte
        Seitenzahlen können abweichen.
        <a
          href={`/api/sources/${source.id}/pdf#page=${page}`}
          target="_blank"
          rel="noreferrer"
        >
          Separat öffnen <ExternalLink size={13} />
        </a>
      </div>
      {evidence && (
        <div className="highlight-notice" role="status">
          <span className="highlighter-swatch" aria-hidden="true" />
          <span>
            {loading
              ? "Die Belegstelle wird im Original markiert …"
              : highlight
                ? highlight.reason
                : previewError || "Die Originalseite wird geladen."}
          </span>
        </div>
      )}
      {loading ? (
        <div className="preview-loading">
          <LoaderCircle size={24} className="spinning" />
          <span>Belegseite wird vorbereitet …</span>
        </div>
      ) : highlight ? (
        <div className="highlighted-page">
          <img
            src={highlight.image}
            alt={`Originalseite ${page}${highlight.highlighted ? " mit gelb markierter Belegstelle" : " ohne Markierung"}`}
          />
        </div>
      ) : (
        <iframe
          title={`Original-PDF ${source.name}, Seite ${page}`}
          src={`/api/sources/${source.id}/pdf#page=${page}`}
        />
      )}
    </dialog>
  );
}

function Activity({ job }: { job: Job }) {
  const events = job.events.filter(
    (e) =>
      e.node !== "start" &&
      !(
        e.node === "decide" &&
        job.events.some((a) => a.step === e.step && a.node === "act")
      ),
  );
  return (
    <details
      className="activity"
      open={
        job.status === "running" || job.status === "waiting" ? true : undefined
      }
    >
      <summary>
        <span>
          <Layers3 size={16} /> Prüfweg{" "}
          <span className="count">{events.length}</span>
        </span>
        <ChevronDown size={15} />
      </summary>
      <div className="activity-list">
        {events.map((e: Trace, i) => (
          <div key={i} className={`activity-row ${e.error ? "has-error" : ""}`}>
            <span className="step-dot">
              {e.error ? <X size={11} /> : <Check size={11} />}
            </span>
            <div>
              <strong>
                {TOOL_LABELS[e.tool ?? ""] ?? NODE_LABELS[e.node] ?? e.node}
              </strong>
              <p>{e.summary}</p>
            </div>
            {e.duration_ms > 0 && (
              <small>{(e.duration_ms / 1000).toFixed(1)} s</small>
            )}
          </div>
        ))}
        {job.status === "running" && (
          <div className="activity-row">
            <span className="step-dot spinning">
              <LoaderCircle size={12} />
            </span>
            <div>
              <strong>Der Agent arbeitet weiter …</strong>
              <p>Du kannst zwischenzeitlich eine andere Prüfung öffnen.</p>
            </div>
          </div>
        )}
      </div>
    </details>
  );
}

function ResultCard({
  job,
  sources,
  onPdf,
  onStatement,
  onCopy,
  onRetry,
}: {
  job: Job;
  sources: Source[];
  onPdf: (id: string, page: number, evidenceIndex?: number) => void;
  onStatement: (text: string) => void;
  onCopy: () => void;
  onRetry: () => void;
}) {
  const result = job.result;
  const view = result ? resultView(result) : null;
  const explanation = readableReasoning(result?.reasoning);
  const hasVerdict =
    !!result &&
    ["gestuetzt", "widersprochen", "nicht_belegt"].includes(result.outcome);
  const sourceId = result?.vote?.source_id ?? job.source_id;
  const source = sources.find((s) => s.id === sourceId);
  return (
    <section
      className="answer-card"
      data-verdict={hasVerdict ? view?.tone : undefined}
    >
      <div className="agent-answer-head">
        <span className="agent-avatar">
          <Sparkles size={18} />
        </span>
        <strong>Fact Attack</strong>
        <span className="assistant-tag">Prüfagent</span>
        {job.mode === "demo" && (
          <span className="demo-badge">
            <FlaskConical size={12} /> Demo
          </span>
        )}
      </div>
      {job.mode === "demo" && (
        <div className="demo-notice">
          <FlaskConical size={16} />
          <span>
            <strong>Demonstration ohne Modellanfrage.</strong> Das Ergebnis
            stammt aus einem heuristischen Mock und ist keine inhaltlich
            verlässliche Prüfung.
          </span>
        </div>
      )}
      {job.status === "running" && !result && (
        <div className="working-state">
          <span className="working-orbit">
            <Sparkles size={24} />
          </span>
          <h2>Ich prüfe deine Behauptung.</h2>
          <p>
            Passende Textstellen finden, Aussage vergleichen und Belege prüfen.
          </p>
          <div className="loading-track">
            <span />
          </div>
        </div>
      )}
      {(job.status === "cancelled" || job.status === "interrupted") && (
        <div className="state-message">
          <Square size={23} />
          <h2>
            {job.status === "cancelled"
              ? "Prüfung abgebrochen"
              : "Prüfung unterbrochen"}
          </h2>
          <p>
            {job.error ??
              "Weitere Modellanfragen wurden gesperrt. Eine bereits laufende Anfrage kann noch zu Ende laufen."}
          </p>
        </div>
      )}
      {job.status === "failed" && !result && (
        <div className="state-message negative">
          <CircleHelp size={25} />
          <h2>Die Prüfung konnte nicht abgeschlossen werden.</h2>
          <p>{job.error}</p>
        </div>
      )}
      {result && view && (
        <>
          <div className={`verdict ${view.tone}`}>
            <span className="verdict-icon">
              {view.tone === "positive" ? (
                <CheckCheck size={25} />
              ) : view.tone === "negative" ? (
                <X size={24} />
              ) : view.tone === "neutral" ? (
                <Minus size={23} />
              ) : (
                <CircleHelp size={23} />
              )}
            </span>
            <div>
              <span className="eyebrow">
                {job.mode === "demo"
                  ? "SIMULIERTES ERGEBNIS"
                  : "ERGEBNIS DER DOKUMENTPRÜFUNG"}
              </span>
              <h2>{view.title}</h2>
              <p>{view.text}</p>
            </div>
          </div>
          {hasVerdict && (
            <div className="evidence-scale" aria-label="Bewertung der Aussage">
              {[
                { tone: "positive", label: "Belegt", icon: CheckCheck },
                { tone: "neutral", label: "Neutral", icon: Minus },
                { tone: "negative", label: "Widerspruch", icon: X },
              ].map(({ tone, label, icon: Icon }) => (
                <span
                  key={tone}
                  className={`scale-item ${tone} ${view.tone === tone ? "is-selected" : ""}`}
                  aria-current={view.tone === tone ? "true" : undefined}
                >
                  <Icon size={15} />
                  {label}
                </span>
              ))}
            </div>
          )}
          {explanation && (
            <details className="explanation">
              <summary>Begründung anzeigen</summary>
              <p className="reasoning">{explanation}</p>
            </details>
          )}
          {hasVerdict && result.evidence.length === 0 && (
            <div className="evidence-empty">
              <FileText size={18} />
              <span>
                {view.tone === "neutral"
                  ? "Keine entscheidende Textstelle gefunden."
                  : "Kein gültiger Originalbeleg für dieses Urteil vorhanden."}
              </span>
            </div>
          )}
          {result.error && <p className="inline-error">{result.error}</p>}
          {result.note && (
            <div className="result-note">
              <CircleHelp size={15} />
              <span>{result.note}</span>
            </div>
          )}
          {result.statements?.length > 0 && (
            <div className="choice-list">
              {result.statements.map((text, i) => (
                <button key={i} onClick={() => onStatement(text)}>
                  <span>{text}</span>
                  <ArrowRight size={16} />
                </button>
              ))}
            </div>
          )}
          {result.evidence.length > 0 && (
            <div className="evidence-section">
              <p className="highlight-legend">
                <span className="highlighter-swatch" aria-hidden="true" />
                Gelb markiert den Satz, der am besten zur Aussage passt. Jede
                Passage ist als belegt, neutral oder widersprochen
                gekennzeichnet.
              </p>
              <div className="section-label">
                <span>BELEGE AUS DEM ORIGINAL</span>
                <span>
                  <ShieldCheck size={13} />{" "}
                  {result.evidence_validated
                    ? "Originalzitat geprüft"
                    : "Nicht validiert"}
                </span>
              </div>
              {result.evidence.map((e, i) => {
                const stance = evidenceStance(e, view);
                const StanceIcon =
                  stance.tone === "positive"
                    ? CheckCheck
                    : stance.tone === "negative"
                      ? X
                      : Minus;
                return (
                  <article
                    className={`evidence ${stance.tone}`}
                    key={`${i}-${e.page}`}
                  >
                    <div className="evidence-label">
                      <span className="citation-number">
                        {String(i + 1).padStart(2, "0")}
                      </span>
                      <span>PDF-Seite {e.page}</span>
                      <span className="evidence-status">
                        <StanceIcon size={12} aria-hidden="true" />
                        {stance.label}
                        <span className="evidence-status-detail">
                          · {stance.detail}
                        </span>
                      </span>
                      <span className="evidence-language">
                        {source?.language.toUpperCase()}
                      </span>
                    </div>
                    <EvidenceQuote
                      quote={e.quote}
                      focus={e.focus}
                      marked={result.evidence_validated}
                    />
                    {sourceId && source && (
                      <button
                        className="text-link"
                        onClick={() => onPdf(sourceId, e.page, i)}
                      >
                        Markierte Originalstelle öffnen <ArrowRight size={14} />
                      </button>
                    )}
                  </article>
                );
              })}
            </div>
          )}
          {source && (
            <div className="source-reference">
              <FileText size={18} />
              <div>
                <strong>
                  {source.kind === "official"
                    ? `Abstimmungsbüchlein · ${date(source.date)}`
                    : source.name}
                </strong>
                <span>
                  {source.kind === "official"
                    ? "Schweizerische Eidgenossenschaft"
                    : "Eigenes PDF"}{" "}
                  · {source.language.toUpperCase()}
                </span>
              </div>
              <IconButton
                label="Originalquelle öffnen"
                onClick={() =>
                  onPdf(
                    source.id,
                    result.evidence[0]?.page ?? 1,
                    result.evidence.length ? 0 : undefined,
                  )
                }
              >
                <ExternalLink size={16} />
              </IconButton>
            </div>
          )}
        </>
      )}
      <Activity job={job} />
      <div className="run-metrics" aria-label="Laufmetriken">
        <span>
          <Zap size={12} /> {job.requests_used}/{job.max_requests}{" "}
          Modellanfragen
        </span>
        {job.elapsed_ms > 0 && (
          <span>
            <Clock3 size={12} /> {(job.elapsed_ms / 1000).toFixed(1)} s aktive
            Laufzeit
          </span>
        )}
        {result && job.mode === "live" && (
          <span>
            {(
              result.metrics.input_tokens + result.metrics.output_tokens
            ).toLocaleString("de-CH")}{" "}
            Tokens
          </span>
        )}
      </div>
      {job.status !== "running" && job.status !== "waiting" && (
        <div className="answer-footer">
          <div>
            <IconButton label="Prüfbericht kopieren" onClick={onCopy}>
              <Copy size={15} />
            </IconButton>
            <button
              className="text-button"
              onClick={() =>
                download(
                  `fact-attack-${job.id.slice(0, 8)}.txt`,
                  report(job),
                  "text/plain;charset=utf-8",
                )
              }
            >
              <ArrowDownToLine size={15} /> Bericht
            </button>
            <details className="technical-export">
              <summary>Technische Daten</summary>
              <button
                className="text-button"
                onClick={() =>
                  download(
                    `fact-attack-${job.id.slice(0, 8)}.json`,
                    JSON.stringify(job, null, 2),
                  )
                }
              >
                JSON herunterladen
              </button>
            </details>
          </div>
          <button className="text-button" onClick={onRetry}>
            Erneut prüfen <ArrowRight size={13} />
          </button>
        </div>
      )}
    </section>
  );
}

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(
    location.hash.slice(1) || null,
  );
  const [page, setPage] = useState<"workspace" | "sources" | "about">(
    "workspace",
  );
  const [claim, setClaim] = useState(
    () => sessionStorage.getItem("fact-attack-draft") ?? "",
  );
  const [sourceId, setSourceId] = useState("");
  const [language, setLanguage] = useState("de");
  const [mode, setMode] = useState<Mode>("live");
  const [maxRequests, setMaxRequests] = useState(12);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [search, setSearch] = useState("");
  const [sourceSearch, setSourceSearch] = useState("");
  const [sourceLang, setSourceLang] = useState("all");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sourcePicker, setSourcePicker] = useState(false);
  const [preview, setPreview] = useState<PdfSelection | null>(null);
  const [connection, setConnection] = useState<
    "loading" | "online" | "offline"
  >("loading");
  const [reconnecting, setReconnecting] = useState(false);
  const [dark, setDark] = useState(
    () => localStorage.getItem("fact-attack-theme-v2") !== "light",
  );
  const textarea = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const submitting = useRef(false);
  const selected = jobs.find((j) => j.id === selectedId);
  const chosenSource = sources.find((s) => s.id === sourceId);
  const active =
    selected?.status === "running" || selected?.status === "waiting";
  const updateJob = useCallback(
    (job: Job) =>
      setJobs((current) =>
        [job, ...current.filter((j) => j.id !== job.id)].sort((a, b) =>
          b.created_at.localeCompare(a.created_at),
        ),
      ),
    [],
  );

  const load = useCallback(async () => {
    setError("");
    try {
      const [health, catalog, checks] = await Promise.all([
        api<Status>("/status"),
        api<Source[]>("/sources"),
        api<Job[]>("/checks"),
      ]);
      setStatus(health);
      setSources(catalog);
      setJobs(checks);
      setConnection("online");
      setSourceId(
        (current) =>
          current ||
          (!health.auto_available
            ? (catalog.find((s) => s.language === "de")?.id ?? "")
            : ""),
      );
    } catch (e) {
      setError((e as Error).message);
      setConnection("offline");
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("fact-attack-theme-v2", dark ? "dark" : "light");
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute("content", dark ? "#17212e" : "#f7f4ef");
  }, [dark]);
  useEffect(() => {
    sessionStorage.setItem("fact-attack-draft", claim);
  }, [claim]);
  useEffect(() => {
    history.replaceState(
      null,
      "",
      selectedId ? `#${selectedId}` : location.pathname,
    );
  }, [selectedId]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 3500);
    return () => clearTimeout(timer);
  }, [toast]);

  // A snapshot stream can reconnect without starting another model request.
  const runningKey = jobs
    .filter((j) => j.status === "running")
    .map((j) => j.id)
    .sort()
    .join(",");
  useEffect(() => {
    const streams: EventSource[] = [];
    let disposed = false;
    const timers: ReturnType<typeof setInterval>[] = [];
    for (const id of runningKey.split(",").filter(Boolean)) {
      const stream = new EventSource(`/api/checks/${id}/events`);
      streams.push(stream);
      let poll: ReturnType<typeof setInterval> | undefined;
      const refresh = async () => {
        try {
          const job = await api<Job>(`/checks/${id}`);
          if (!disposed) {
            updateJob(job);
            setReconnecting(false);
          }
          if (job.status !== "running" && poll) clearInterval(poll);
        } catch {
          if (!disposed) setReconnecting(true);
        }
      };
      stream.addEventListener("snapshot", (event) => {
        if (!disposed) {
          updateJob(JSON.parse((event as MessageEvent).data));
          setReconnecting(false);
        }
      });
      stream.addEventListener("done", () => stream.close());
      stream.onerror = () => {
        stream.close();
        if (!disposed) setReconnecting(true);
        if (!poll) {
          void refresh();
          poll = setInterval(() => void refresh(), 2500);
          timers.push(poll);
        }
      };
    }
    return () => {
      disposed = true;
      streams.forEach((s) => s.close());
      timers.forEach(clearInterval);
    };
  }, [runningKey, updateJob]);

  function fresh() {
    setSelectedId(null);
    setPage("workspace");
    setClaim("");
    setError("");
    setSidebarOpen(false);
    setTimeout(() => textarea.current?.focus(), 0);
  }
  function chooseSource(source: Source) {
    setSourceId(source.id);
    setPage("workspace");
    setSourcePicker(false);
    setToast(
      `${source.kind === "official" ? date(source.date) : source.name} als Quelle gewählt`,
    );
  }
  function pickExample(text: string) {
    setClaim(text);
    setSelectedId(null);
    setPage("workspace");
    textarea.current?.focus();
  }
  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (submitting.current || !claim.trim()) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      const job = await api<Job>("/checks", {
        method: "POST",
        body: JSON.stringify({
          claim: claim.trim(),
          source_id: sourceId || null,
          language,
          mode,
          max_requests: maxRequests,
        }),
      });
      updateJob(job);
      setSelectedId(job.id);
      setClaim("");
      setPage("workspace");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  async function action(path: string, body?: object) {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      updateJob(
        await api<Job>(path, {
          method: "POST",
          body: JSON.stringify(body ?? {}),
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setError("");
    if (file.size > 20 * 1024 * 1024) {
      setError("Das PDF darf höchstens 20 MB gross sein.");
      event.target.value = "";
      return;
    }
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const source = await api<Source>(`/uploads`, {
        method: "POST",
        body: form,
      });
      setSources((current) => [
        ...current.filter((s) => s.id !== source.id),
        source,
      ]);
      chooseSource(source);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }
  async function remove(job: Job) {
    if (!confirm("Diese Prüfung dauerhaft aus dem lokalen Verlauf löschen?"))
      return;
    try {
      await api(`/checks/${job.id}`, { method: "DELETE" });
      setJobs((current) => current.filter((j) => j.id !== job.id));
      if (selectedId === job.id) setSelectedId(null);
      setToast("Prüfung gelöscht");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function copy(job: Job) {
    try {
      await navigator.clipboard.writeText(report(job));
      setToast("Prüfbericht kopiert");
    } catch {
      setError(
        "Kopieren nicht möglich. Lade den Prüfbericht als Datei herunter.",
      );
    }
  }
  function retry(job: Job) {
    setClaim(job.claim);
    setSourceId(job.source_id ?? "");
    setLanguage(job.language);
    setMode(job.mode);
    setSelectedId(null);
    textarea.current?.focus();
  }
  const filteredSources = sources.filter(
    (s) =>
      (sourceLang === "all" || s.language === sourceLang) &&
      `${s.name} ${date(s.date)} ${s.votes.join(" ")}`
        .toLowerCase()
        .includes(sourceSearch.toLowerCase()),
  );

  const sourceList = (
    <>
      <div className="source-search">
        <Search size={17} />
        <input
          aria-label="Quellen durchsuchen"
          placeholder="Datum oder Vorlage suchen …"
          value={sourceSearch}
          onChange={(e) => setSourceSearch(e.target.value)}
        />
        <select
          aria-label="Quellensprache filtern"
          value={sourceLang}
          onChange={(e) => setSourceLang(e.target.value)}
        >
          <option value="all">Alle Sprachen</option>
          <option value="de">DE</option>
          <option value="fr">FR</option>
          <option value="it">IT</option>
        </select>
      </div>
      <div className="source-grid">
        {filteredSources.map((s) => (
          <article
            key={s.id}
            className={`source-card ${sourceId === s.id ? "selected" : ""}`}
          >
            <div className="source-card-top">
              <span className="document-icon">
                <FileText size={22} />
              </span>
              <span className="source-lang">{s.language.toUpperCase()}</span>
              {sourceId === s.id && (
                <span className="source-selected">
                  <Check size={12} /> Gewählt
                </span>
              )}
            </div>
            <h3>{s.kind === "official" ? date(s.date) : s.name}</h3>
            <p className="source-kind">
              {s.kind === "official"
                ? "Amtliches Abstimmungsbüchlein"
                : "Eigenes PDF"}
            </p>
            <ul>
              {s.votes.slice(0, 3).map((v) => (
                <li key={v}>{v}</li>
              ))}
            </ul>
            <div className="source-card-actions">
              <button className="text-link" onClick={() => chooseSource(s)}>
                Als Quelle wählen <ArrowRight size={14} />
              </button>
              <IconButton
                label={`PDF ${s.name} öffnen`}
                onClick={() => setPreview({ id: s.id, page: 1 })}
              >
                <ExternalLink size={15} />
              </IconButton>
            </div>
          </article>
        ))}
      </div>
      {!filteredSources.length && (
        <div className="empty-small">
          <Search size={25} />
          <h3>Keine passende Quelle gefunden</h3>
          <p>Ändere deine Suche oder lade ein eigenes PDF hoch.</p>
          <button
            className="secondary-button"
            onClick={() => {
              setSourceSearch("");
              setSourceLang("all");
            }}
          >
            Filter zurücksetzen
          </button>
        </div>
      )}
    </>
  );

  return (
    <div className="app-shell">
      {sidebarOpen && (
        <button
          className="sidebar-backdrop"
          aria-label="Navigation schliessen"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <aside className={`sidebar ${sidebarOpen ? "is-open" : ""}`}>
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            fresh();
          }}
        >
          <span className="brand-mark">
            <Check size={20} strokeWidth={3} />
          </span>
          <span>
            fact<span className="brand-light">attack</span>
            <span className="brand-sub">EVIDENCE OVER OPINION</span>
          </span>
        </a>
        <button className="new-check" onClick={fresh}>
          <Plus size={17} /> Neue Prüfung <span>⌘ K</span>
        </button>
        <nav aria-label="Hauptnavigation">
          <button
            className={page === "workspace" ? "nav-item active" : "nav-item"}
            onClick={() => {
              setPage("workspace");
              setSidebarOpen(false);
            }}
          >
            <MessageSquare size={17} /> Arbeitsbereich{" "}
            <ChevronRight size={13} />
          </button>
          <button
            className={page === "sources" ? "nav-item active" : "nav-item"}
            onClick={() => {
              setPage("sources");
              setSidebarOpen(false);
            }}
          >
            <BookOpen size={17} /> Quellenbibliothek{" "}
            <span className="nav-count">{sources.length}</span>
          </button>
          <button
            className={page === "about" ? "nav-item active" : "nav-item"}
            onClick={() => {
              setPage("about");
              setSidebarOpen(false);
            }}
          >
            <CircleHelp size={17} /> So funktioniert’s
          </button>
        </nav>
        <div className="history-head">
          <span>DEINE PRÜFUNGEN</span>
          <span>{jobs.length}</span>
        </div>
        <div className="history-search">
          <Search size={14} />
          <input
            aria-label="Prüfverlauf durchsuchen"
            placeholder="Verlauf durchsuchen"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="history-list">
          {jobs
            .filter((j) => j.claim.toLowerCase().includes(search.toLowerCase()))
            .map((job) => (
              <div
                key={job.id}
                className={`history-entry ${selectedId === job.id && page === "workspace" ? "selected" : ""}`}
              >
                <button
                  className="history-item"
                  onClick={() => {
                    setSelectedId(job.id);
                    setPage("workspace");
                    setSidebarOpen(false);
                  }}
                >
                  <span
                    className={`history-dot ${job.status === "running" ? "running" : job.status === "waiting" ? "waiting" : job.result ? resultView(job.result).tone : job.status}`}
                  />
                  <span>
                    <strong>{job.claim}</strong>
                    <small>
                      {job.mode === "demo" ? "Demo · " : ""}
                      {statusText(job)}
                    </small>
                  </span>
                </button>
                {!["running", "waiting"].includes(job.status) && (
                  <IconButton
                    label="Prüfung löschen"
                    className="history-delete"
                    onClick={() => void remove(job)}
                  >
                    <Trash2 size={13} />
                  </IconButton>
                )}
              </div>
            ))}
          {!jobs.length && (
            <div className="history-empty">
              <Clock3 size={20} />
              <p>Hier entsteht dein Prüfverlauf.</p>
              <span>Prüfungen bleiben auf diesem Rechner gespeichert.</span>
            </div>
          )}
        </div>
        <div className="sidebar-bottom">
          <div className="local-box">
            <span className={`status-dot ${connection}`} />
            <div>
              <strong>
                {connection === "online"
                  ? "Lokal verbunden"
                  : connection === "loading"
                    ? "Verbindung wird aufgebaut"
                    : "Backend nicht erreichbar"}
              </strong>
              <span>
                {status?.api_configured
                  ? "Apertus konfiguriert"
                  : "Demomodus verfügbar"}
              </span>
            </div>
            <ShieldCheck size={16} />
          </div>
          <div className="sidebar-bottom-line">
            <span className="swiss-flag">✚</span>
            <span>Swiss voting. Clearly checked.</span>
            <IconButton
              label={dark ? "Helles Design" : "Dunkles Design"}
              onClick={() => setDark((v) => !v)}
            >
              {dark ? <Sun size={16} /> : <Moon size={16} />}
            </IconButton>
          </div>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div>
            <IconButton
              label="Navigation öffnen"
              className="mobile-menu"
              onClick={() => setSidebarOpen(true)}
            >
              <Menu size={19} />
            </IconButton>
            <span className="breadcrumb">Workspace</span>
            <ChevronRight size={12} />
            <strong>
              {page === "sources"
                ? "Quellenbibliothek"
                : page === "about"
                  ? "So funktioniert’s"
                  : "Prüfagent"}
            </strong>
          </div>
          <div>
            <span className="private-label">
              <ShieldCheck size={14} /> Lokaler Arbeitsbereich
            </span>
            <span className={`mode-indicator ${mode === "demo" ? "demo" : ""}`}>
              <span />
              {mode === "demo" ? "Demomodus" : "Apertus"}
            </span>
          </div>
        </header>
        {error && (
          <div className="error-banner" role="alert">
            <CircleHelp size={17} />
            <span>{error}</span>
            {connection === "offline" && (
              <button onClick={() => void load()}>Erneut verbinden</button>
            )}
            <IconButton
              label="Fehlermeldung schliessen"
              onClick={() => setError("")}
            >
              <X size={15} />
            </IconButton>
          </div>
        )}
        {reconnecting && (
          <div className="reconnect-banner" role="status">
            <LoaderCircle size={15} className="spinning" /> Live-Verbindung wird
            wiederhergestellt. Deine Prüfung läuft im Backend weiter.
          </div>
        )}

        {page === "workspace" && (
          <div className={`workspace ${selected ? "has-conversation" : ""}`}>
            {!selected && (
              <section className="welcome">
                <div className="welcome-label">
                  <span className="tiny-spark">
                    <Sparkles size={14} />
                  </span>{" "}
                  DEIN AGENT FÜR SCHWEIZER ABSTIMMUNGEN
                </div>
                <h1>
                  Behauptungen prüfen.
                  <br />
                  <span>Mit Quellen statt Vermutungen.</span>
                </h1>
                <p>
                  Stelle eine Behauptung auf. Der Agent sucht im
                  Abstimmungsbüchlein,
                  <br className="desktop-break" /> prüft den Zusammenhang und
                  zeigt dir die Originalbelege.
                </p>
                <div className="capability-pills">
                  <span>
                    <Search size={14} /> Quellenbasiert
                  </span>
                  <span>
                    <Layers3 size={14} /> Nachvollziehbar
                  </span>
                  <span>
                    <ShieldCheck size={14} /> Belege geprüft
                  </span>
                </div>
              </section>
            )}
            {selected && (
              <div className="conversation">
                <div className="conversation-toolbar">
                  <button className="text-button" onClick={fresh}>
                    <Plus size={15} /> Neue Prüfung
                  </button>
                  <span>
                    {new Date(selected.created_at).toLocaleString("de-CH", {
                      dateStyle: "medium",
                      timeStyle: "short",
                    })}
                  </span>
                </div>
                <div className="user-message">
                  <span className="eyebrow">DEINE BEHAUPTUNG</span>
                  <p>{selected.claim}</p>
                  <div>
                    <span>{selected.language.toUpperCase()}</span>
                    <span>
                      {selected.source_id
                        ? (sources.find((s) => s.id === selected.source_id)
                            ?.name ?? selected.source_id)
                        : "Automatische Quellenauswahl"}
                    </span>
                  </div>
                </div>
                <ResultCard
                  job={selected}
                  sources={sources}
                  onPdf={(id, p, index) =>
                    setPreview({
                      id,
                      page: p,
                      evidence:
                        index !== undefined
                          ? { checkId: selected.id, index }
                          : undefined,
                    })
                  }
                  onStatement={pickExample}
                  onCopy={() => void copy(selected)}
                  onRetry={() => retry(selected)}
                />
                {selected.pending && (
                  <section className="question-card" aria-live="polite">
                    <span className="eyebrow">
                      <MessageSquare size={14} /> EINE KURZE RÜCKFRAGE
                    </span>
                    <h2>{selected.pending.question}</h2>
                    <p>
                      Deine Behauptung passt zu mehreren Vorlagen. Wähle das
                      Büchlein, das du meinst. Danach wird dieselbe Prüfung
                      fortgesetzt.
                    </p>
                    <div className="choice-list">
                      {selected.pending.options.map((v) => (
                        <button
                          disabled={busy}
                          key={v.vote_id}
                          onClick={() =>
                            void action(`/checks/${selected.id}/answer`, {
                              vote_id: v.vote_id,
                            })
                          }
                        >
                          <div>
                            <strong>{v.title}</strong>
                            <small>
                              Abstimmungsbüchlein {date(v.date)} ·{" "}
                              {v.language.toUpperCase()}
                              {v.passage
                                ? ` · PDF-Seite ${v.passage.page}`
                                : ""}
                            </small>
                            {v.passage && (
                              <q className="option-passage">
                                {v.passage.excerpt}
                              </q>
                            )}
                          </div>
                          <ArrowRight size={17} />
                        </button>
                      ))}
                      <button
                        disabled={busy}
                        onClick={() =>
                          void action(`/checks/${selected.id}/answer`, {
                            vote_id: "keine",
                          })
                        }
                      >
                        Keine dieser Vorlagen
                      </button>
                    </div>
                  </section>
                )}
                {active && (
                  <button
                    className="stop-button"
                    disabled={busy || selected.status === "cancelled"}
                    onClick={() => void action(`/checks/${selected.id}/cancel`)}
                  >
                    <Square size={13} /> Prüfung abbrechen
                  </button>
                )}
              </div>
            )}

            {!active && (
              <form className="composer-area" onSubmit={(e) => void submit(e)}>
                <div className="composer">
                  <div className="composer-title">
                    <span>
                      <span className="composer-icon">
                        <Sparkles size={16} />
                      </span>
                      {selected
                        ? "Eine weitere Behauptung prüfen"
                        : "Was möchtest du überprüfen?"}
                    </span>
                    <div className="mode-switch" aria-label="Prüfmodus">
                      <button
                        type="button"
                        className={mode === "live" ? "selected" : ""}
                        aria-pressed={mode === "live"}
                        onClick={() => setMode("live")}
                      >
                        Live
                      </button>
                      <button
                        type="button"
                        className={mode === "demo" ? "selected" : ""}
                        aria-pressed={mode === "demo"}
                        onClick={() => setMode("demo")}
                      >
                        <FlaskConical size={12} /> Demo
                      </button>
                    </div>
                  </div>
                  <textarea
                    ref={textarea}
                    aria-label="Behauptung eingeben"
                    placeholder="Zum Beispiel: Der Bundesrat empfiehlt, die Vorlage anzunehmen …"
                    value={claim}
                    maxLength={status?.max_claim_chars ?? 1000}
                    onChange={(e) => setClaim(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                        e.preventDefault();
                        void submit();
                      }
                    }}
                    rows={3}
                  />
                  <div className="composer-source">
                    <BookOpen size={15} />
                    <label htmlFor="source">Quelle</label>
                    <select
                      id="source"
                      value={sourceId}
                      onChange={(e) => setSourceId(e.target.value)}
                    >
                      <option
                        value=""
                        disabled={!status?.auto_available && mode !== "demo"}
                      >
                        Automatisch finden
                        {!status?.auto_available ? " · Index erforderlich" : ""}
                      </option>
                      {sources.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.kind === "official"
                            ? `${date(s.date)} · ${s.language.toUpperCase()}`
                            : s.name}
                        </option>
                      ))}
                    </select>
                    <IconButton
                      label="Quellenbibliothek öffnen"
                      onClick={() => setSourcePicker(true)}
                    >
                      <Layers3 size={15} />
                    </IconButton>
                  </div>
                  <div className="composer-bottom">
                    <div>
                      <button
                        type="button"
                        className="attach-button"
                        onClick={() => fileInput.current?.click()}
                        disabled={uploading}
                      >
                        {uploading ? (
                          <LoaderCircle size={15} className="spinning" />
                        ) : (
                          <Plus size={17} />
                        )}
                        <span>PDF</span>
                      </button>
                      <span className="toolbar-divider" />
                      <select
                        aria-label="Sprache der Behauptung"
                        value={language}
                        onChange={(e) => setLanguage(e.target.value)}
                      >
                        <option value="de">Deutsch</option>
                        <option value="fr">Français</option>
                        <option value="it">Italiano</option>
                      </select>
                      <details className="budget-options">
                        <summary title="Anfragelimit einstellen">
                          <Zap size={13} />
                          <span>Max. {maxRequests} Anfragen</span>
                          <ChevronDown size={11} />
                        </summary>
                        <div>
                          <label htmlFor="budget">
                            Modellanfragen pro Prüfung
                          </label>
                          <input
                            id="budget"
                            type="range"
                            min="1"
                            max="12"
                            value={maxRequests}
                            onChange={(e) =>
                              setMaxRequests(Number(e.target.value))
                            }
                          />
                          <p>
                            Inklusive Suche, Wiederholungen und NLI. Ein
                            niedriger Wert kann die Prüfung unterbrechen.
                          </p>
                        </div>
                      </details>
                    </div>
                    <span className="char-count">{claim.length}/1.000</span>
                    <button
                      type="submit"
                      className="send-button"
                      aria-label="Behauptung prüfen"
                      disabled={
                        busy ||
                        !claim.trim() ||
                        connection !== "online" ||
                        (mode === "live" && !status?.api_configured)
                      }
                    >
                      {busy ? (
                        <LoaderCircle size={18} className="spinning" />
                      ) : (
                        <ArrowUp size={20} />
                      )}
                      <span>Prüfen</span>
                    </button>
                  </div>
                </div>
                <div className="composer-caption">
                  <span>
                    {mode === "demo" ? (
                      <>
                        <FlaskConical size={12} /> Demo: keine Modellanfragen,
                        kein verlässliches Sachurteil.
                      </>
                    ) : !status?.api_configured ? (
                      <>
                        <CircleHelp size={12} /> API_KEY in .env eintragen oder
                        oben „Demo“ wählen.
                      </>
                    ) : (
                      <>
                        <ShieldCheck size={12} /> Originalquellen. Transparente
                        Prüfschritte. Keine Abstimmungsempfehlung.
                      </>
                    )}
                  </span>
                  <span>⌘ / Ctrl + Enter</span>
                </div>
              </form>
            )}

            {!selected && (
              <>
                <section className="example-section">
                  <div className="section-label">
                    <span>MIT EINER BEHAUPTUNG STARTEN</span>
                    <span>Beispiele zum Ausprobieren</span>
                  </div>
                  <div className="example-grid">
                    {EXAMPLES.map(({ tag, text, icon: Icon }) => (
                      <button
                        className="example-card"
                        key={tag}
                        onClick={() => pickExample(text)}
                      >
                        <span className="example-icon">
                          <Icon size={17} />
                        </span>
                        <h3>{tag}</h3>
                        <p>{text}</p>
                        <ArrowRight size={16} />
                      </button>
                    ))}
                  </div>
                </section>
                <div className="scope-note">
                  <span className="scope-icon">
                    <BookOpen size={18} />
                  </span>
                  <p>
                    <strong>Ein Urteil mit klarem Bezug.</strong> Wir prüfen,
                    was im ausgewählten Dokument steht. „Neutral“ bedeutet nicht
                    „falsch“.
                  </p>
                </div>
              </>
            )}
            <footer className="workspace-footer">
              <span>FACT ATTACK</span>
              <span>Entwickelt für nachvollziehbare Abstimmungschecks</span>
              <span>DE · FR · IT</span>
            </footer>
          </div>
        )}

        {page === "sources" && (
          <section className="library page-content">
            <div className="page-heading">
              <div>
                <span className="eyebrow">DEINE WISSENSGRUNDLAGE</span>
                <h1>
                  Quellenbibliothek<span>.</span>
                </h1>
                <p>Amtliche Abstimmungsbüchlein und deine eigenen Dokumente.</p>
              </div>
              <button
                className="primary-button"
                onClick={() => fileInput.current?.click()}
                disabled={uploading}
              >
                <Upload size={16} />
                {uploading ? "Wird hochgeladen …" : "PDF hinzufügen"}
              </button>
            </div>
            <div className="library-note">
              <ShieldCheck size={17} />
              <p>
                {sources.filter((s) => s.kind === "official").length} amtliche
                PDFs lokal verfügbar. Eigene Uploads bleiben separat
                gekennzeichnet. Bei Live-Prüfungen werden relevante Textstellen
                an den Apertus-Endpoint gesendet.
              </p>
            </div>
            {sourceList}
          </section>
        )}

        {page === "about" && (
          <section className="about page-content">
            <span className="eyebrow">VOM CLAIM ZUM BELEG</span>
            <h1>
              Nachvollziehbar.
              <br />
              <span>Schritt für Schritt.</span>
            </h1>
            <p className="about-intro">
              Fact Attack prüft die Beziehung zwischen einer Behauptung und
              einem Dokument. Jeder Lauf hat einen begrenzten Suchweg und ein
              festes Anfragebudget.
            </p>
            {[
              {
                icon: MessageSquare,
                title: "01 · Behauptung formulieren",
                text: "Eine konkrete Aussage pro Prüfung. Bei mehreren Teilaussagen kann der Agent nachfragen, welche er prüfen soll.",
              },
              {
                icon: BookOpen,
                title: "02 · Quelle bestimmen",
                text: "Der Agent sucht die passende Vorlage selbst in den lokalen deutschen Abstimmungsbüchlein und fragt bei Mehrdeutigkeit nach. Du kannst auch ein bestimmtes Büchlein oder ein eigenes PDF wählen.",
              },
              {
                icon: Search,
                title: "03 · Textstellen suchen & prüfen",
                text: "Die bestehende Retrieval-Pipeline wählt relevante Passagen. Apertus vergleicht die Behauptung mit diesen Textstellen. Bei fehlendem Beleg gibt es höchstens eine Nachsuche.",
              },
              {
                icon: ShieldCheck,
                title: "04 · Originalbelege validieren",
                text: "Seite und Zitat werden gegen den extrahierten PDF-Text geprüft. Du kannst die Originalquelle öffnen und den Bericht exportieren. Wortgetreue Zitate allein garantieren keine semantisch korrekte Antwort.",
              },
            ].map(({ icon: Icon, title, text }) => (
              <article className="how-step" key={title}>
                <span>
                  <Icon size={23} />
                </span>
                <div>
                  <h2>{title}</h2>
                  <p>{text}</p>
                </div>
              </article>
            ))}
            <div className="about-grid">
              <article>
                <h3>Lokal gespeichert</h3>
                <p>
                  Prüfverlauf und Uploads liegen auf diesem Rechner.
                  Browser-Neuladen unterbricht laufende Prüfungen nicht. Nach
                  einem Backend-Neustart muss eine offene Prüfung neu gestartet
                  werden.
                </p>
              </article>
              <article>
                <h3>Remote geprüft</h3>
                <p>
                  Im Live-Modus gehen Behauptung und ausgewählte Textstellen an
                  den konfigurierten Apertus-Endpoint. Der API-Schlüssel bleibt
                  im Backend. Im Demo-Modus gibt es keine Modellanfragen.
                </p>
              </article>
              <article>
                <h3>Bewusst begrenzt</h3>
                <p>
                  Keine allgemeine Internetrecherche und keine politische
                  Empfehlung. Technische Fehler und fehlende Quellen werden
                  getrennt von inhaltlichen Urteilen angezeigt.
                </p>
              </article>
              <article>
                <h3>Sprachen</h3>
                <p>
                  Deutsch, Französisch und Italienisch bei gewählter Quelle. Die
                  automatische Vorlagensuche ist derzeit auf deutsche Quellen
                  begrenzt.
                </p>
              </article>
            </div>
            <button className="primary-button" onClick={fresh}>
              Erste Behauptung prüfen <ArrowRight size={16} />
            </button>
          </section>
        )}
      </main>

      <aside className="context-panel">
        <div className="context-head">
          <span>PRÜFKONTEXT</span>
          <IconButton
            label="Quellen anzeigen"
            onClick={() => setPage("sources")}
          >
            <BookOpen size={16} />
          </IconButton>
        </div>
        <div className="context-agent">
          <span className="context-agent-icon">
            <Sparkles size={22} />
          </span>
          <h3>Dein Faktencheck-Agent</h3>
          <p>
            Findet Textstellen.
            <br />
            Prüft Aussagen.
            <br />
            Macht Belege sichtbar.
          </p>
          <div>
            <span
              className={`status-dot ${status?.api_configured ? "online" : "loading"}`}
            />
            {status?.api_configured ? "Apertus konfiguriert" : "Demo bereit"}
          </div>
        </div>
        <div className="context-section">
          <span className="eyebrow">AKTUELLE QUELLE</span>
          {(
            selected
              ? sources.find(
                  (s) =>
                    s.id ===
                    (selected.result?.vote?.source_id ?? selected.source_id),
                )
              : chosenSource
          ) ? (
            (() => {
              const s = (
                selected
                  ? sources.find(
                      (s) =>
                        s.id ===
                        (selected.result?.vote?.source_id ??
                          selected.source_id),
                    )
                  : chosenSource
              )!;
              return (
                <button
                  className="context-source"
                  onClick={() => setPreview({ id: s.id, page: 1 })}
                >
                  <span>
                    <FileText size={18} />
                    <span className="source-lang">
                      {s.language.toUpperCase()}
                    </span>
                  </span>
                  <strong>
                    {s.kind === "official" ? date(s.date) : s.name}
                  </strong>
                  <small>
                    {s.kind === "official"
                      ? "Amtliches Abstimmungsbüchlein"
                      : "Eigenes Dokument"}
                  </small>
                  <span className="text-link">
                    Original ansehen <ExternalLink size={12} />
                  </span>
                </button>
              );
            })()
          ) : (
            <div className="auto-source">
              <Search size={22} />
              <p>Der Agent sucht eine passende Vorlage.</p>
            </div>
          )}
          <button
            className="context-change"
            onClick={() => setSourcePicker(true)}
          >
            Andere Quelle wählen <ArrowRight size={13} />
          </button>
        </div>
        <div className="context-section">
          <span className="eyebrow">SO LIEST DU DAS ERGEBNIS</span>
          <div className="legend">
            <span className="legend-dot positive" />
            <div>
              <strong>Belegt</strong>
              <p>Das Dokument bestätigt die Aussage.</p>
            </div>
          </div>
          <div className="legend">
            <span className="legend-dot negative" />
            <div>
              <strong>Widerspruch</strong>
              <p>Das Dokument widerspricht der Aussage.</p>
            </div>
          </div>
          <div className="legend">
            <span className="legend-dot neutral" />
            <div>
              <strong>Neutral</strong>
              <p>Im geprüften Kontext nicht entscheidbar.</p>
            </div>
          </div>
        </div>
        <div className="context-foot">
          <ShieldCheck size={17} />
          <p>Ein Beleg ist besser als ein Bauchgefühl.</p>
          <small>
            Die Erreichbarkeit wird erst bei einer Live-Prüfung festgestellt.
          </small>
        </div>
      </aside>

      <input
        ref={fileInput}
        type="file"
        accept="application/pdf,.pdf"
        aria-label="PDF hochladen"
        className="visually-hidden"
        onChange={(e) => void upload(e)}
      />
      {sourcePicker && (
        <SourceDialog onClose={() => setSourcePicker(false)}>
          {sourceList}
        </SourceDialog>
      )}
      {preview && sources.find((s) => s.id === preview.id) && (
        <PdfModal
          source={sources.find((s) => s.id === preview.id)!}
          page={preview.page}
          evidence={preview.evidence}
          onClose={() => setPreview(null)}
        />
      )}
      {toast && (
        <div className="toast" role="status">
          <Check size={16} />
          {toast}
        </div>
      )}
      <KeyboardShortcut onNew={fresh} />
    </div>
  );
}

function SourceDialog({
  children,
  onClose,
}: {
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className="source-modal"
      onCancel={onClose}
      onClick={(e) => {
        if (e.currentTarget === e.target) onClose();
      }}
    >
      <div className="modal-head">
        <div>
          <span className="eyebrow">WISSENSGRUNDLAGE</span>
          <h2>Quelle auswählen</h2>
        </div>
        <IconButton label="Quellenauswahl schliessen" onClick={onClose}>
          <X size={20} />
        </IconButton>
      </div>
      {children}
    </dialog>
  );
}

function KeyboardShortcut({ onNew }: { onNew: () => void }) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        onNew();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onNew]);
  return null;
}
