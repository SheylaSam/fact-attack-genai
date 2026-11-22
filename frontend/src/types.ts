export type Mode = "live" | "demo";
export type Stance = "supports" | "neutral" | "contradicts";
export type EvidencePreview = {
  page: number;
  highlighted: boolean;
  reason: string;
  image: string;
};
export type PdfSelection = {
  id: string;
  page: number;
  evidence?: { checkId: string; index: number };
};
export type Source = {
  id: string;
  name: string;
  date: string;
  language: string;
  votes: string[];
  kind: "official" | "upload";
  url: string | null;
};
export type Status = {
  model: string;
  api_configured: boolean;
  auto_available: boolean;
  max_claim_chars: number;
  max_requests: number;
  max_upload_mb: number;
};
export type Vote = {
  vote_id: string;
  source_id: string;
  title: string;
  date: string;
  language: string;
};
export type Trace = {
  node: string;
  step: number;
  tool: string | null;
  summary: string;
  error: string | null;
  duration_ms: number;
  tokens_prompt: number;
  tokens_completion: number;
};
export type Pending = {
  question: string;
  options: (Vote & { passage?: { page: number; excerpt: string } | null })[];
};
export type Result = {
  outcome: string;
  reasoning: string;
  note: string | null;
  error: string | null;
  vote: Vote | null;
  evidence: {
    page: number;
    quote: string;
    focus?: string | null;
    stance?: Stance;
  }[];
  statements: string[];
  question: string | null;
  evidence_validated: boolean;
  checks: number;
  metrics: {
    input_tokens: number;
    output_tokens: number;
    inference_ms: number;
  };
};
export type Job = {
  id: string;
  claim: string;
  source_id: string | null;
  language: string;
  mode: Mode;
  max_requests: number;
  created_at: string;
  status:
    | "running"
    | "waiting"
    | "completed"
    | "failed"
    | "cancelled"
    | "interrupted";
  events: Trace[];
  result: Result | null;
  pending: Pending | null;
  error: string | null;
  requests_used: number;
  elapsed_ms: number;
};
