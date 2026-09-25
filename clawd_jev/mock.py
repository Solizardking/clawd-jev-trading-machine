"""Deterministic mock decision engine.

Used ONLY when the user passes --dry-run-mock. Every result is labeled
mock=True and model="mock" and must never be presented as real JEV output.
The mock always chooses WAIT: it exercises the loop, mapping, simulator,
and persistence without inventing any trading signal.
"""

MOCK_MODEL = "mock"


def mock_ask(state, questions, **kwargs) -> dict:
    """Same return shape as typesafe.ask, but with honest mock answers."""
    answers = {}
    for qid, q in (questions or {}).items():
        qtype = (q or {}).get("type")
        if qtype == "choice":
            criteria = q.get("criteria", {})
            choice = "WAIT" if "WAIT" in criteria else next(iter(criteria), "WAIT")
            answers[qid] = {
                "type": "choice",
                "choice": choice,
                "confidence": 1.0,
                "probabilities": {choice: 1.0},
                "mock": True,
            }
        elif qtype == "noul":
            answers[qid] = {"type": "noul", "noul": 0.0, "mock": True}
        elif qtype == "score":
            answers[qid] = {"type": "score", "score": 0, "confidence": 1.0, "mock": True}
        else:
            answers[qid] = {"type": qtype, "mock": True}
    return {"answers": answers, "model": MOCK_MODEL, "latency_ms": 0}
