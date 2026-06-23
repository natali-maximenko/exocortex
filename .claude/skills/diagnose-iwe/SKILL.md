---
name: diagnose-iwe
description: "Diagnose mastery level (Diagnostician R28, FORM.089 §6.1) directly in VS Code / claude.ai. 5–9 questions, ~5–10 min. Saves cp-profile to digital twin (browser) or Neon (VS Code). Launch when pilot says 'run diagnostics', 'what is my level', '/diagnose-iwe' — or PROACTIVELY when you see an empty cp_profile or a new user without data."
argument-hint: "[необязательно: --check для просмотра профиля без нового опроса]"
related: [WP-318, DP.ROLE.042, DP.SC.132, PD.FORM.089]
version: 1.1.0
layer: L1
status: active
triggers:
  slash: [/diagnose-iwe]
  phrases: []
routing:
  executor: sonnet
  deterministic: false
---

# /diagnose-iwe — диагностика ступени мастерства

> ⚡ **Алгоритм, не свободный разговор.** Шаги 1-5 последовательно. Ждать ответа после каждого вопроса. Без преждевременных выводов.

> **Алгоритм CAT (FORM.089 §6.1 v5.0):** 5–9 вопросов, старт со ступени 3. Фаза 1 — 5 якорных вопросов (cp.rhy, cp.wld, cp.int, cp.agt, cp.iwe). Фаза 2 — drill-down для слотов с оценкой < 3. cp.skl — производный срез (= cp.rhy), не задаётся напрямую.
>
> **Данные вопросов и шкал:** `shared/rubrics/form-089.yaml` (SoT). Секции между маркерами `<!-- RUBRIC-AUTO:... -->` генерируются скриптом `scripts/generate-diagnose-iwe-skill.py`. **Не редактировать вручную** — обновлять YAML, затем запускать генератор.

## Контракт скилла

- **Вход:** пилот вызвал скилл или Claude видит пустой cp_profile.
- **Выход:** cp-профиль в чате + сохранение (путь зависит от интерфейса — см. Шаг 5).
- **Время:** 5–10 мин (5–9 вопросов).
- **Не делает:** не даёт рекомендации по развитию (это Навигатор R27), не строит персональное руководство (это Портной R28).

## Определить интерфейс (ПЕРВЫМ действием)

**Браузер (claude.ai):** инструмент Bash недоступен → сохранение через `dt_write_digital_twin`.
**VS Code / локальный:** Bash доступен → сохранение в Neon через psycopg2 (+ локальный fallback).

Проверка: попробовать Bash. Если недоступен — работаем в браузерном режиме.

## Шаг 0. Проверить существующий профиль

### Браузерный режим

Вызвать `mcp__claude_ai_IWE__dt_read_digital_twin` с path `1_declarative/cp_profile`.

Если возвращает данные с полем `assessed_at` — проверить возраст:
- Профиль есть и моложе 30 дней → показать, предложить пройти заново.
- `--check` → показать и завершить.
- Нет профиля или старше 30 дней → продолжить с Шага 1.

### VS Code режим

```bash
source ~/.config/aist/env
python3 -c "
import os, json
try:
    import psycopg2
    conn = psycopg2.connect(os.environ['NEON_LEARNING_URL'])
    cur = conn.cursor()
    cur.execute('''SELECT stage, bottleneck_slot, recommended_stream, assessed_at, valid_until
                   FROM learning.cp_assessments
                   WHERE account_id = %s::uuid
                   ORDER BY assessed_at DESC LIMIT 1''',
                (os.environ['DT_USER_ID'],))
    row = cur.fetchone()
    conn.close()
    if row:
        stage, bottleneck, stream, assessed_at, valid_until = row
        print(json.dumps({'stage': stage, 'bottleneck': bottleneck, 'stream': stream,
                          'assessed_at': str(assessed_at)[:10], 'valid_until': str(valid_until)[:10]}))
    else:
        print('null')
except Exception as e:
    print(f'error: {e}')
"
```

**Формат вывода существующего профиля (оба режима):**

```
📊 Текущий cp-профиль (диагностика от YYYY-MM-DD)

Ступень: [название] ([N] из 5)
Приоритет роста: [слот по-русски]
Рекомендованный поток: [SN]
Действителен до: YYYY-MM-DD

Хочешь пройти диагностику заново? (она обновит профиль)
```

## Шаг 1. Объявить диагностику

Открыть одним сообщением:

```
🔬 Диагностика ступени мастерства

5–9 вопросов. Для каждого выбери цифру от 1 до 5, где:
1 — совсем не про меня / нет опыта
5 — полностью про меня / устойчивая практика

Выбирай то, что ближе к реальности прямо сейчас, не к идеалу.

Готов? Тогда начнём.
```

Ждать ответа («да», «давай», «начнём» или любой другой — сигнал готовности).

## Шаг 2. Фаза 1 — якорные вопросы (все 5)

Задавать по одному, ждать ответ 1-5 после каждого. Записывать в `scores`.

<!-- RUBRIC-AUTO:phase1 v5.0 -->
**Вопрос 1 (cp.rhy):**
Как вы ведёте учёт времени на саморазвитие и насколько регулярный ритм? Сколько примерно часов в неделю?

1 — Не выделяю, как пойдёт
2 — Стараюсь, но без ритма (1-2 ч/нед)
3 — Еженедельно явно (3-4 ч/нед)
4 — Ежедневная практика + трекер (5-10 ч/нед)
5 — Автоматизировано + артефакты (10+ ч/нед)

**Вопрос 2 (cp.wld):**
Как вы принимаете важные решения? Через интуицию, ценности или системный анализ?

1 — В основном интуитивно
2 — Пробую разные подходы, не сложилось
3 — Через сформулированные принципы / мировоззрение
4 — Системно: цели, ограничения, альтернативы
5 — Передаю свои методы и принципы другим

**Вопрос 3 (cp.int):**
Применяете ли вы системное мышление — видите ли роли, границы, интерфейсы, надсистемы в реальных задачах?

1 — Нет опыта
2 — Слышал(а), но не применяю
3 — Базовые различения (роль/функция/граница)
4 — Системный разбор в работе
5 — Формализую модели, учу других

**Вопрос 4 (cp.agt):**
Какая доля задач за последний месяц инициирована вами лично — не «спустили», а вы сами увидели и взяли?

1 — Почти всё спущено сверху
2 — Иногда сам(а), редко
3 — Около половины — мои
4 — Большинство задач — моя инициатива
5 — Задаю повестку для других

**Вопрос 5 (cp.iwe, информационный):**
Насколько у вас настроена среда работы со знаниями — заметки, база знаний, инструменты (VS Code + Pack + ИИ, или альтернативы)?

1 — Среды нет
2 — Простейшее (заметки в телефоне, папка в облаке)
3 — Базово настроено, пользуюсь регулярно
4 — Несколько сервисов, структура, связи, поиск
5 — Развиваю как систему — Pack/протоколы/агенты
<!-- /RUBRIC-AUTO:phase1 -->

После всех 5 вопросов:
- Записать: `scores = {cp.rhy: X, cp.wld: X, cp.int: X, cp.agt: X, cp.iwe: X}`
- **Вычислить производный срез (v5.0):** `scores['cp.skl'] = scores['cp.rhy']` — cp.skl не задаётся, выводится из ритма.

## Шаг 3. Фаза 2 — drill-down (при необходимости)

**Детерминированный алгоритм (итерация по ВСЕМ слабым срезам, дедуп по target):**

```python
# 0. После Фазы 1 все срезы имеют значения (cp.skl выведен выше). Дефолты не нужны.
ALL_SLOTS = ['cp.rhy', 'cp.wld', 'cp.skl', 'cp.iwe', 'cp.int', 'cp.agt']

# 1. Маппинг weak-источник → drill-target (из YAML drill_down_slot)
# v5.0: cp.iwe — информационный, drill_down_slot=null → не включён.
# cp.skl производный от cp.rhy → тот же drill-target (dedup обрабатывает повтор).
DRILL_MAP = {
    'cp.rhy': 'cp.rhy',
    'cp.wld': 'cp.wld',
    'cp.skl': 'cp.rhy',   # производный = cp.rhy; dedup не допустит повторный вопрос
    'cp.int': 'cp.int',
    'cp.agt': 'cp.agt',
    # cp.iwe: null — не задаётся в drill (информационный)
}

# 2. ВСЕ слабые срезы (< 3) с drill-target, дедуп по target
weak_sources  = [s for s in ALL_SLOTS if scores[s] < 3 and s in DRILL_MAP]
drill_targets = sorted(
    {DRILL_MAP[s] for s in weak_sources},
    key=lambda t: min(scores[s] for s in weak_sources if DRILL_MAP[s] == t)  # слабейший — первым
)

# 3. По ОДНОМУ вопросу на каждый уникальный target
for target in drill_targets:
    scores[target] = ask(target)   # ответ 1-5 по рубрике ниже
```

> **Почему дедуп обязателен:** `cp.skl→cp.rhy` и `cp.rhy→cp.rhy` дают одинаковый target → один вопрос про ритм, не два.
>
> **cp.iwe:** информационный, drill_down_slot=null → даже если cp.iwe < 3, drill не задаётся.
>
> **Бюджет:** Фаза 1 = 5 слотов; Фаза 2 = 0–4 уникальных drill-target. Типично 0–2 (зависит от слабых срезов). Суммарно 5–9 вопросов.

**Вопросы и шкалы drill-down (из YAML, авто-генерация):**

<!-- RUBRIC-AUTO:drill_down v5.0 -->
**cp.rhy** — Ритуалы и регулярность
Есть ли у вас «ритуалы» начала/завершения рабочей недели? Или регулярные точки рефлексии?
1 — Нет ничего
2 — Иногда делаю итоги
3 — Еженедельный ритуал
4 — Структурированные ритуалы
5 — Полная ОРЗ-практика

**cp.wld** — Мировоззрение (уточнение)
Можете назвать 2-3 принципа, которые направляют ваши решения? Насколько они явные?
1 — Не сформулированы
2 — Смутное ощущение
3 — Могу назвать 1-2
4 — Явные, записаны
5 — Работающая система

**cp.int** — Системное мышление (уточнение)
Пробовали ли вы разбирать ситуацию через надсистему и подсистему? Выделять роли, функции, ограничения?
1 — Незнакомо
2 — Слышал(а), не применял(а)
3 — Интуитивно
4 — Осознанно
5 — Учу других

**cp.agt** — Агентность (уточнение)
Берёте ли вы на себя ответственность за результат, даже если обстоятельства были неблагоприятные?
1 — Объясняю обстоятельствами
2 — Иногда беру
3 — Обычно беру
4 — Всегда беру
5 — Задаю стандарты
<!-- /RUBRIC-AUTO:drill_down -->

## Шаг 4. Вычислить профиль

```python
mandatory_scores = {k: scores[k] for k in ['cp.rhy', 'cp.wld', 'cp.skl', 'cp.int', 'cp.agt']}
# БЕЗ cp.iwe — информационный, ступень НЕ блокирует (решение пилота 2026-06-08)
vals = mandatory_scores
```

**Формулы (из `shared/rubrics/form-089.yaml § scoring`):**

<!-- RUBRIC-AUTO:formulas v5.0 -->
stage:      `min(vals.values())`
bottleneck: `min(mandatory_scores, key=mandatory_scores.get)`
<!-- /RUBRIC-AUTO:formulas -->

```python
cp_confirmed_stage = min(vals.values())
bottleneck_slot    = min(mandatory_scores, key=mandatory_scores.get)
recommended_stream = "S" + str(max(1, min(4, cp_confirmed_stage)))
```

Ступени: 1-Случайный / 2-Практикующий / 3-Систематический / 4-Дисциплинированный / 5-Проактивный

Bottleneck по-русски:
- cp.rhy → «регулярность и ритм занятий»
- cp.wld → «мировоззрение и системный взгляд»
- cp.skl → «осознанное инвестирование времени»
- cp.iwe → «инструмент работы со знаниями»
- cp.int → «системное мышление»
- cp.agt → «методы и агентность»

Потоки: S1-«Фундамент» / S2-«Систематизация» / S3-«Масштаб» / S4-«Передача»

## Шаг 5. Показать результат и сохранить

Сначала показать итог в чате:

```
📊 Результаты диагностики

Ступень: [НАЗВАНИЕ] ([N] из 5)
Приоритет роста: [bottleneck по-русски]
Рекомендованный поток: [SN] — [label потока]

Профиль по слотам:
cp.rhy [N] | cp.wld [N] | cp.skl [N]
cp.iwe [N] | cp.int [N] | cp.agt [N]

(действителен 180 дней)
```

Затем сохранить — путь зависит от интерфейса:

### Браузерный режим (claude.ai) — сохранить через dt_write_digital_twin

Вычислить `assessed_at` (сегодняшняя дата ISO) и `valid_until` (+180 дней).

Вызвать `mcp__claude_ai_IWE__dt_write_digital_twin`:
- **path:** `1_declarative/cp_profile`
- **data:**
```json
{
  "stage": <N>,
  "bottleneck_slot": "<cp.xxx>",
  "recommended_stream": "<SN>",
  "skip_to_stage": <N>,
  "cp_scores": {
    "cp.rhy": <N>, "cp.wld": <N>, "cp.skl": <N>,
    "cp.iwe": <N>, "cp.int": <N>, "cp.agt": <N>
  },
  "source": "self_report",
  "interface": "browser",
  "rcs_version": "v5.0",
  "assessed_at": "YYYY-MM-DD",
  "valid_until": "YYYY-MM-DD"
}
```

Если `dt_write_digital_twin` вернул успех → сообщить: `✅ Профиль сохранён в цифровой двойник`.
Если ошибка → показать профиль в чате и попросить пользователя скопировать его вручную.

### VS Code режим — сохранить в Neon через Bash

```bash
source ~/.config/aist/env
CP_SCORES='<JSON с финальными scores>' \
CP_Q_COUNT=<число заданных вопросов> \
python3 -c "
import os, json, datetime as dt_lib

scores_raw = os.environ['CP_SCORES']
q_count    = int(os.environ.get('CP_Q_COUNT', '6'))
account_id = os.environ.get('DT_USER_ID', '')
url        = os.environ.get('NEON_LEARNING_URL', '')

slots     = ['cp.rhy', 'cp.wld', 'cp.skl', 'cp.iwe', 'cp.int', 'cp.agt']  # все 6 — для профиля cp_scores
mandatory = ['cp.rhy', 'cp.wld', 'cp.skl', 'cp.int', 'cp.agt']            # 5 — ступень (FORM.089 §5.1, без cp.iwe)
scores = json.loads(scores_raw)
vals   = {s: int(scores.get(s, 2)) for s in slots}
stage  = min(vals[s] for s in mandatory)
bn     = min(mandatory, key=lambda s: vals[s])
stream = 'S' + str(max(1, min(4, stage)))
valid  = dt_lib.datetime.now(dt_lib.timezone.utc) + dt_lib.timedelta(days=180)

saved = False
if url and account_id:
    try:
        import psycopg2
        conn = psycopg2.connect(url)
        cur  = conn.cursor()
        cur.execute(
            '''INSERT INTO learning.cp_assessments
               (account_id, stage, bottleneck_slot, recommended_stream, skip_to_stage,
                cp_scores, source, interface, questions_count, rcs_version, valid_until)
               VALUES (%s::uuid, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
               RETURNING id''',
            (account_id, stage, bn, stream, stage, json.dumps(vals),
             'self_report', 'vscode', q_count, 'v5.0', valid)
        )
        row_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        print(f'OK neon id={row_id}')
        saved = True
    except Exception as e:
        print(f'neon error: {e}')

if not saved:
    import pathlib
    d = pathlib.Path.home() / '.aist' / 'cp-assessments'
    d.mkdir(parents=True, exist_ok=True)
    path = d / (dt_lib.date.today().isoformat() + '.json')
    path.write_text(json.dumps({'account_id': account_id, 'stage': stage,
        'bottleneck_slot': bn, 'recommended_stream': stream,
        'cp_scores': vals, 'source': 'self_report', 'interface': 'vscode',
        'questions_count': q_count, 'rcs_version': 'v5.0',
        'valid_until': valid.isoformat()}, indent=2))
    print(f'saved local: {path}')
"
```

Если вывод содержит `OK neon` — профиль в Neon. Если `saved local` — сохранён локально.

## Шаг 6. Следующий шаг

После показа результата — предложить:
```
Следующий шаг:
→ Навигатор, как развивать [bottleneck по-русски]? — рекомендации по росту
→ /diagnose в боте @aist_pilot_me — пройти диагностику там тоже
→ /progress в боте — посмотреть полный профиль прогресса
```

## Граничные случаи

| Ситуация | Действие |
|---|---|
| Пилот не отвечает числом 1-5 | Переспросить: «Выбери цифру от 1 до 5» |
| Пилот хочет пропустить вопрос | Присвоить дефолт 2 (conservative), перейти дальше |
| dt_write_digital_twin недоступен | Показать профиль в чате, попросить скопировать |
| psycopg2 не установлен (VS Code) | Сохранить в `~/.aist/cp-assessments/YYYY-MM-DD.json` |
| Профиль уже есть (< 30 дней) | Показать и спросить: «Пройти заново?» |

## Проактивный триггер

Claude должен предложить `/diagnose-iwe` **без явного запроса** когда:
1. В `dt_read_digital_twin` по path `1_declarative/cp_profile` нет данных или `stage = null`
2. В `day-open` IWE-данные пустые или `stage = null`
3. Пилот говорит «с чего начать», «как мне развиваться», «что делать дальше» — без контекста ступени

Формулировка предложения:
```
Вижу, что cp-профиль не заполнен (или устарел).
Хочешь пройти диагностику ступени? ~7 мин, 5-9 вопросов — скажи «да» или `/diagnose-iwe`.
```
