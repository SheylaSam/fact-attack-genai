"""Transport-level fault injection for the evaluation cases with `fault` (`testset.FAULTS`).

    transport = faulty_transport(httpx.HTTPTransport(), "timeout")
    client = budget_http_client(budget, transport=transport)

The faulty transport is the innermost one, beneath `BudgetTransport`: every request is still reserved on the budget
and written to its journal before it fails, so a faulted run costs and counts what a real one would. No request ever
reaches the inner transport, so nothing is sent to the endpoint. A fault ends the run as `technischer_fehler`
(the agent's decide node turns the client's exception into one), never as a verdict.
"""
from typing import Literal

import httpx

Fault = Literal["timeout", "http_500"]
FAULTS = ("timeout", "http_500")  # the same names as `testset.FAULTS`


class _FaultyTransport(httpx.BaseTransport):
    def __init__(self, inner: httpx.BaseTransport, fault: str):
        self._inner = inner
        self._fault = fault

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self._fault == "timeout":  # what the real client sees when the endpoint stays silent
            raise httpx.ReadTimeout("Zeitüberschreitung (injizierter Fehler)", request=request)
        return httpx.Response(500, json={"error": {"message": "Interner Serverfehler (injizierter Fehler)"}},
                              request=request)

    def close(self) -> None:
        self._inner.close()


def faulty_transport(inner: httpx.BaseTransport, fault: Fault) -> httpx.BaseTransport:
    """A transport on which every request fails as `fault` says: "timeout" raises `httpx.ReadTimeout`, "http_500"
    answers with status 500. `inner` is never asked to send; it is only closed with the transport. Raises ValueError
    for any other fault."""
    if fault not in FAULTS:
        raise ValueError(f"Unbekannter Fehler {fault!r}. Erlaubt: {', '.join(FAULTS)}.")
    return _FaultyTransport(inner, fault)
