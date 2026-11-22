import { readableReasoning, resultView } from "./presentation";

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      headers: {
        ...(options?.body instanceof FormData
          ? {}
          : { "Content-Type": "application/json" }),
        ...options?.headers,
      },
    });
  } catch {
    throw new Error(
      "Das lokale Backend ist nicht erreichbar. Starte die Anwendung mit make web.",
    );
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : `Die Anfrage konnte nicht verarbeitet werden (${response.status}).`,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export function download(
  name: string,
  content: string,
  type = "application/json",
) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function date(value: string) {
  return value
    ? new Intl.DateTimeFormat("de-CH", {
        day: "numeric",
        month: "long",
        year: "numeric",
        timeZone: "Europe/Zurich",
      }).format(new Date(`${value}T12:00:00Z`))
    : "Eigenes Dokument";
}

export function report(job: import("./types").Job) {
  const result = job.result;
  return `FACT ATTACK · PRÜFBERICHT\n${job.mode === "demo" ? "DEMONSTRATION · Kein Modellurteil\n" : ""}\nBehauptung: ${job.claim}\nErstellt: ${new Date(job.created_at).toLocaleString("de-CH")}\nStatus: ${job.status}\nErgebnis: ${result ? resultView(result).title : "Kein Urteil"}\n\n${(result ? readableReasoning(result.reasoning) || resultView(result).text : job.error) ?? ""}\n${result?.note ?? ""}\n\nQuelle: ${result?.vote?.source_id ?? job.source_id ?? "Keine"}\n\n${result?.evidence.map((e, i) => `[${i + 1}] PDF-Seite ${e.page}\n${e.quote}`).join("\n\n") ?? ""}\n\nDie Prüfung beschreibt die Beziehung zum geprüften Dokument. Neutral bedeutet nicht falsch.\n`;
}
