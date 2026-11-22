import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import type { Job } from "./types";

const source = {
  id: "2026-06-14_de",
  name: "2026-06-14_de.pdf",
  kind: "official",
  date: "2026-06-14",
  language: "de",
  votes: ["Zivildienstgesetz"],
  url: "https://www.admin.ch/",
};
const status = {
  api_configured: false,
  auto_available: false,
  model: "Apertus",
  max_claim_chars: 1000,
  max_requests: 12,
  max_upload_mb: 20,
};
const completed: Job = {
  id: "demo-check",
  claim: "Eine konkrete Behauptung.",
  source_id: source.id,
  language: "de",
  mode: "demo",
  max_requests: 12,
  created_at: "2026-11-22T12:00:00Z",
  status: "completed",
  events: [],
  pending: null,
  error: null,
  requests_used: 0,
  elapsed_ms: 1000,
  result: {
    outcome: "nicht_belegt",
    reasoning: "Keine passende Textstelle gefunden.",
    note: null,
    error: null,
    vote: null,
    evidence: [],
    statements: [],
    question: null,
    evidence_validated: true,
    checks: 1,
    metrics: { input_tokens: 0, output_tokens: 0, inference_ms: 0 },
  },
};
let fetcher: Mock<(path: string, options?: RequestInit) => Promise<Response>>;

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  history.replaceState(null, "", "/");
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
  fetcher = vi.fn(async (path: string, options?: RequestInit) => {
    if (path === "/api/status") return Response.json(status);
    if (path === "/api/sources") return Response.json([source]);
    if (path === "/api/checks" && options?.method === "POST")
      return Response.json(completed, { status: 202 });
    if (path === "/api/checks") return Response.json([]);
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.stubGlobal("fetch", fetcher);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Agent workspace", () => {
  it("highlights validated excerpts and opens their marked original page", async () => {
    const user = userEvent.setup();
    const stored: Job = {
      ...completed,
      result: {
        ...completed.result!,
        outcome: "gestuetzt",
        evidence: [{ page: 2, quote: "Der Bundesrat empfiehlt die Annahme." }],
      },
    };
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation(async (path, options) => {
      if (path === "/api/checks" && !options?.method)
        return Response.json([stored]);
      if (path === `/api/checks/${stored.id}/evidence/0/preview`)
        return Response.json({
          page: 2,
          highlighted: true,
          reason:
            "Markiert ist der angezeigte Belegauszug auf der Originalseite.",
          image: "data:image/png;base64,iVBORw0KGgo=",
        });
      return original(path, options);
    });
    history.replaceState(null, "", `/#${stored.id}`);
    render(<App />);
    await screen.findByRole("heading", { name: "Belegt", level: 2 });
    expect(
      document.querySelector("blockquote mark.evidence-highlight")?.textContent,
    ).toBe(stored.result!.evidence[0].quote);
    await user.click(
      screen.getByRole("button", { name: "Markierte Originalstelle öffnen" }),
    );
    expect(
      await screen.findByRole("img", {
        name: "Originalseite 2 mit gelb markierter Belegstelle",
      }),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "Markiert ist der angezeigte Belegauszug auf der Originalseite.",
      ),
    ).toBeTruthy();
    expect(
      fetcher.mock.calls.filter(([, options]) => options?.method === "POST"),
    ).toHaveLength(0);
  });

  it.each([
    ["gestuetzt", "Belegt", "positive"],
    ["nicht_belegt", "Neutral", "neutral"],
    ["widersprochen", "Widerspruch", "negative"],
  ])(
    "renders %s as a readable verdict instead of raw JSON",
    async (outcome, title, tone) => {
      const stored: Job = {
        ...completed,
        result: {
          ...completed.result!,
          outcome,
          reasoning:
            'Apertus answer: {"p_entail":0,"p_neutral":1,"p_contra":0,"label":1,"evidence_ids":[]}',
          evidence:
            outcome === "nicht_belegt"
              ? []
              : [
                  {
                    page: 2,
                    quote: "Eine Originaltextstelle aus dem Dokument.",
                  },
                ],
        },
      };
      const original = fetcher.getMockImplementation()!;
      fetcher.mockImplementation(async (path: string, options?: RequestInit) =>
        path === "/api/checks" && !options?.method
          ? Response.json([stored])
          : original(path, options),
      );
      history.replaceState(null, "", `/#${stored.id}`);
      render(<App />);
      await screen.findByRole("heading", { name: title, level: 2 });
      expect(
        document.querySelector(".answer-card")?.getAttribute("data-verdict"),
      ).toBe(tone);
      expect(document.body.textContent).not.toContain("Apertus answer:");
      expect(document.body.textContent).not.toContain("p_neutral");
      expect(
        document.querySelector(".scale-item.is-selected")?.textContent,
      ).toBe(title);
    },
  );

  it("requires explicit demo selection when no API key is configured and submits only once", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Lokal verbunden");
    const submit = screen.getByRole("button", {
      name: "Behauptung prüfen",
    }) as HTMLButtonElement;
    await user.type(
      screen.getByLabelText("Behauptung eingeben"),
      "Eine konkrete Behauptung.",
    );
    expect(submit.disabled).toBe(true);
    await user.click(screen.getByRole("button", { name: /^Demo$/ }));
    expect(submit.disabled).toBe(false);
    await user.click(submit);
    await screen.findByText("Neutral", { selector: "h2" });
    expect(screen.getByText("Demonstration ohne Modellanfrage.")).toBeTruthy();
    const posts = fetcher.mock.calls.filter(
      ([, options]) => options?.method === "POST",
    );
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0][1]?.body as string)).toMatchObject({
      claim: completed.claim,
      mode: "demo",
      source_id: source.id,
    });
  });

  it("opens the source picker from the composer without submitting a check", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Lokal verbunden");
    await user.click(screen.getByRole("button", { name: /^Demo$/ }));
    await user.type(
      screen.getByLabelText("Behauptung eingeben"),
      "Eine Behauptung.",
    );
    await user.click(
      screen.getByRole("button", { name: "Quellenbibliothek öffnen" }),
    );
    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(
      fetcher.mock.calls.filter(([, options]) => options?.method === "POST"),
    ).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Als Quelle wählen" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("keeps a saved draft and never renders user text as HTML", async () => {
    sessionStorage.setItem("fact-attack-draft", "<img src=x onerror=alert(1)>");
    render(<App />);
    await screen.findByText("Lokal verbunden");
    expect(
      (screen.getByLabelText("Behauptung eingeben") as HTMLTextAreaElement)
        .value,
    ).toContain("<img");
    expect(document.querySelector("img")).toBeNull();
  });
});
