from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .cases import MENTIONS, Scenario


@dataclass(frozen=True)
class Issue:
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


LEAK = re.compile(
    r"</?think>|\b(?:finReports|baseInfo|reputationalRisks|executionProceedings|"
    r"arbitrationByStatus|foundersInfo|zskRiskLevel|dealContext)\b|"
    r"\b(?:LOW|MEDIUM|HIGH|UNKNOWN|GREEN|YELLOW|RED|CURRENT|CLOSED)\b|"
    r"\b(?:BANK|FIN|REG|ARB|EP|STATUS|DEAL)_[A-Z_]+\b"
)
VERDICT = re.compile(
    r"\b(?:не\s+работать|не\s+рекоменду\w*\s+работать|отказаться\s+от\s+сделк\w*|"
    r"однозначн\w*|безопас(?:ен|на|но|ны)|безопасн(?:ый|ая|ое|ые|ой|ого)|"
    r"над[её]ж(?:ен|на|но|ны)|над[её]жн(?:ый|ая|ое|ые|ой|ого))\b",
    re.IGNORECASE,
)
RANKING = re.compile(
    r"\b(?:лучше|лучш(?:ий|ая|ее)|над[её]жнее|сам\w*\s+над[её]жн\w*|"
    r"перв\w*\s+мест\w*|победител\w*|рейтинг\w*|рекомендую\s+(?:выбрать|работать))\b",
    re.IGNORECASE,
)
NUMBER = re.compile(r"(?<![\w])[+−-]?\d+(?:[,.]\d+)?")


@lru_cache(maxsize=1)
def schema() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    path = root / "services/root_agent/src/core/report_contract.py"
    spec = importlib.util.spec_from_file_location("eval_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Не удалось загрузить {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RESPONSE_SCHEMA


def text_of(turn: dict[str, Any], suggestions: bool = False) -> str:
    parts: list[str] = []
    for key in ("reply_text", "headline"):
        if isinstance(turn.get(key), str):
            parts.append(turn[key])
    for key in ("brief", "facts", "gaps", "next_steps"):
        for item in turn.get(key, []) if isinstance(turn.get(key), list) else []:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.extend(item[name] for name in ("text", "why") if isinstance(item.get(name), str))
    if suggestions:
        parts.extend(item for item in turn.get("suggestions", []) if isinstance(item, str))
    return "\n".join(filter(None, parts))


def numbers(text: str) -> set[Decimal]:
    result = set()
    for value in NUMBER.findall(text):
        try:
            result.add(Decimal(value.replace("−", "-").replace(",", ".").lstrip("+")).normalize())
        except InvalidOperation:
            pass
    return result


def evaluate(envelope: dict[str, Any], case: Scenario) -> list[Issue]:
    if not isinstance(envelope, dict) or not envelope.get("ok"):
        return [Issue("backend", str(envelope.get("error", "Некорректный ответ бэкенда")))]
    turn = envelope.get("turn")
    if not isinstance(turn, dict):
        return [Issue("turn", "Поле turn отсутствует")]

    analyze = case.analyze()
    expected = case.expected
    clean_turn = {key: value for key, value in turn.items() if key != "_latency_s"}
    issues = [
        Issue("schema", f"{'.'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}")
        for error in list(Draft202012Validator(schema()).iter_errors(clean_turn))[:5]
    ]
    issues.extend(
        Issue("backend_validation", str(item))
        for item in (envelope.get("validation") or {}).get("issues", [])
    )

    text = text_of(turn)
    visible = text_of(turn, suggestions=True)
    if match := LEAK.search(visible):
        issues.append(Issue("technical_leak", f"Техническое значение в тексте: {match.group(0)!r}"))
    if match := VERDICT.search(text):
        issues.append(Issue("verdict", f"Безусловный вердикт: {match.group(0)!r}"))
    if expected.no_ranking and (match := RANKING.search(text)):
        issues.append(Issue("ranking", f"Ранжирование компаний: {match.group(0)!r}"))

    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text)
    cyrillic = re.findall(r"[А-Яа-яЁё]", text)
    if letters and len(cyrillic) / len(letters) < 0.65:
        issues.append(Issue("language", "Ответ преимущественно не на русском языке"))

    allowed_numbers = numbers(json.dumps(analyze, ensure_ascii=False) + case.message + str(len(case.inns)))
    extra_numbers = numbers(text) - allowed_numbers
    if extra_numbers:
        issues.append(Issue("numbers", f"Числа отсутствуют во входе: {sorted(extra_numbers)}"))

    if turn.get("answer_kind") not in expected.kinds:
        issues.append(Issue("kind", f"Ожидалось {expected.kinds}, получено {turn.get('answer_kind')!r}"))
    panel = turn.get("panel") if isinstance(turn.get("panel"), dict) else {}
    if expected.panels and panel.get("kind") not in expected.panels:
        issues.append(Issue("panel", f"Ожидалось {expected.panels}, получено {panel.get('kind')!r}"))
    if expected.blocks and panel.get("block") not in expected.blocks:
        issues.append(Issue("block", f"Ожидалось {expected.blocks}, получено {panel.get('block')!r}"))
    if panel.get("inn") is not None and panel.get("inn") not in analyze:
        issues.append(Issue("panel_inn", f"Чужой ИНН в panel: {panel.get('inn')}"))
    if turn.get("primary_inn") is not None and turn.get("primary_inn") not in analyze:
        issues.append(Issue("primary_inn", f"Чужой primary_inn: {turn.get('primary_inn')}"))

    for pattern in expected.required:
        if not re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            issues.append(Issue("missing", f"Не найден обязательный смысл /{pattern}/"))
    for pattern in expected.forbidden:
        if match := re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            issues.append(Issue("forbidden", f"Найден запрещённый смысл: {match.group(0)!r}"))

    if expected.brief:
        brief = turn.get("brief", [])
        low, high = expected.brief
        if not isinstance(brief, list) or not low <= len(brief) <= high:
            issues.append(Issue("brief", f"Ожидалось {low}–{high} абзаца brief"))

    mentioned = sum(bool(re.search(MENTIONS[inn], text, re.IGNORECASE)) for inn in case.inns)
    if mentioned < expected.min_companies:
        issues.append(Issue("coverage", f"Названо компаний {mentioned}, ожидалось {expected.min_companies}"))
    for inn, pattern in MENTIONS.items():
        if inn not in analyze and re.search(pattern, text, re.IGNORECASE):
            issues.append(Issue("foreign_company", f"Упомянута компания {inn}, которой нет во входе"))

    signal_map: dict[str, dict[str, Any]] = {}
    fields: set[str] = set()
    for item in analyze.values():
        for signal in item.get("signals", []):
            signal_map[signal["id"]] = signal
            fields.update(signal.get("fields", []))

    claims = turn.get("claims", []) if isinstance(turn.get("claims"), list) else []
    reply = turn.get("reply_text", "")
    claimed = set()
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claimed.add(claim.get("field"))
        if claim.get("field") not in fields:
            issues.append(Issue("claim_field", f"Неизвестное поле claim: {claim.get('field')!r}"))
        if claim.get("signal_id") is not None and claim.get("signal_id") not in signal_map:
            issues.append(Issue("claim_signal", f"Неизвестный signal_id: {claim.get('signal_id')!r}"))
        if claim.get("inn") not in analyze:
            issues.append(Issue("claim_inn", f"Чужой ИНН в claim: {claim.get('inn')!r}"))
        if claim.get("text_span") not in reply:
            issues.append(Issue("claim_span", f"text_span отсутствует в reply_text: {claim.get('text_span')!r}"))
    for field in expected.claims:
        if field not in claimed:
            issues.append(Issue("missing_claim", f"Нет claim для {field!r}"))

    return issues
