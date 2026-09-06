from __future__ import annotations

import json
from typing import Any

import pytest

from .cases import SCENARIOS, Scenario
from .evaluator import evaluate, text_of


@pytest.mark.llm_eval
@pytest.mark.parametrize("case", SCENARIOS, ids=lambda case: case.id)
def test_local_qwen_answer_quality(
    case: Scenario,
    eval_profile: str,
    agent_client: Any,
    result_sink: list[dict[str, Any]],
) -> None:
    if case.tier == "full" and eval_profile != "full":
        pytest.skip("Full-profile scenario")

    payload = {
        "message": case.message,
        "analyze_results": case.analyze(),
        "primary_inn": case.primary,
        "session_len": len(case.inns),
    }
    try:
        envelope, elapsed = agent_client.call(payload)
    except Exception as error:
        result_sink.append(
            {
                "id": case.id,
                "message": case.message,
                "inns": list(case.inns),
                "elapsed_s": None,
                "answer": "",
                "turn": None,
                "issues": [{"code": "request", "message": str(error)}],
            }
        )
        pytest.fail(f"Ошибка запроса к модели: {error}")

    issues = evaluate(envelope, case)
    turn = envelope.get("turn") if isinstance(envelope, dict) else None
    result_sink.append(
        {
            "id": case.id,
            "message": case.message,
            "inns": list(case.inns),
            "elapsed_s": elapsed,
            "answer": text_of(turn or {}, suggestions=True),
            "turn": turn,
            "validation": envelope.get("validation"),
            "issues": [issue.as_dict() for issue in issues],
        }
    )
    assert not issues, (
        "\n".join(f"[{issue.code}] {issue.message}" for issue in issues)
        + "\n\nОтвет модели:\n"
        + json.dumps(turn, ensure_ascii=False, indent=2)
    )
