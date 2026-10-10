# Первая компания

Сопровождающий выполняет этот проход сначала на учебной компании без личных данных и живых аккаунтов. Владелец подтверждает компанию, своё имя, общую Git-папку и разрешённую работу. Обычный пользователь затем говорит агенту: «Прочитай AGENTS.md, сохрани поручение подготовить сводку; непонятное оставь вопросом». Агент сам ведёт задачу и доставку.

## 1. Получить проверенную основу

Сопровождающий продукта в чистом принятом product checkout создаёт отдельную release-only историю и переносимый пакет штатной командой:

```sh
python3 scripts/product.py /absolute/new/release.git --bundle /absolute/new/company.bundle
```

Не клонировать product repo в компанию: в его истории есть непоставляемые материалы. Сохранить выданные `release_sha` и `bundle_sha256`, передать пакет и hashes по принятому каналу. На принимающем компьютере сверить SHA-256 файла, затем:

```sh
git clone --mirror /absolute/company.bundle /absolute/received-release.git
git -C /absolute/received-release.git rev-parse refs/heads/main
git clone --single-branch --branch main /absolute/received-release.git /absolute/release-tools
```

SHA main должен совпасть с переданным `release_sha`. `release-tools` — только инструменты установки из основы. `create` дополнительно проверяет все refs/историю release; наличие одного чистого HEAD недостаточно. Все назначения ниже новые; существующие папки не затирать.

## 2. Подготовить среду и компанию

В `release-tools` выполнить [setup выбранной среды](../adapters/README.md), затем `doctor`. В POSIX:

```sh
sh scripts/run.sh setup
sh scripts/run.sh doctor
sh scripts/run.sh create /absolute/received-release.git /absolute/company --id demo-company --owner demo-owner
cd /absolute/company
sh scripts/run.sh setup
sh scripts/run.sh doctor
```

На Windows тот же CLI вызывается через `& ./scripts/run.ps1`; пути заменить местными. Setup требует разрешённой сети и устанавливает только локальное окружение. Если Python3.12+, PyYAML6.0.1 и Git2.43+ уже готовы, допустим прямой `python3 scripts/system.py`. Далее показан именно этот вариант; после setup POSIX можно использовать `.venv/bin/python` вместо `python3`, Windows — `.venv/Scripts/python.exe`, либо launcher для команд system.py. Это не новая проверка native skills/hooks.

`create` задаёт `id`, `name`, `owner`, `permissions.local_work: true`, записывает ограниченное решение `initial-adaptation` и происхождение в `.system/base.json`. Прочитать `AGENTS.md`, `company/README.md`, `company/config.yaml`. Проверить реальную identity; учебные имена нельзя переносить в живую компанию. По умолчанию `definitions: {}`, `sources: {}`, `bindings: {}`, `permissions.external_delivery: false`, `permissions.remotes: []`; бюджет/KPI/права не выводятся из названия компании. Hooks и proactive оставить выключенными.

Источник добавлять только по принятому решению в `sources` config, например локальную разрешённую выгрузку:

```yaml
sources:
  accepted-export:
    type: export
    path: company/sources/input.json
    approved_by: demo-owner
    account: accepted-account
    scope: accepted-scope
    period: accepted-period
    unit: accepted-unit
    max_age_seconds: 3600
    max_pages: 1
    timeout_seconds: 5
```

Это пример формы, не готовое разрешение или рекомендуемые лимиты. Значения подтверждает владелец. Envelope и проверка полноты описаны в [data-access](../standards/data-access.md). Секреты/закрытые raw-файлы в общий Git не помещать. Для первой учебной сводки ниже внешний источник не нужен: её основание — неизменяемый снимок задач.

## 3. Подключить общий Git компании

Источник выпусков остаётся в `.system/base.json`. `origin` должен стать общим операционным Git этой компании. Для учебного локального прохода, после разрешённых правок config:

```sh
python3 scripts/system.py validate
python3 scripts/system.py commit company/config.yaml --message "Configure accepted company scope"
git clone --bare /absolute/company /absolute/company-common.git
git remote set-url origin /absolute/company-common.git
python3 scripts/system.py preflight
```

Коммит config нужен только если файл изменён. В живой компании использовать заранее согласованный remote и доступ. Для внешнего URL дополнительно нужны `permissions.external_delivery`, точный `permissions.remotes` и принятое правило export по [runtime](../standards/runtime.md); этот пример их не выдаёт. При dirty/разошедшихся копиях сохранять обе работы и готовить отдельный кандидат. Один preflight в начале самостоятельной задачи; доставку завершает readback.

## 4. Первая задача и проверенный результат

В компании создать критерий и критический разбор учебной сводки:

```sh
python3 - <<'PY'
import json
from pathlib import Path
p = Path('work/first-summary'); p.mkdir(parents=True, exist_ok=True)
def save(name, value):
    (p / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
save('acceptance.json', {'kind': 'action-summary'})
save('critical.json', {
    'request_alignment': 'Сводка отражает записанные задачи, владельцев и следующий шаг',
    'counterexample': 'Пустая учебная очередь не доказывает отсутствие работы в компании',
    'limitations': ['Учебный локальный проход; применение и эффект неизвестны'],
    'references': ['company/config.yaml']})
PY
python3 scripts/system.py intake first-summary --request "Подготовить учебную сводку записанных задач" --owner demo-owner --acceptance work/first-summary/acceptance.json --confirmed --skill company-summary
python3 scripts/system.py summary --task first-summary > work/first-summary/candidate.json
python3 scripts/system.py context --task first-summary
```

`--confirmed` допустим только после настоящего подтверждения поручения. Проверить кандидат и его limitations; первичная пустая очередь допустима, но не доказывает полноту реальных дел. `summary --task` сохраняет snapshot в `inputs`; простой `summary` только показывает. Узнать текущую revision из task.json непосредственно перед исполнением:

```sh
REV=$(python3 -c "import json; print(json.load(open('work/first-summary/task.json'))['revision'])")
python3 scripts/system.py execute first-summary --artifact work/first-summary/candidate.json --critical work/first-summary/critical.json --expected-revision "$REV"
python3 scripts/system.py validate
python3 scripts/system.py summary
```

Если нужен независимый review, передать реальный `--review`, а не собственное «PASS». Отказ stale означает, что задачу изменили: прочитать её и заново проверить намерение, не подставлять свежую revision автоматически. Неизвестный владелец/право/критерий сохраняется через intake без `--confirmed` или с `--unknown "какое решение и кто нужен"`; зависимое execute остановится. `task.json` остаётся единственным состоянием, второй STATUS/TODO не нужен.

## 5. Сохранить, доставить, продолжить

```sh
BASE=$(git rev-parse refs/remotes/origin/main)
python3 scripts/system.py commit work/first-summary --message "Verify first company summary"
python3 scripts/system.py deliver /absolute/company-common.git --expected-base "$BASE" --task first-summary
python3 scripts/system.py context --task first-summary
```

Проверить `readback-confirmed` и `delivery.status: delivered`; команда доставляет также запись readback. Проверка файла означает `verified`, доставка отдельно, application/effect пока `unknown`. После успешного readback повторный sync не нужен. При timeout сперва выяснить общий SHA/содержимое до повтора.

В новой сессии открыть эту же компанию, явно прочитать AGENTS/config/карту, выполнить один preflight и `context --task first-summary`. Прочитать указанный task.json, output и следующий шаг. Новый чат восстанавливает работу по файлам; автоматическое native discovery не предполагается. [Наблюдения](observations.md) добавляются только из фактических свидетельств владельца, не из artifact.

## 6. Обновить и восстановить

Перед обновлением завершить разрешённые записи и доставку. Из новой проверенной release-only основы подготовить отдельный кандидат:

```sh
python3 scripts/system.py update /absolute/next-release.git /absolute/company-update
```

`already-installed` — повтор той же основы. `verified-candidate` — проверить в указанном кандидате doctor/validate, identity, точные company/work bytes и исторические результаты, затем доставить его в общий remote с `--expected-base` из `company_base`. При `conflict` или `needs-reconciliation` сохранить кандидат и обе версии; решение владельца и `finish-update --target SHA --company-base SHA` применимы только к разрешённому merge-кандидату, не к отказу `phase: clone`. Активные старые задачи требуют принятой сверки bindings. Исходная компания не меняется подготовкой update.

Резервная копия и реальное восстановление в новую папку:

```sh
python3 scripts/system.py backup /absolute/new/company-backup
python3 scripts/system.py restore /absolute/new/company-backup /absolute/new/company-restored
```

Проверить `restored-and-verified`, HEAD, manifest hashes и tracked company/work bytes, затем подготовить окружение заново и проверить doctor/context/validate. Backup содержит только tracked файлы и Git-историю; untracked/ignored secrets, runtime и данные провайдеров требуют отдельного принятого backup. Restore не затирает живую папку и не отменяет внешние действия. Native settings после переноса требуют отдельной projection/trust проверки; hooks остаются optional.

Учебный проход доказывает только локальные команды, доставку в выбранный Git и восстановление конкретных bytes. Реальное использование командой и бизнес-эффект проверяются после раскатки.
