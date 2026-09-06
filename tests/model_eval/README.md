# Проверка ответов локальной Qwen

Тесты вызывают реальный `POST /chat` из `services/root_agent/src/web_agent.py`. Движок правил не пересчитывается: в запрос передаются готовые `analyze_results`, как во фронте.

Есть 20 сценариев для пустой сессии и сессий с 1, 2, 3 и 5 компаниями: сводка, точные вопросы, отсутствующие данные, ИП, неизвестная метка банка, банкротство, сравнение, контекст сделки и защита от требования выдать рейтинг.

## 1. Установить зависимости

Из каталога `app`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r tests/model_eval/requirements.txt
```

## 2. Запустить модель

В LM Studio загрузите `qwen/qwen3.8-27b` и включите OpenAI-совместимый сервер на `http://localhost:1234`.

Если идентификатор или адрес отличаются:

```bash
export LM_MODEL="ваш-id-модели"
export LM_URL="http://localhost:1234/v1/chat/completions"
```

## 3. Запустить тесты

Быстрый прогон из 5 запросов:

```bash
.venv/bin/python -m pytest tests/model_eval --run-llm-evals --eval-profile smoke
```

Полный прогон из 20 запросов:

```bash
.venv/bin/python -m pytest tests/model_eval --run-llm-evals --eval-profile full
```

Один сценарий:

```bash
.venv/bin/python -m pytest tests/model_eval --run-llm-evals --eval-profile full -k bankruptcy
```

Тесты сами запускают `web_agent.py` на порту 8130, если он ещё не работает. После прогона ответы, время и нарушения сохраняются в `.artifacts/llm-eval/report-*.json`.

Проверка тестового валидатора без модели:

```bash
.venv/bin/python -m pytest tests/model_eval/test_evaluator.py
```

Тест считается проваленным при нарушении JSON Schema, выдуманном числе или поле, техническом пути в тексте, безусловном вердикте, рейтинге компаний, неверном блоке интерфейса или пропуске обязательного смысла ответа.
