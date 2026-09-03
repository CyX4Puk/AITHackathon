# Правила срабатывания сигналов

Детерминированные условия для кода и для проверки ответов LLM. Поля профиля и зоны отображения — в `profile_fields.md`. Структура отчёта — в `columns_spec.md`. Проверено на выгрузке `app/data/contractors_audit.snapshot.json` (100 отчётов); цифры срабатываний ниже — по ней.

**Не score, но фиксированный приоритет.** Правила не вычисляют «важность». Они задают, когда факт попадает в вывод, и в каком порядке заполняются слоты сводки (§5). Внутри слота действует фиксированный порядок полярностей: `negative` → `caution` → `gap` → `info` → `positive`. Это осознанное продуктовое решение (риск-факты раньше нейтральных), а не оценка агентом «насколько плохо».

**Факт ≠ пробел.** Пустой массив в отчёте означает «не найдено» (факт, полярность `info` или `positive`). Пробел (`gap`) — только когда раздел отсутствует в отчёте (`null` или нет ключа) или его значения пустые. Иначе колонка «полнота данных» в сравнении будет врать: в выгрузке ключи `executionProceedings` и `arbitrationByStatus` есть у всех 100 компаний, а `finReports` отсутствует у 25.

---

## 1. Нормализация данных

Выгрузка — Mongo Extended JSON. До применения правил код приводит поля к простым типам.

| Что | Как в выгрузке | Нормализация |
|---|---|---|
| Даты | `{"$date": "2026-08-27T21:00:00.000Z"}` — `reportDate`, `registrationDate`, `cofounders[].dateFrom`, `authPerson.positionDate`, `executionProceedings[].date`, `licenses[].issueDate` | → ISO-дата. Исключение: `inspections[].startDate/endDate` уже строка `"2023-11-17"` |
| Большие числа | `proceeds` в 15 записях — `{"$numberLong": "3730752000"}` | → число |
| Суммы ИП | `executionProceedings[].amount` — строка `"517235.54"` или `null` (770 из 3873) | → число или `null`; `null` не суммировать, считать отдельно |
| Учредитель активен | В `columns_spec.md` поле `isActive`, в выгрузке — `active` | Читать оба имени |
| Арбитраж | `arbitrationByStatus.*` у 52 компаний — вложенные `{}`; `commonCount` есть только у 48 | Отсутствующий `*Count` → `0`; отсутствующий `commonCount` → сумма всех `*Count` |
| Код репутационного фактора | Позитивный код `аrbitrationDefendant` содержит кириллическую «а» (U+0430) | Сравнивать коды после транслитерации кириллицы в латиницу или по нормализованному словарю |
| Единицы | `finReports` — рубли (`profit: -6518000` соответствует «‑6518.0 тыс. руб.» в тексте банка) | В текстах выводить в тыс. ₽ или млн ₽ с округлением до 1 знака |
| `riskLevel` | Кроме `LOW / MEDIUM / HIGH` встречается `UNKNOWN` (3 компании) | Отдельное правило `BANK_RISK_UNKNOWN` (gap) |
| ИП | 25 из 100 записей — индивидуальные предприниматели: ИНН 12 знаков, `shortName` начинается с «ИП», нет `finReports`, `foundersInfo`, `taxSystem`, `companySize` | Признак `isIP`; для ИП отсутствие отчётности и учредителей — норма, тексты gap-правил меняются (см. `FIN_MISSING`) |

**Подписи для человека:** `LOW` → «низкий», `MEDIUM` → «средний», `HIGH` → «высокий», `UNKNOWN` → «не определён»; `GREEN` → «зелёный», `YELLOW` → «жёлтый», `RED` → «красный». Коды в скобках не выводить. Отображение YELLOW/RED для ЗСК — открытый вопрос кейсодателю (`columns_spec.md`: банк показывает клиенту Green/grey/grey); до ответа выводим подпись как есть.

---

## 2. Вспомогательные определения

| Имя | Определение |
|---|---|
| `isIP` | `baseInfo.inn.length === 12` (у юрлиц — 10) |
| `absent(x)` | Ключ отсутствует или `null` |
| `empty(x)` | Массив/объект есть, но пустой |
| `reportDate` | Дата из `reportDate`; если нет — дата снимка выгрузки |
| `finSorted` | `finReports`, отсортированные по `common.year` по возрастанию (в выгрузке не отсортированы у 65 из 67) |
| `finWindow` | Последние 3 записи `finSorted` с непустым `common.year` |
| `hasProceeds(f)` | `f.common.proceeds` — число и `≠ 0` |
| `hasProfit(f)` | `f.common.profit` — число (ключ `profit` есть только в 79 из 194 годовых записей; `0` допустим) |
| `proceedsYears` | Записи `finWindow`, где `hasProceeds` |
| `profitYears` | Записи `finWindow`, где `hasProfit` |
| `finLast` | Последняя запись `finWindow` |
| `pctChange(old, new)` | `(new - old) / abs(old)`; применяется только к `proceedsYears`, где `old ≠ 0` гарантированно |
| `TREND_THRESHOLD` | `0.05` (5%) — граница роста/падения vs стагнации |
| `RECENT_MONTHS` | `24` — окно «недавней» смены учредителя/руководителя |
| `INCEPTION_DAYS` | `30` — если дата назначения/вхождения в пределах 30 дней от `registrationDate`, это не смена, а основание компании |
| `isRecentChange(date)` | `date` не пусто, `(reportDate - date) <= RECENT_MONTHS` месяцев **и** `abs(date - registrationDate) > INCEPTION_DAYS` |
| `activeEP` | Элементы `executionProceedings[]` с `active === true` |
| `epSum`, `epNoAmount` | Сумма `amount` по `activeEP`, где число; количество `activeEP` с `amount === null` |
| `dpCount`, `ppCount` | `defandantArbitrationPending.dpCount`, `plaintiffArbitrationPending.ppCount` (отсутствует → 0) |
| `defendantTotal` | `dfCount + daCount + dpCount` |
| `plaintiffTotal` | `pfCount + paCount + ppCount` |
| `arbTotal` | `commonCount`, если есть; иначе `defendantTotal + plaintiffTotal` |
| `violationInspections` | `inspections[]` с `inspectionStatus === "InspectionsViolationDetected"` (точное сравнение; в выгрузке встречаются `InspectionsViolationNotDetected`, `InspectionsUnknownResult`, `InspectionsCanceled`) |
| `negCodes`, `posCodes` | Множества `code` из `reputationalRisks.negative[]` / `positive[]` после нормализации |
| `dealContext` | Контекст сделки из сессии: `goal` (например `procurement`), `amount` — второй эшелон MVP |

---

## 3. Правила

Колонки: `summary` — может занять слот сводки (§5); `compare` — строка таблицы сравнения; `card` — всегда ✓, не показана.

### 3.1. Банк и статус

| ID | Полярность | IF | Поля | Текст | summary | compare |
|---|---|---|---|---|:---:|:---:|
| `STATUS_CLOSED` | negative | `status.status === "CLOSED"` | `status.status`, `status.reasonName` | «Организация не действует ({reasonName}).» | заголовок | ✓ |
| `BANK_RISK` | info | `riskLevel ∈ {LOW, MEDIUM, HIGH}` | `baseInfo.riskLevel` | «Оценка банка: риск {label}.» | ✓ | ✓ |
| `BANK_RISK_UNKNOWN` | gap | `riskLevel === "UNKNOWN"` или `absent` | `baseInfo.riskLevel` | «Оценка банка: уровень риска не определён.» | ✓ | ✓ |
| `BANK_ZSK` | info | `zskRiskLevel` не `absent` | `zskRiskLevel` | «ЗСК: {label}.» | ✓ | ✓ |

`BANK_RISK`/`BANK_RISK_UNKNOWN` и `BANK_ZSK` выводятся одной строкой сводки: «Оценка банка: риск низкий, ЗСК зелёный.»

### 3.2. Финансы

| ID | Полярность | IF | Поля | Текст | summary | compare |
|---|---|---|---|---|:---:|:---:|
| `FIN_MISSING` | gap | `absent(finReports)` или `empty(finReports)` (33 компании: 25 ИП + 8 юрлиц) | `finReports` | Юрлицо: «Финансовая отчётность в отчёте отсутствует — оценить финансы нельзя.» ИП: «ИП не публикует бухгалтерскую отчётность — оценить финансы по отчёту нельзя.» | ✓ | ✓ |
| `FIN_EMPTY` | gap | `finReports` есть, но `proceedsYears` и `profitYears` пусты (7 компаний) | `finReports[].common` | «Отчётность за {y1}–{y2} без показателей выручки и прибыли — оценить финансы нельзя.» | ✓ | ✓ |
| `FIN_TREND_INSUFFICIENT` | gap | `proceedsYears.length === 1` | `finReports[].common.proceeds` | «Выручка есть только за {year} ({value}); тренд оценить нельзя.» | ✓ | ✓ |
| `FIN_PROCEEDS_DOWN` | negative | `proceedsYears.length >= 2` и `pctChange(first, last) < -TREND_THRESHOLD` | `common.proceeds`, `common.year` | «Выручка снижается: {v1} ({y1}) → {v2} ({y2}), {pct}%.» | ✓ | ✓ |
| `FIN_PROCEEDS_UP` | positive | `proceedsYears.length >= 2` и `pctChange > TREND_THRESHOLD` | то же | «Выручка растёт: {v1} ({y1}) → {v2} ({y2}), +{pct}%.» | ✓ | ✓ |
| `FIN_PROCEEDS_FLAT` | info | `proceedsYears.length >= 2` и `pctChange ∈ [-T, T]` | то же | «Выручка стабильна: {v1} ({y1}) → {v2} ({y2}).» | ✓ | ✓ |
| `FIN_PROFIT_LOSS` | negative | `hasProfit(finLast)` и `finLast.common.profit < 0` | `common.profit` | «Убыток за {year}: {amount}.» | ✓ | ✓ |
| `FIN_PROFIT_DOWN` | negative | `profitYears.length >= 2` и `profit_last < profit_first` и `abs(profit_last - profit_first) > 0.05 * abs(profit_first)` | `common.profit` | «Прибыль снижается: {v1} ({y1}) → {v2} ({y2}).» | ✓ | ✓ |
| `FIN_PROFIT_UP` | positive | `profitYears.length >= 2` и `profit_last > profit_first` и та же граница 5% | то же | «Прибыль растёт: {v1} ({y1}) → {v2} ({y2}).» | ✓ | ✓ |
| `FIN_NEGATIVE_CAPITAL` | negative | `finLast.liabilities.capitals` — число `< 0` | `liabilities.capitals` | «Отрицательный собственный капитал: {amount} ({year}).» | ✓ | ✓ |
| `FIN_DEBT_LOAD` | negative | `finLast.liabilities.shortTermLiabilities.total` и `finLast.assets.currentAssets.total` — числа, первое `>` второго | оба поля | «Краткосрочные обязательства ({stl}) превышают оборотные активы ({ca}) за {year}.» | ✓ | ✓ |

Для тренда `first`/`last` берутся из `proceedsYears` (для прибыли — из `profitYears`), не из крайних записей окна: год с нулевой или отсутствующей выручкой в сравнение не попадает. Для прибыли процент не выводим (переходы через ноль делают его бессмысленным), только абсолютные значения.

### 3.3. Юридические события

| ID | Полярность | IF | Поля | Текст | summary | compare |
|---|---|---|---|---|:---:|:---:|
| `EP_ACTIVE` | negative | `activeEP.length > 0` (18 компаний) | `executionProceedings[].active`, `amount` | «Активные исполнительные производства: {count} на {epSum}» + при `epNoAmount > 0`: «; по {n} сумма не указана» | ✓ | ✓ |
| `EP_HISTORY_ONLY` | info | `executionProceedings.length > 0` и `activeEP.length === 0` | `executionProceedings[]` | «Исполнительные производства: {count} завершённых, активных нет.» | ✓ | ✓ |
| `EP_NONE` | positive | `empty(executionProceedings)` | `executionProceedings` | «Исполнительные производства не найдены.» | ✓ | ✓ |
| `EP_MISSING` | gap | `absent(executionProceedings)` (в выгрузке — 0 компаний) | `executionProceedings` | «Сведений об исполнительных производствах в отчёте нет.» | ✓ | ✓ |
| `ARB_DEFENDANT_OPEN` | negative | `dpCount > 0` (17 компаний) | `defandantArbitrationPending.dpCount`, `dpAmount` | «Открытые арбитражные дела как ответчик: {dpCount} на {dpAmount}.» | ✓ | ✓ |
| `ARB_DEFENDANT_DOMINANT` | caution | `defendantTotal >= 2` и `defendantTotal > plaintiffTotal` и не сработал `ARB_DEFENDANT_OPEN` | `arbitrationByStatus` | «Арбитраж: как ответчик {def} дел, как истец {pl}.» | ✓ | ✓ |
| `ARB_SUMMARY` | info | `arbTotal > 0` и не сработали два правила выше | `arbitrationByStatus`, `arbitrationCases[]` | «Арбитраж: {arbTotal} дел (истец {pl}, ответчик {def}), открытых как ответчик нет.» | ✓ | ✓ |
| `ARB_NONE` | positive | блок есть и `arbTotal === 0` | `arbitrationByStatus` | «Арбитражные дела не найдены.» | ✓ | ✓ |
| `ARB_MISSING` | gap | `absent(arbitrationByStatus)` | `arbitrationByStatus` | «Сведений об арбитраже в отчёте нет.» | ✓ | ✓ |
| `INSPECTION_VIOLATION` | negative | `violationInspections.length > 0` (в выгрузке — 0) | `inspections[].inspectionStatus` | «Проверки с нарушениями: {count} из {total}.» | ✓ | ✓ |
| `INSPECTION_CLEAN` | info | `inspections.length > 0` и `violationInspections` пуст (30 компаний) | `inspections[]` | «Проверок: {total}, нарушений не выявлено» + при наличии `InspectionsUnknownResult`: «, по {n} результат неизвестен» | — | ✓ |
| `INSPECTION_MISSING` | gap | `absent(inspections)` (70 компаний) | `inspections` | «Сведений о проверках в отчёте нет.» | — | ✓ |

### 3.4. Реестры ФНС и репутационные факторы

Блок `reputationalRisks` в выгрузке — это результат прогона по реестрам (у всех 100 компаний, 9–22 позитивных записи). Работаем по `code`, а не по `name`: тексты банка длиной 150–300 символов в сводку не помещаются, показываем их только в карточке по клику.

**Коды-дубликаты** — исключены из сводки и сравнения, потому что тот же факт даёт правило из §3.2–3.3 или блок F: `executionProceedings`, `arbitrationDefendant`, `profit`, `proceeds`, `currentAssets`, `uncurrentAssets`, `relatedCompanies`, `governmentContract`, `webSite`, `licenses`, `branchesInfo`. В карточке они видны в исходном виде.

**Реестровые коды** — свои правила:

| ID | Полярность | IF (`code ∈ negCodes`) | Текст | summary | compare |
|---|---|---|---|:---:|:---:|
| `REG_BANKRUPTCY` | negative | `liquidationStatus` (1 компания) | «В реестре организаций в процедуре банкротства.» | ✓ | ✓ |
| `REG_FNS_BLOCKING` | negative | `fnsBlocking` (9) | «Действуют блокировки счетов по решению ФНС.» | ✓ | ✓ |
| `REG_INVALID_ADDRESS` | negative | `invalidAddress` (3) | «В реестре организаций с фиктивным адресом.» | ✓ | ✓ |
| `REG_INVALID_REG_DATA` | negative | `invalidRegistrationData` (3) | «Отметка ФНС о недостоверных регистрационных данных.» | ✓ | ✓ |
| `REG_INVALID_AUTHPERSON` | negative | `invalidAuthpersonsData` (1) | «Отметка ФНС о недостоверных данных руководителя.» | ✓ | ✓ |
| `REG_DISHONEST_PROVIDER` | negative | `dishonestProvider` | «В реестре недобросовестных поставщиков.» | ✓ | ✓ |
| `REG_DISQUALIFIED` | negative | `disqualifiedAuthpersons` | «Руководитель в реестре дисквалифицированных лиц.» | ✓ | ✓ |
| `REG_TAX_ARREARS` | negative | `taxArrears` | «В реестре должников ФНС.» | ✓ | ✓ |
| `REG_TAX_REPORTING` | negative | `taxReporting` | «В реестре организаций, не сдающих отчётность.» | ✓ | ✓ |
| `REG_MASS_ADDRESS` | caution | `massAddress` (13) | «Адрес регистрации — массовый.» | ✓ | ✓ |
| `REG_MASS_AUTHPERSON` | caution | `massAuthpersons` (2) | «Руководитель или учредитель — массовый.» | ✓ | ✓ |
| `REG_MASS_OKVED` | info | `massOkved` (25) | «Большое число кодов ОКВЭД ({n}).» | — | — |
| `REG_CLEAN` | positive | `reputationalRisks` есть, ни один реестровый код не в `negCodes` | «Проверено {n} реестров ФНС, негативных отметок нет.» | ✓ | ✓ |
| `REG_MISSING` | gap | `absent(reputationalRisks)` | «Сведений по реестрам ФНС в отчёте нет.» | ✓ | ✓ |

`{n}` в `REG_CLEAN` — число реестровых кодов в `posCodes`. Если сработало несколько `REG_*` negative/caution — в сводке они объединяются в одну строку через «;» и занимают один слот.

### 3.5. Расширение профиля

| ID | Полярность | IF | Поля | Текст | summary | compare | dealContext |
|---|---|---|---|---|:---:|:---:|:---:|
| `YOUNG_COMPANY` | caution | `yearsFromRegistration < 3` (29 компаний) | `registrationInfo.yearsFromRegistration`, `registrationDate` | «Компания зарегистрирована {date}, ей {n} лет.» | ✓ | ✓ | — |
| `FOUNDER_RECENT` | caution | ∃ `cofounders[]` с `isRecentChange(dateFrom)` | `cofounders[].dateFrom`, `registrationDate` | «Смена состава учредителей: {name} с {date}.» | ✓ | — | — |
| `AUTH_PERSON_RECENT` | caution | `isRecentChange(authPerson.positionDate)` | `authPerson.positionDate`, `registrationDate` | «Смена руководителя: {name} с {date}.» | ✓ | — | — |
| `LICENSE_EXPIRED` | negative | ∃ `licenses[]` с `status === "EXPIRED"` (в выгрузке — 0; статусы только `INDEFINITE`/`ACTIVE`) | `licenses[].status`, `name`, `endDate` | «Истёкшие лицензии: {list}.» | ✓ | — | — |
| `PROCUREMENT_WIN` | positive | ∃ год в `procurements[]` с `tenderWinnerCnt > 0` или `contractSignedCnt > 0` (8 компаний) | `procurements[]` | «Госзакупки: выиграно {wins}, контрактов {signed} на {amt}.» | ✓† | — | procurement |
| `PROCUREMENT_NONE` | info | `empty(procurements)` | `procurements` | «Участие в госзакупках не найдено.» | — | — | procurement |
| `DEAL_AMOUNT_DISPROPORTION` | caution | `dealContext.amount` задан, `hasProceeds(finLast)` и `amount > proceeds` | `dealContext.amount`, `common.proceeds` | «Сумма сделки ({amount}) превышает выручку за {year} ({proceeds}).» | ✓ | ✓ | amount |

† В summary по умолчанию — нет; только при `dealContext.goal === procurement`. Без контекста — card/qa.

`isRecentChange` исключает даты, совпадающие с регистрацией: у молодых компаний `dateFrom` и `positionDate` равны `registrationDate`, и без этого правило дублировало бы `YOUNG_COMPANY` ложным «смена учредителей». После исключения на выгрузке остаётся 24 реальные смены руководителя за 2025–2026; в большинстве из них в ту же дату сменился и учредитель. Если сработали оба правила, в сводке они объединяются в одну строку: «Смена руководителя и учредителя с {date}». Для ИП (`isIP`) правила `FOUNDER_RECENT` и `AUTH_PERSON_RECENT` не применяются.

**Убрано из MVP:** правило `LICENSE_OKVED_GAP` («нет лицензии для лицензируемого ОКВЭД»). Список 41/42/43/49 давал 29 ложных срабатываний из 100: строительство лицензий не требует (СРО, которого в отчёте нет), из 49 лицензируется только пассажирский транспорт. Возврат возможен после согласования списка с кейсодателем.

---

## 4. Сигнал → следующий шаг

Рекомендация — единственная часть сводки, где LLM формулирует свободно. Чтобы она была проверяемой и одинаковой между сессиями, LLM выбирает из таблицы по сработавшим правилам и переформулирует под контекст, но не придумывает новых действий. Вердикт «не работать» запрещён при любом наборе сигналов.

| Сигнал | Рекомендуемый шаг |
|---|---|
| `STATUS_CLOSED`, `REG_BANKRUPTCY` | Уточнить статус в ЕГРЮЛ/ЕФРСБ до любых переговоров |
| `EP_ACTIVE` | Запросить подтверждение погашения или справку ФССП; учесть сумму в условиях оплаты |
| `ARB_DEFENDANT_OPEN`, `ARB_DEFENDANT_DOMINANT` | Посмотреть предмет открытых дел в картотеке арбитражных дел; уточнить у контрагента |
| `REG_FNS_BLOCKING` | Запросить справку об отсутствии задолженности; не работать по предоплате до снятия блокировки |
| `REG_INVALID_ADDRESS`, `REG_MASS_ADDRESS`, `REG_INVALID_REG_DATA` | Подтвердить фактический адрес и реквизиты (договор аренды, выписка ЕГРЮЛ) |
| `REG_MASS_AUTHPERSON`, `REG_INVALID_AUTHPERSON`, `REG_DISQUALIFIED` | Запросить документы о полномочиях подписанта |
| `FIN_MISSING`, `FIN_EMPTY`, `FIN_TREND_INSUFFICIENT` | Юрлицо: запросить бухгалтерскую отчётность за 2–3 года напрямую. ИП: запросить налоговую декларацию или выписку по счёту за период |
| `FIN_PROFIT_LOSS`, `FIN_NEGATIVE_CAPITAL`, `FIN_DEBT_LOAD`, `FIN_PROCEEDS_DOWN` | Рассмотреть постоплату или обеспечение; запросить пояснения по отчётности |
| `DEAL_AMOUNT_DISPROPORTION` | Разбить сделку на этапы или запросить обеспечение |
| `YOUNG_COMPANY` | Запросить рекомендации/портфолио; начать с небольшого объёма |
| `FOUNDER_RECENT`, `AUTH_PERSON_RECENT` | Уточнить причины смены; проверить полномочия нового руководителя |
| `INSPECTION_VIOLATION` | Запросить акты проверок и сведения об устранении |
| `LICENSE_EXPIRED` | Запросить действующую лицензию до подписания |
| Только info/positive и `REG_CLEAN` | Стандартная проверка документов; сигналов, требующих отдельных действий, не выявлено |

---

## 5. Сборка risk summary

Сводка — до 5 фактов плюс рекомендация. Заголовок и слоты фиксированы; внутри слота первое сработавшее правило в порядке полярностей (см. преамбулу) и в порядке перечисления.

| Слот | Правила (первое сработавшее) |
|---|---|
| 0. Заголовок (вне лимита) | `STATUS_CLOSED`, если сработал |
| 1. Оценка банка | `BANK_RISK` / `BANK_RISK_UNKNOWN` + `BANK_ZSK` — одна строка |
| 2. Финансы | `FIN_PROCEEDS_DOWN`, `FIN_PROFIT_LOSS`, `FIN_NEGATIVE_CAPITAL`, `FIN_DEBT_LOAD`, `FIN_PROFIT_DOWN`, `FIN_MISSING`, `FIN_EMPTY`, `FIN_TREND_INSUFFICIENT`, `FIN_PROCEEDS_FLAT`, `FIN_PROCEEDS_UP`, `FIN_PROFIT_UP` |
| 3. Юридические события | `EP_ACTIVE`, `ARB_DEFENDANT_OPEN`, `INSPECTION_VIOLATION`, `ARB_DEFENDANT_DOMINANT`, `EP_MISSING`, `ARB_MISSING`, `EP_HISTORY_ONLY`, `ARB_SUMMARY`, `EP_NONE`, `ARB_NONE` |
| 4. Реестры ФНС | все сработавшие `REG_*` negative/caution одной строкой; иначе `REG_MISSING`; иначе `REG_CLEAN` |
| 5. Дополнительно | `DEAL_AMOUNT_DISPROPORTION`, `YOUNG_COMPANY`, `AUTH_PERSON_RECENT`, `FOUNDER_RECENT`, `LICENSE_EXPIRED`, `PROCUREMENT_WIN`† |

Слоты 1–4 заполняются всегда (у каждого есть gap- или info-fallback), то есть база — 4 факта. Пятый факт — первое из: второе сработавшее `negative` правило слота 3; второе сработавшее `negative` слота 2; слот 5. Если ничего из этого нет, сводка из 4 фактов.

Правило для LLM: в текст попадают только `summary_slots`; факты вне слотов доступны в карточке и Q&A. Формулировку можно сгладить (склонения, «1 год / 2 года»), числа и годы — как в `message`.

**Проверка на выгрузке (100 отчётов):** 41 сводка из 4 фактов, 59 из 5. Слот 2: `FIN_MISSING` 33, `FIN_PROCEEDS_UP` 31, `FIN_PROCEEDS_DOWN` 11, `FIN_TREND_INSUFFICIENT` 8, `FIN_EMPTY` 5, negative по прибыли/капиталу/долгу 11, `FLAT` 1. Слот 3: `EP_NONE` 38, `EP_HISTORY_ONLY` 23, `EP_ACTIVE` 18, `ARB_DEFENDANT_OPEN` 8, `ARB_SUMMARY` 7, `ARB_DEFENDANT_DOMINANT` 6. Слот 4: `REG_CLEAN` 76, `REG_MASS_ADDRESS` 13, `REG_FNS_BLOCKING` 6, прочие 5. Слот 5: `YOUNG_COMPANY` 28, `AUTH_PERSON_RECENT` 19, второй negative юр. блока 9, второй negative финансов 2.

---

## 6. Формат результата для кода

```json
{
  "inn": "7826131151",
  "signals": [
    {
      "id": "FIN_DEBT_LOAD",
      "polarity": "negative",
      "fields": [
        "finReports[year=2025].liabilities.shortTermLiabilities.total",
        "finReports[year=2025].assets.currentAssets.total"
      ],
      "message": "Краткосрочные обязательства (12,4 млн ₽) превышают оборотные активы (8,1 млн ₽) за 2025.",
      "zones": ["summary", "compare", "card"]
    },
    {
      "id": "REG_CLEAN",
      "polarity": "positive",
      "fields": ["reputationalRisks.positive[]"],
      "message": "Проверено 14 реестров ФНС, негативных отметок нет.",
      "zones": ["summary", "compare", "card"]
    }
  ],
  "gaps": ["INSPECTION_MISSING"],
  "summary_slots": ["BANK_RISK", "FIN_DEBT_LOAD", "EP_NONE", "REG_CLEAN", "YOUNG_COMPANY"],
  "next_steps": ["FIN_DEBT_LOAD", "YOUNG_COMPANY"]
}
```

Индексация `finReports[year=2025]` — по году, не по позиции в массиве (массив не отсортирован). `gaps` — все сработавшие правила с полярностью `gap`, независимо от попадания в сводку; из них строится колонка «полнота данных». `next_steps` — ID правил, по которым LLM берёт рекомендации из §4.

LLM получает `signals`, `summary_slots`, `next_steps` и формулирует текст; не добавляет факты, не покрытые сработавшими правилами, и не выбирает действия вне §4.

---

## 7. Связанные документы

- `profile_fields.md` — поля профиля и зоны UI
- `context_pack.md` — границы MVP
- `columns_spec.md` — полный перечень полей отчёта
