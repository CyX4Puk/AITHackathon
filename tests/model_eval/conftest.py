from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest


ROOT = Path(__file__).resolve().parents[2]


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("local LLM evaluations")
    group.addoption("--run-llm-evals", action="store_true", help="Call the local language model")
    group.addoption("--eval-profile", choices=("smoke", "full"), default=os.getenv("LLM_EVAL_PROFILE", "smoke"))
    group.addoption("--agent-url", default=os.getenv("LLM_EVAL_AGENT_URL", "http://localhost:8130"))
    group.addoption("--lm-url", default=os.getenv("LM_URL", "http://localhost:1234/v1/chat/completions"))
    group.addoption("--lm-model", default=os.getenv("LM_MODEL", "qwen/qwen3.8-27b"))
    group.addoption("--llm-timeout", type=float, default=float(os.getenv("LLM_EVAL_TIMEOUT", "900")))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-llm-evals"):
        return
    skip = pytest.mark.skip(reason="Use --run-llm-evals to call the local model")
    for item in items:
        if "llm_eval" in item.keywords:
            item.add_marker(skip)


def request_json(url: str, payload: dict[str, Any] | None = None, timeout: float = 5) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def models_url(chat_url: str) -> str:
    parsed = urlsplit(chat_url)
    path = parsed.path
    path = path[: -len("/chat/completions")] + "/models" if path.endswith("/chat/completions") else path.rstrip("/") + "/models"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def health(base_url: str) -> dict[str, Any] | None:
    try:
        return request_json(base_url.rstrip("/") + "/health", timeout=2)
    except Exception:
        return None


@dataclass
class AgentClient:
    url: str
    timeout: float

    def call(self, payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
        started = time.monotonic()
        response = request_json(self.url.rstrip("/") + "/chat", payload, self.timeout)
        return response, round(time.monotonic() - started, 3)


@pytest.fixture(scope="session")
def eval_profile(pytestconfig: pytest.Config) -> str:
    return str(pytestconfig.getoption("--eval-profile"))


@pytest.fixture(scope="session")
def agent_client(pytestconfig: pytest.Config, tmp_path_factory: pytest.TempPathFactory) -> AgentClient:
    agent_url = str(pytestconfig.getoption("--agent-url")).rstrip("/")
    lm_url = str(pytestconfig.getoption("--lm-url"))
    model = str(pytestconfig.getoption("--lm-model"))
    timeout = float(pytestconfig.getoption("--llm-timeout"))

    try:
        models = request_json(models_url(lm_url))
    except Exception as error:
        pytest.fail(f"LM Studio недоступна по {models_url(lm_url)}: {error}")
    available = {item.get("id") for item in models.get("data", []) if isinstance(item, dict)}
    if available and model not in available:
        pytest.fail(f"Модель {model!r} не загружена. Доступны: {sorted(available)}")

    process: subprocess.Popen[str] | None = None
    log_handle = None
    state = health(agent_url)
    if state is None:
        parsed = urlsplit(agent_url)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            pytest.fail(f"Агент недоступен по {agent_url}")
        log_path = tmp_path_factory.mktemp("llm-agent") / "web-agent.log"
        log_handle = log_path.open("w", encoding="utf-8")
        env = {**os.environ, "PORT": str(parsed.port or 8130), "LM_URL": lm_url, "LM_MODEL": model}
        script = ROOT / "services/root_agent/src/web_agent.py"
        process = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=ROOT,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(50):
            state = health(agent_url)
            if state is not None or process.poll() is not None:
                break
            time.sleep(0.1)
        if state is None:
            if process.poll() is None:
                process.terminate()
            pytest.fail(f"Не удалось запустить web_agent.py. Лог: {log_path}")

    if state and state.get("model") not in (None, model):
        pytest.fail(f"На {agent_url} запущена другая модель: {state.get('model')!r}")

    try:
        yield AgentClient(agent_url, timeout)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()
        if log_handle is not None:
            log_handle.close()


@pytest.fixture(scope="session")
def result_sink(pytestconfig: pytest.Config, request: pytest.FixtureRequest) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    yield records
    directory = ROOT / ".artifacts/llm-eval"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"report-{datetime.now():%Y%m%d-%H%M%S}.json"
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "profile": pytestconfig.getoption("--eval-profile"),
        "model": pytestconfig.getoption("--lm-model"),
        "summary": {
            "total": len(records),
            "passed": sum(not item["issues"] for item in records),
            "failed": sum(bool(item["issues"]) for item in records),
        },
        "results": records,
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter:
        reporter.write_line(f"LLM evaluation report: {path}")
