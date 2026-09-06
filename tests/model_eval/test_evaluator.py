from __future__ import annotations

from copy import deepcopy

from .cases import SCENARIOS
from .evaluator import evaluate


CASE = next(case for case in SCENARIOS if case.id == "one-revenue")


def valid_envelope() -> dict:
    return {
        "ok": True,
        "turn": {
            "reply_text": "Выручка Транслориума за 2025 год составляет 806,3 млн ₽.",
            "brief": [],
            "answer_kind": "qa",
            "primary_inn": "9721159668",
            "facts": [],
            "claims": [
                {
                    "text_span": "806,3 млн ₽",
                    "inn": "9721159668",
                    "field": "finReports[year=2025].common.proceeds",
                    "signal_id": "FIN_PROCEEDS_UP",
                }
            ],
            "gaps": [],
            "next_steps": [],
            "headline": None,
            "panel": {"kind": "card", "inn": "9721159668", "block": "B", "metric": None},
            "suggestions": [],
            "_latency_s": 1.0,
        },
        "validation": {"issues": []},
    }


def codes(envelope: dict) -> set[str]:
    return {issue.code for issue in evaluate(envelope, CASE)}


def test_accepts_grounded_answer() -> None:
    assert evaluate(valid_envelope(), CASE) == []


def test_detects_hallucinated_number() -> None:
    envelope = valid_envelope()
    envelope["turn"]["reply_text"] = "Выручка за 2025 год составляет 999,9 млн ₽."
    assert "numbers" in codes(envelope)


def test_detects_verdict() -> None:
    envelope = valid_envelope()
    envelope["turn"]["reply_text"] += " Компания однозначно надёжна."
    assert "verdict" in codes(envelope)


def test_allows_reliability_noun_without_verdict() -> None:
    envelope = valid_envelope()
    envelope["turn"]["reply_text"] += " Данных недостаточно для оценки надёжности."
    assert "verdict" not in codes(envelope)


def test_detects_invalid_claim() -> None:
    envelope = valid_envelope()
    envelope["turn"]["claims"][0]["field"] = "finReports[year=2030].common.proceeds"
    assert {"claim_field", "missing_claim"} <= codes(envelope)


def test_detects_missing_claim_span() -> None:
    envelope = valid_envelope()
    envelope["turn"]["claims"][0]["text_span"] = "569,2 млн ₽"
    assert "claim_span" in codes(envelope)


def test_detects_schema_extension() -> None:
    envelope = deepcopy(valid_envelope())
    envelope["turn"]["unexpected"] = True
    assert "schema" in codes(envelope)
