import { describe, expect, it } from "vitest";
import {
  evidenceStance,
  readableReasoning,
  resultView,
  splitFocus,
} from "./presentation";
import type { Result } from "./types";

const base: Result = {
  outcome: "nicht_belegt",
  reasoning: "",
  note: null,
  error: null,
  vote: null,
  evidence: [],
  statements: [],
  question: null,
  evidence_validated: true,
  checks: 1,
  metrics: { input_tokens: 0, output_tokens: 0, inference_ms: 0 },
};

describe("Readable evidence results", () => {
  it("hides the ids-mode JSON fallback from the user's screenshot", () => {
    expect(
      readableReasoning(
        'Apertus answer: {"p_entail":0,"p_neutral":1,"p_contra":0,"label":1,"evidence_ids":[]}',
      ),
    ).toBe("");
  });
  it("keeps an actual explanation after JSON and removes markdown markers", () => {
    const raw =
      'Apertus answer: {"label":1,"evidence_ids":[]}\n\n**Reasoning:**\nThe document does **not contain any statement** about this claim.';
    expect(readableReasoning(raw)).toBe(
      "The document does not contain any statement about this claim.",
    );
  });
  it("extracts the explanation even with quoted braces inside the JSON", () => {
    const payload = {
      label: 0,
      reasoning: 'Der Wortlaut lautet "{Artikel 1}".',
    };
    expect(
      readableReasoning(`Apertus answer: ${JSON.stringify(payload)}`),
    ).toBe(payload.reasoning);
  });
  it.each([
    'Apertus answer: {"label":1,"evidence_ids":[',
    '```json\n{"label": 1}\n```',
    "Apertus answer: 1",
    "Parsed via regex fallback",
  ])("does not display technical fallback %s", (raw) => {
    expect(readableReasoning(raw)).toBe("");
  });
  it("leaves ordinary explanations intact", () => {
    expect(readableReasoning("Der Bundesrat empfiehlt die Annahme.")).toBe(
      "Der Bundesrat empfiehlt die Annahme.",
    );
  });
  it("keeps NLI neutral yellow and contradictory evidence red", () => {
    expect(resultView(base)).toMatchObject({
      title: "Neutral",
      tone: "neutral",
    });
    expect(resultView({ ...base, outcome: "widersprochen" })).toMatchObject({
      title: "Widerspruch",
      tone: "negative",
    });
  });
  it("only marks supported claims green when original evidence is available", () => {
    expect(
      resultView({
        ...base,
        outcome: "gestuetzt",
        evidence: [{ page: 2, quote: "Originalbeleg" }],
      }).tone,
    ).toBe("positive");
    // Without a valid original quote nothing is contradicted: "Nicht belegt", neutral, never "Widerspruch".
    expect(resultView({ ...base, outcome: "gestuetzt" })).toMatchObject({
      title: "Nicht belegt",
      tone: "neutral",
    });
    expect(
      resultView({ ...base, note: "Urteil ohne prüfbaren Beleg verworfen" })
        .tone,
    ).toBe("neutral");
  });
});

describe("Evidence passages", () => {
  it("marks only the key sentence of a passage", () => {
    const quote = "Erster Satz. Das kostet 4 Milliarden Franken. Letzter Satz.";
    expect(splitFocus(quote, "Das kostet 4 Milliarden Franken.")).toEqual({
      before: "Erster Satz. ",
      focus: "Das kostet 4 Milliarden Franken.",
      after: " Letzter Satz.",
    });
    expect(splitFocus(quote, "Nicht im Zitat.")).toBeNull();
    expect(splitFocus(quote, null)).toBeNull();
  });

  it("labels each passage, falling back to the verdict for older results", () => {
    const positive = resultView({
      ...base,
      outcome: "gestuetzt",
      evidence: [{ page: 1, quote: "x" }],
    });
    expect(
      evidenceStance({ page: 1, quote: "x", stance: "neutral" }, positive)
        .label,
    ).toBe("Neutral");
    expect(
      evidenceStance({ page: 1, quote: "x", stance: "contradicts" }, positive)
        .label,
    ).toBe("Widersprochen");
    expect(evidenceStance({ page: 1, quote: "x" }, positive).label).toBe(
      "Belegt",
    );
  });
});
