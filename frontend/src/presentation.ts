import type { Result, Stance } from "./types";

export type ResultView = {
  title: string;
  text: string;
  tone: "positive" | "neutral" | "negative" | "warning";
  evidenceText?: string;
};

const OUTCOMES: Record<string, ResultView> = {
  gestuetzt: {
    title: "Belegt",
    text: "Die Originalbelege stützen deine Behauptung.",
    tone: "positive",
    evidenceText: "Stützt die Aussage",
  },
  nicht_belegt: {
    title: "Neutral",
    text: "Die Quelle bestätigt oder widerlegt die Behauptung nicht eindeutig. Das bedeutet nicht, dass sie falsch ist.",
    tone: "neutral",
  },
  widersprochen: {
    title: "Widerspruch",
    text: "Die Originalbelege widersprechen deiner Behauptung.",
    tone: "negative",
    evidenceText: "Widerspricht der Aussage",
  },
  quelle_fehlt: {
    title: "Keine passende Quelle",
    text: "Wähle ein anderes Büchlein oder formuliere die Behauptung genauer. Es liegt noch kein Urteil vor.",
    tone: "warning",
  },
  rueckfrage: {
    title: "Lass uns die Aussage eingrenzen",
    text: "Welche Teilaussage möchtest du einzeln prüfen?",
    tone: "warning",
  },
  technischer_fehler: {
    title: "Die Prüfung wurde unterbrochen",
    text: "Es liegt kein sachliches Urteil vor. Du kannst die Prüfung erneut starten.",
    tone: "warning",
  },
};

export function resultView(result: Result): ResultView {
  const discarded = result.note?.includes(
    "Urteil ohne prüfbaren Beleg verworfen",
  );
  const unsupportedPositive =
    result.outcome === "gestuetzt" &&
    (!result.evidence_validated || result.evidence.length === 0);
  if (discarded || unsupportedPositive) {
    return {
      title: "Nicht belegt",
      text: "Es wurde kein gültiger Originalbeleg gefunden, der das Urteil absichert. Die Behauptung ist damit nicht als falsch bewertet.",
      tone: "neutral",
    };
  }
  return OUTCOMES[result.outcome] ?? OUTCOMES.technischer_fehler;
}

// In ids mode the legacy client puts its raw response in `reasoning` when no explanation
// is provided. Present only an actual explanation; never infer a verdict from this text.
export function readableReasoning(raw: string | null | undefined): string {
  let text = (raw ?? "").trim();
  if (!text) return "";
  const diagnostic = /^Apertus answer:\s*/i.test(text);
  text = text
    .replace(/^Apertus answer:\s*/i, "")
    .replace(/^```(?:json)?\s*/i, "");
  if (text.startsWith("{")) {
    let depth = 0,
      quoted = false,
      escaped = false,
      end = -1;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (quoted) {
        if (escaped) escaped = false;
        else if (c === "\\") escaped = true;
        else if (c === '"') quoted = false;
      } else if (c === '"') quoted = true;
      else if (c === "{") depth++;
      else if (c === "}" && --depth === 0) {
        end = i + 1;
        break;
      }
    }
    if (end < 0) return "";
    try {
      const parsed = JSON.parse(text.slice(0, end));
      if (typeof parsed.reasoning === "string") text = parsed.reasoning;
      else {
        const trailing = text
          .slice(end)
          .replace(/^\s*```\s*/, "")
          .trim();
        text =
          /^(?:\*\*)?(?:Reasoning|Begründung|Erklärung)(?:\*\*)?\s*:(?:\*\*)?/i.test(
            trailing,
          )
            ? trailing
            : "";
      }
    } catch {
      return "";
    }
  } else if (
    diagnostic &&
    /(?:p_entail|p_neutral|p_contra|evidence_ids|"label")/.test(text)
  ) {
    return "";
  }
  if (diagnostic && /^[012](?:[|,\s].*)?$/.test(text)) return "";
  if (
    /^(?:Parsed via regex fallback|Unparseable answer|Response Parse Error|API Error|Could not parse)/i.test(
      text,
    )
  )
    return "";
  return text
    .replace(
      /^(?:\*\*)?(?:Reasoning|Begründung|Erklärung)(?:\*\*)?\s*:(?:\*\*)?\s*/i,
      "",
    )
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*\n]+)\*/g, "$1")
    .trim();
}

export type EvidenceStanceView = {
  tone: "positive" | "neutral" | "negative";
  label: string;
  detail: string;
};

const STANCES: Record<Stance, EvidenceStanceView> = {
  supports: { tone: "positive", label: "Belegt", detail: "stützt die Aussage" },
  neutral: { tone: "neutral", label: "Neutral", detail: "kein direkter Bezug" },
  contradicts: {
    tone: "negative",
    label: "Widersprochen",
    detail: "widerspricht der Aussage",
  },
};

// The stance of one passage. Results stored before passages had their own stance take the verdict's direction.
export function evidenceStance(
  item: Result["evidence"][number],
  verdict: ResultView,
): EvidenceStanceView {
  if (item.stance && STANCES[item.stance]) return STANCES[item.stance];
  return STANCES[
    verdict.tone === "positive"
      ? "supports"
      : verdict.tone === "negative"
        ? "contradicts"
        : "neutral"
  ];
}

// The quote split around the sentence to mark; without a focus that occurs in the quote, nothing is split.
export function splitFocus(
  quote: string,
  focus: string | null | undefined,
): { before: string; focus: string; after: string } | null {
  if (!focus) return null;
  const start = quote.indexOf(focus);
  if (start < 0) return null;
  return {
    before: quote.slice(0, start),
    focus,
    after: quote.slice(start + focus.length),
  };
}
