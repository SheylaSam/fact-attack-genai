"""Deciders: turn the conversation so far into the next tool action.

Three variants behind one `Decider` protocol:
* `NativeToolDecider`: the chat model's own tool calling (`bind_tools`); the first tool call is the action.
* `JsonActionDecider`: the model writes `{"tool": ..., "args": {...}}` as text; the tool list and the format are
  appended to the system message. Thinking output (`<|inner_prefix|>...<|inner_suffix|>answer`) is cut off first.
* `ScriptedDecider`: a deterministic policy over the last observation, for tests and the offline mode. No network.

`messages` are OpenAI chat messages; the claim is the content of the first user message and never part of the system
message. An action is validated against the offered tools: an unknown tool or invalid arguments give
`AgentAction(tool=None, error=<German message>)`, which the graph counts as a step and feeds back to the model.
Exceptions of the chat model (timeouts, a spent request budget) are not caught here.

`make_chat_model` is the only place that builds the LLM client: sync only, one HTTP request per call, counted on the
check's `RequestBudget`.
"""
import datetime
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, Tuple

import httpx

from src import config
from src.agent import settings
from src.agent.budget import RequestBudget, budget_http_client
from src.agent.tools import MAX_QUERY_CHARS, Observation, validate_args
from src.apertus_client import ApertusClient

if TYPE_CHECKING:  # langchain-openai is optional (requirements-agent.txt); make_chat_model imports it when called
    from langchain_openai import ChatOpenAI

MODES = ("native", "json", "scripted")
DEFAULT_MARGIN = 0.02  # top-1 must lead top-2 by at least this much, or the scripted policy asks
RAW_CHARS = 2000  # cap on a model answer that parsed to nothing; it goes into the history again
TITLE_CHARS = 90  # a vote title inside the scripted question
CONNECT_TIMEOUT_S = 10.0
FORMAT_INSTRUCTION = (
    'Antworte ausschließlich mit genau einem JSON-Objekt der Form {"tool": "<Werkzeugname>", "args": {<Argumente>}}. '
    "Schreibe keinen weiteren Text und kein Markdown. Die Argumente müssen genau den Parametern des gewählten "
    "Werkzeugs entsprechen."
)


@dataclass
class AgentAction:
    """The next step. `tool` is None when the model's answer was no usable action; `error` then says why."""
    tool: Optional[str] = None
    args: dict = field(default_factory=dict)
    raw: str = ""  # what the model wrote (JSON text), for the history and the trace
    call_id: Optional[str] = None  # id of a native tool call
    tokens_prompt: int = 0
    tokens_completion: int = 0
    error: Optional[str] = None


class Decider(Protocol):
    def decide(self, messages: List[dict], tools: List[dict], history: List[Observation]) -> AgentAction:
        """The next action. `history` holds the observations so far; the native and JSON deciders ignore it."""

    def observation_messages(self, action: AgentAction, observation: Observation) -> List[dict]:
        """The messages that record `action` and its `observation` in the conversation."""


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # content blocks
        return "".join(part if isinstance(part, str) else str(part.get("text", "")) for part in content
                       if isinstance(part, (str, dict)))
    return "" if content is None else str(content)


def _usage(reply: Any) -> Tuple[int, int]:
    usage = getattr(reply, "usage_metadata", None) or {}
    return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


def _action_json(tool: Any, args: Any) -> str:
    return json.dumps({"tool": tool, "args": args}, ensure_ascii=False, default=str)


def _quoted(value: Any) -> str:
    text = value if isinstance(value, str) else repr(value)
    return repr(text[:60] + ("…" if len(text) > 60 else ""))


def _offered(tools: List[dict]) -> List[str]:
    return [spec["name"] for spec in tools]


def _checked(tool: Any, args: Any, tools: List[dict]) -> Tuple[Optional[dict], Optional[str]]:
    """(validated args, None) for an offered tool with valid arguments, else (None, German error)."""
    offered = _offered(tools)
    if not isinstance(tool, str) or tool not in offered:
        return None, f"Unbekanntes Werkzeug {_quoted(tool)}. Verfügbar: {', '.join(offered)}."
    try:
        return validate_args(tool, args), None
    except ValueError as exc:
        return None, str(exc)


def _text_exchange(action: AgentAction, observation: Observation) -> List[dict]:
    """The action as the assistant's text and the observation as the next user message (JSON and scripted)."""
    written = action.raw or (_action_json(action.tool, action.args) if action.tool else "(keine Antwort)")
    return [{"role": "assistant", "content": written},
            {"role": "user", "content": f"Ergebnis von {observation.tool}: {observation.text}"}]


class NativeToolDecider:
    """Native tool calling. The first tool call becomes the action; further calls in the same answer are ignored
    (noted in `raw`), so the history stays valid: one tool call, one tool message."""

    def __init__(self, chat_model: Any):
        self.chat_model = chat_model

    def decide(self, messages: List[dict], tools: List[dict], history: List[Observation]) -> AgentAction:
        reply = self.chat_model.bind_tools(tools).invoke(messages)
        tokens_prompt, tokens_completion = _usage(reply)
        usage = {"tokens_prompt": tokens_prompt, "tokens_completion": tokens_completion}
        calls = list(getattr(reply, "tool_calls", None) or [])
        if not calls:
            error = "Keine Werkzeugwahl"
            broken = getattr(reply, "invalid_tool_calls", None) or []
            if broken:  # the model did call a tool, but its arguments were not JSON
                error += f": Die Argumente von {broken[0].get('name')} sind kein gültiges JSON."
            return AgentAction(raw=_text(reply.content), error=error, **usage)
        call = calls[0]
        raw = _action_json(call.get("name"), call.get("args"))
        if len(calls) > 1:
            raw += f"\n[{len(calls)} Werkzeugaufrufe in einer Antwort, nur der erste wird ausgeführt]"
        args, error = _checked(call.get("name"), call.get("args"), tools)
        if error:
            return AgentAction(raw=raw, error=error, **usage)
        return AgentAction(call["name"], args, raw, call.get("id") or None, **usage)

    def observation_messages(self, action: AgentAction, observation: Observation) -> List[dict]:
        if action.tool is None or action.call_id is None:  # no tool call to answer: plain text instead
            return _text_exchange(action, observation)
        call = {"id": action.call_id, "type": "function",
                "function": {"name": action.tool, "arguments": json.dumps(action.args, ensure_ascii=False)}}
        return [{"role": "assistant", "content": "", "tool_calls": [call]},
                {"role": "tool", "tool_call_id": action.call_id, "content": observation.text}]


def format_instruction(tools: List[dict]) -> str:
    """The German text that tells the model to answer with a JSON action, with the offered tools and their schemas."""
    lines = [FORMAT_INSTRUCTION, "", "Verfügbare Werkzeuge:"]
    for spec in tools:
        schema = json.dumps(spec["parameters"], ensure_ascii=False, separators=(",", ":"))
        lines += [f"- {spec['name']}: {spec['description']}", f"  Parameter (JSON-Schema): {schema}"]
    return "\n".join(lines)


def _last_action_object(text: str) -> Optional[Tuple[dict, str]]:
    """The last top-level JSON object of `text` that has a "tool" key, with its source text."""
    decoder = json.JSONDecoder(strict=False)
    found = None
    position = text.find("{")
    while position != -1:
        try:
            value, end = decoder.raw_decode(text, position)
        except ValueError:
            position = text.find("{", position + 1)
            continue
        if isinstance(value, dict) and "tool" in value:
            found = (value, text[position:end])
            position = text.find("{", end)  # objects inside it belong to it
        else:
            position = text.find("{", position + 1)
    return found


def parse_json_action(text: str, tools: List[dict]) -> AgentAction:
    """The action a model wrote as text: reasoning cut off, last JSON object with "tool" taken (also from a code
    fence or between prose), tool name and arguments validated against `tools`."""
    answer = ApertusClient._strip_thinking(text or "").strip()
    found = _last_action_object(answer)
    if found is None:
        return AgentAction(raw=answer[:RAW_CHARS], error='Keine Werkzeugwahl: Die Antwort enthält kein JSON-Objekt '
                                                         'der Form {"tool": "...", "args": {...}}.')
    value, raw = found
    args, error = _checked(value["tool"], value.get("args", {}), tools)
    if error:
        return AgentAction(raw=raw, error=error)
    return AgentAction(value["tool"], args, raw)


class JsonActionDecider:
    """The model answers with `{"tool": ..., "args": {...}}` as text; no native tool calling needed."""

    def __init__(self, chat_model: Any):
        self.chat_model = chat_model

    def decide(self, messages: List[dict], tools: List[dict], history: List[Observation]) -> AgentAction:
        reply = self.chat_model.invoke(self._with_format(messages, tools))
        action = parse_json_action(_text(reply.content), tools)
        action.tokens_prompt, action.tokens_completion = _usage(reply)
        return action

    @staticmethod
    def _with_format(messages: List[dict], tools: List[dict]) -> List[dict]:
        instruction = format_instruction(tools)
        if messages and messages[0].get("role") == "system":
            return [{**messages[0], "content": f"{_text(messages[0].get('content'))}\n\n{instruction}"}, *messages[1:]]
        return [{"role": "system", "content": instruction}, *messages]

    def observation_messages(self, action: AgentAction, observation: Observation) -> List[dict]:
        return _text_exchange(action, observation)


def _claim(messages: List[dict]) -> Optional[str]:
    for message in messages:
        if message.get("role") == "user":
            return _text(message.get("content")).strip() or None
    return None


def _german_date(iso_date: Any) -> str:
    try:
        return datetime.date.fromisoformat(str(iso_date)).strftime("%d.%m.%Y")
    except ValueError:
        return str(iso_date)


def _short(title: Any) -> str:
    title = " ".join(str(title).split())
    return title if len(title) <= TITLE_CHARS else title[:TITLE_CHARS].rstrip() + "…"


class ScriptedDecider:
    """Deterministic policy, no network: find the vote, ask when the two best votes are too close to call, search the
    best vote's passages, check. Reads only the last observation (`history[-1].data`) and the claim.

    With `fixed_vote_id` the vote is given: the policy goes straight to `suche_passagen(fixed vote, claim)`.
    """

    def __init__(self, margin: float = DEFAULT_MARGIN, fixed_vote_id: Optional[str] = None):
        self.margin = margin
        self.fixed_vote_id = fixed_vote_id

    def decide(self, messages: List[dict], tools: List[dict], history: List[Observation]) -> AgentAction:
        claim = _claim(messages)
        if claim is None:
            return AgentAction(error="Keine Nutzernachricht mit der Behauptung gefunden.")
        query = claim[:MAX_QUERY_CHARS].strip()
        offered = _offered(tools)
        if not history:
            if self.fixed_vote_id is not None:
                return self._act("suche_passagen", {"vorlage_id": self.fixed_vote_id, "anfrage": query}, offered)
            return self._act("suche_vorlagen", {"anfrage": query}, offered)
        last = history[-1]
        if last.error:
            return AgentAction(error=f"Das Werkzeug {last.tool} ist fehlgeschlagen: {last.error}")
        if last.tool == "suche_vorlagen":
            return self._after_vote_search(last.data, query, offered)
        if last.tool in ("suche_passagen", "suche_passagen_vektor"):
            return self._after_passage_search(last.data, offered)
        return AgentAction(error=f"Keine Regel für das Ergebnis von {last.tool}.")

    def _after_vote_search(self, data: dict, query: str, offered: List[str]) -> AgentAction:
        candidates = data.get("candidates") or []
        if not candidates:
            return self._act("quelle_fehlt", {"begruendung": "Keine Vorlage der Büchlein passt zur Behauptung."},
                             offered)
        top = candidates[0]
        if len(candidates) > 1 and top["score"] - candidates[1]["score"] < self.margin:
            first, second = candidates[:2]
            question = (f"Welche Vorlage ist gemeint: «{_short(first['title'])}» ({_german_date(first['date'])}) "
                        f"oder «{_short(second['title'])}» ({_german_date(second['date'])})?")
            return self._act("rueckfrage", {"frage": question, "optionen": [first["vote_id"], second["vote_id"]]},
                             offered)
        return self._act("suche_passagen", {"vorlage_id": top["vote_id"], "anfrage": query}, offered)

    def _after_passage_search(self, data: dict, offered: List[str]) -> AgentAction:
        if not data.get("pool_size"):
            return self._act("quelle_fehlt", {"begruendung": "In der gewählten Vorlage wurden keine Passagen zur "
                                                             "Behauptung gefunden."}, offered)
        return self._act("pruefen", {"vorlage_id": data["vote_id"]}, offered)

    @staticmethod
    def _act(tool: str, args: Dict[str, Any], offered: List[str]) -> AgentAction:
        if tool not in offered:
            return AgentAction(error=f"Das Werkzeug {tool} ist nicht verfügbar. Verfügbar: {', '.join(offered)}.")
        try:
            clean = validate_args(tool, args)
        except ValueError as exc:
            return AgentAction(error=str(exc))
        return AgentAction(tool, clean, _action_json(tool, clean))

    def observation_messages(self, action: AgentAction, observation: Observation) -> List[dict]:
        return _text_exchange(action, observation)


class _SyncOnlyTransport(httpx.AsyncBaseTransport):
    """Async transport that sends nothing: the budget counts only the sync client, so `ainvoke` must fail closed."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise RuntimeError("Der Agent sendet nur synchrone Anfragen (invoke); der asynchrone Weg würde das "
                           "Anfrage-Budget umgehen.")


def make_chat_model(budget: RequestBudget) -> "ChatOpenAI":
    """The agent's LLM client: every HTTP request is counted on `budget` and none is retried by the SDK.

    `timeout` is passed on because the SDK hands its own (by default: no) timeout to every request, which would
    override the one of the httpx client. An unreachable host fails after `CONNECT_TIMEOUT_S`, a silent one after
    `settings.LLM_TIMEOUT_S`.
    """
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=config.LLM_NAME, base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY, temperature=0,
                      max_retries=0, timeout=httpx.Timeout(settings.LLM_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
                      http_client=budget_http_client(budget),
                      http_async_client=httpx.AsyncClient(transport=_SyncOnlyTransport()))


def make_decider(mode: str, budget: RequestBudget, *, fixed_vote_id: Optional[str] = None) -> Decider:
    """`"native"` | `"json"` | `"scripted"`. The scripted decider needs no chat model and sends nothing."""
    if mode == "scripted":
        return ScriptedDecider(fixed_vote_id=fixed_vote_id)
    if mode == "native":
        return NativeToolDecider(make_chat_model(budget))
    if mode == "json":
        return JsonActionDecider(make_chat_model(budget))
    raise ValueError(f"Unbekannter Entscheider-Modus {mode!r}. Erlaubt: {', '.join(MODES)}.")
