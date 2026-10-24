---
id: runtime
version: 11
owner: product-maintainer
---
# Форматы и подготовленные команды

## Текущие маршруты и сохранение документов

Дополнительные команды того же scripts/system.py:

| Операция | Вызов |
| --- | --- |
| Действующие маршруты | route-check [--paths-file exact-paths.json] либо route-check --path relative/path |
| Наблюдение сохранения | preservation --paths-file exact-paths.json [--task id] |
| Выбранная локальная копия | --root source-root snapshot --paths-file exact-paths.json new-snapshot-directory |
| Восстановление копии | restore-snapshot snapshot-directory new-restore-directory |

workspace.navigation необязателен: redirects object задаёт old-path: current-path, consumers list выбирает текущие документы, local_only list объясняет известные недоступные в clone цели. Пути канонические относительные. Команды маршрутов только читают; доказанное исправление выполняет агент по system-evolution/organization. context возвращает navigation и записанные слои задачи, output_local отдельно проверяет наличие и совпадение hash. preservation читает только перечисленные пути, не делает fetch и не объявляет доставку по одному Git commit. recorded-readback относится к точным текущим bytes результата и сохранённому readback, не к свежести remote или независимому удостоверению JSON.

Selected snapshot имеет kind selected-files-snapshot, schema_version1 и manifest files {relative-path: {sha256, size}}. Пределы:128 regular document files,8MiB на файл,32MiB всего,24компонента пути; допустимы md/json/yaml/yml/txt. Источник и назначение не пересекаются; назначение новое, его родитель существует. Snapshot не создаёт company config и не исполняет YAML/код. Частичная ошибка сохраняет неполный каталог без завершённого manifest; его нельзя объявлять готовой копией. Restore проверяет точный payload, переносимые пути, отсутствие aliases/collisions и hashes, затем копирует в новую папку. Manifest остаётся в исходной копии; источник восстановления и его bytes сохраняются.

## Явные свидетельства исследования

Новый research Result может иметь research_evidence {schema_version:1, outcome:findings|no-findings|inconclusive, reason, decision, conditions:[], gaps:[], recheck:[]}. Acceptance research_evidence_version:1 требует этот блок; переданный блок всегда проверяется. Пустые findings требуют явной причины и no-findings/inconclusive. Старые результаты без блока читаются без миграции и нового обязательства.

Enhanced finding сохраняет claim/source/level и добавляет уникальный id, outcome:supported|contradicted|unknown. Supported/contradicted имеют evidence:[{path,sha256,start_line,end_line,quote}]; unknown содержит конкретный gap. Ссылка/hash совпадают с Result.sources. Нумерация строк начинается с1, границы целые, quote точно равен выбранным UTF-8 строкам, соединённым LF; hash относится к исходным bytes, поэтому EOL-изменение обнаруживается. Совпадение цитаты подтверждает происхождение, а смысловую поддержку вывода оценивает reviewer.

Optional independent_review.process_reference {path,sha256} закрепляет retained JSON report: schema_version1, run_id, reviewer, artifact_hash, verdict, reviewed_claim_ids и challenge. Он связан с точным Result и проверяется при наличии; acceptance research_review_process_required:true требует его. Это сохранённое свидетельство запуска/содержательного review, не криптографическая проверка личности. Не прикладывать вымышленный запуск; synthetic примеры явно обозначают synthetic. Прежний review без нового требования работает по прежнему контракту.

Source metadata может выбрать selected_result:{path,sha256} вместо material. Индексируются только question,summary,claims/outcomes,decision,conditions,gaps,recheck этого research Result той же компании. Raw sources, quotes и произвольные дополнительные поля не индексируются; перехода по ссылкам в них нет. Выдача сохраняет канонический Result/hash и предел применимости; свежесть первичных источников при search не перепроверяется. Изменившийся pinned Result или одновременные material/selected_result отвергаются. Индекс производный и не заменяет владельца результата.

CLI вызывается через launcher выбранной [среды](../adapters/README.md). Примеры `python3 scripts/system.py <операция> ...` обозначают тот же CLI: в подготовленном локальном окружении заменить этот префикс на `sh scripts/run.sh` (POSIX) либо `& ./scripts/run.ps1` (PowerShell), сохранив операцию и аргументы. Глобальный python3, активация .venv и изменение PATH не требуются. Перед первой операцией агент выполняет setup, если окружение отсутствует; обычные команды сами зависимости не устанавливают.

Для компании origin должен указывать на её общий операционный Git, а не release-only основу продукта. External URL допускается только по permissions.external_delivery/remotes; нового права preflight не выдаёт. При отсутствии общего remote сначала адаптация владельцем. Новая компания из create сохраняет release provenance отдельно; источник обновлений не является общей очередью сотрудников. Product adapter `python3 scripts/product.py preflight` использует ту же функцию для явно разрешённого репозитория продукта, без переноса этого права в компании.

Для сохраняемой action-summary: intake задачи сводки → `summary --task id` → сохранить JSON-кандидат → execute → validate → общий commit/deliver. Sources результата указывают на неизменяемый snapshot в work/id/inputs с наблюдёнными revisions/hashes исходных задач; текущие task.json не являются источником исторического результата. Без --task команда только показывает текущее состояние. Подмена snapshot отвергается общим hash-validator.

Сводка показывает delivery/application/effect и точный output {path,sha256} независимо у каждой записи и в сохраняемом snapshot. Доставка не подтверждает применение; observed означает наличие свидетельства, включая отсутствие применения или отрицательный эффект, а не положительный исход. Старые schema1 snapshots без новых полей остаются читаемыми и проверяются по сохранённым bytes.

Наблюдения application/effect содержат evidence/actor/at/result_sha256 для точного output. Новый результат сбрасывает их в unknown; прежние подтверждения и доставка сохраняются в history. Подтверждение старой версии не переносится на новую.

Перед подготовкой результата или наблюдения один раз прочитать `context --task id` и task.json (для новой задачи допустим ответ intake/decide). Сохранить revision как версию намерения, а для наблюдения также точный output.sha256 из этого чтения. `execute --expected-revision N` и `observe --expected-revision N --expected-result-sha256 HEX` сравнивают эти значения под общим lock сразу после загрузки задачи, до записи/проверки кандидата/повтора. N обязателен, целый и неотрицательный; bool, пропуск, None и неверные типы в Python API отвергаются. HEX — ровно 64 строчных шестнадцатеричных символа текущего output.sha256. При отсутствующем, неверном или устаревшем token task/history/errors/results сохраняют исходные bytes. Кандидат остаётся на диске. После конфликта перечитать контекст, разобраться в чужом изменении и пересмотреть кандидат или принадлежность свидетельства; просто получить последние tokens и повторить прежнее намерение запрещено. Изменённые зависимости по-прежнему требуют принятого owner resume. Корректный повтор execute с текущей revision сохраняет прежнюю идемпотентность.

Python API сохраняет позиционные аргументы: `execute(root, id, artifact, critical, independent_review=None, *, expected_revision=None)` и `observe(root, id, layer, evidence, actor, *, expected_revision=None, expected_result_sha256=None)`. Defaults нужны для явного отказа старому небезопасному вызову, не для автоматического получения текущей версии. Чтение прежних task/result/snapshot schema1 не мигрирует и не переписывает их.

Одна программа: `python3 scripts/system.py [--root <company>] <operation>`. JSON stdout содержит фактический результат; exit2 и stderr — отказ, не PASS. Python3.12+, PyYAML6.0.1, Git2.43+; выбор ОС/первый запуск — adapters/README.md. Никаких shell-команд из YAML, собственного scheduler-сервиса или автоматически исполняемых migrations. Нужный метод выбирает агент; CLI выполняет точный известный шаг.

| Действие | Команда / нужный вход |
| --- | --- |
| Проверка перед работой | `preflight [--remote allowed-url-or-local-path] [--branch main]`: ready/blocked, local/remote SHA; только safe fast-forward, exit2 при blocked |
| Вход и проверка | `context [--task id]`, `validate`, `self-test` (изолированные known-bad примеры), `summary [--task id]` |
| Связанные описания правил | `documentation-check`, `documentation-render id`: read-only проверка/печать блока; декларация и границы — [system-evolution](system-evolution.md#связанные-описания-принятого-правила) |
| Принять сообщение | `intake id --request text --owner name --acceptance file.json [--confirmed] [--unknown text] [--input relative-path] [--independent-required] [--skill canonical-id] [--harness codex/claude] [--source-id accepted-id]` |
| Решение владельца | `decide id confirm/cancel/resume --revision N --reason text --actor owner` |
| Результат и проверка | `execute id --artifact result.json --critical critical.json --expected-revision N [--review review.json]` |
| Наблюдение применения | `observe id application/effect --evidence text --actor owner --expected-revision N --expected-result-sha256 HEX` |
| Источники / сверка | `source-read source-id`, `reconcile left.json right.json`, `episodes envelope.json --denominator N` |
| Проактивность / инцидент | `tick`, `incident id --observed text --assumption text --proposal text` |
| Знания | `search query`, `kb-read source-id` |
| Организация файлов | `organization-check --paths-file paths.json [--task id] [--expect snapshot.json]`: read-only место, карты, ссылки, structured consumers, immutable pins и exact pre/postimage; манифесты только внутри компании |
| Чистая компания | `create release.git new-directory --id company-id --owner owner` |
| Общая доставка | `prepare remote [new-candidate] [--task id] [--keep-candidate]`, `commit path... --message text`, `deliver remote --expected-base SHA [--task id] [--finish]` для сохранения readback у задачи |
| Временные копии | `candidate-status` читает только новые зарегистрированные попытки и причины удержания. Завершённые неизменённые копии закрываются при следующем prepare/update/rollback; старые копии не обходятся |
| Не начатая подготовка | `candidate-release ID` закрывает только собственную reserved-запись с отсутствующим каталогом и без связанной Git identity. Файлы не удаляются; для existing/partial каталога команда отказывает |
| Обновление | `update release.git new-candidate`, при принятом разрешении конфликта `finish-update --target SHA --company-base SHA`; потом тот же deliver |
| Восстановление | `rollback new-candidate`, `backup new-outside-directory`, `restore backup new-directory` |
| Обратный поток | `proposal sanitized-package new-output-directory`; `proposal-candidate package product-root product-remote new-candidate` для новых standards или точных hooks/manifest.yaml, hooks/README.md, scripts/hooks.py и checks; потом те же commit/deliver. Hook replacement сохраняет base/candidate hashes и требует maintainer review; donated code при подготовке не исполняется. Другие виды пакетов требуют явной связи потребителей у сопровождающего |

Все paths результатов/источников внутри компании относительны и без symlink обхода. Source material и самостоятельные сущности связать из ближайшей карты. ID 2–64 символа, `[a-z][a-z0-9-]...`. task.json — единственный текущий статус с revision/history, owner/request/acceptance_hash, pinned bindings, inputs/output/critical/evidence/errors/next_action. Статусы: draft/active/waiting/blocked/verified/cancelled. Доставка local/pending/delivered; application/effect unknown/observed отдельно. Повтор intake того же ID/смысла идемпотентен; другой смысл — конфликт, не перезапись. decide использует текущую revision.

Acceptance JSON до исполнения: `{"kind":"note"}` или `{"kind":"metrics","expected_count":2,"expected_total":5,"period":"agreed-period","unit":"agreed-unit"}`. Это учебный пример формата, не правило компании. Менять acceptance автора результата ради успеха запрещено. Result v1: company_id/kind/summary/next_action/limitations и sources `[ {path,sha256} ]`; metrics дополнительно complete=true, period/unit, records `[{id,value}]`, total. Проверяется оригинальная выгрузка, не только самосогласованный sum. Critical: request_alignment, counterexample, limitations[], references[]; смысловой проход остаётся assessment автора. При обязательной независимости review: reviewer не owner, verdict=pass, artifact_hash exact JSON; само наличие review поля не предоставляет host-изоляцию.

Sources: config.sources[id] содержит type/export path или HTTP endpoint или MCP command/read_tool/allowed_read_tools; approved_by/account/scope/period/unit, max_age_seconds/max_pages/timeout_seconds. Envelope v1: company_id/account/scope/period/unit/observed_at (UTC с timezone)/complete/records/next_cursor. Лимиты должны быть приняты для источника; пустое значение блокирует. HTTP GET без перенаправления; MCP stdio initialize/version/tools negotiation и только принятый read tool. Произвольные внешние writes не реализованы; неопределённый исход иной записи требует source readback до повтора.

Местное правило: company/standards/<id>.md c frontmatter id/version/owner и причиной/областью/проверкой; config.bindings[operation]={base,base_sha256,local,approved_by,reason,checks}. Загрузить local по binding; base hash change требует exact reconciliation. Неизвестное принятие не обходить. Версия выпуска — release.yaml; происхождение компании — .system/base.json с source/installed/version/previous.

Git delivery не очищает dirty tree и не включает чужие staged paths. Обычный push сохраняет конкуренцию; устаревший кандидат готовится заново/merge по владельцу, force не используется. Сведения получены из общего SHA — не live интеграция. Backup охватывает tracked разрешённые файлы/историю; ignored secrets и данные внешних систем не входят. Restore проверяет manifest, reconstructs Git files, не извлекает произвольный tar поверх живой компании.

Sanitized-package создаётся заново вне истории компании: approval.json имеет approved_by, permission=share-sanitized-method, purpose, files {relative-path:sha256}. Только standards/skills/workflows/checks. Владелец подтверждает обезличивание exact файлов; scanner не доказывает анонимность. Предложение не является принятым общим методом.

Рецепт workflow v1: id/version/owner/standards/capabilities/inputs/output/steps/on_error/stop. Steps имеют уникальный ID, operation из фиксированного каталога либо skill существующего канонического пакета, depends_on только предыдущих шагов. instruction задаёт агенту входы и условную ветку: просмотр не требует задачи результата, execute применяется один раз к подготовленному Result принятой задачи, successful deliver/readback завершает доставку. Вложенный навык использует уже выполненный вход задачи по task-validation. YAML читает агент; программного движка/автоматического вызова CLI по operation нет. Возможности не выдаются декларацией, ограничения профиля проверяются до действия.

operation описывает фазу, а не обязательно имя команды. author/research/marketing — подготовка агентом через разрешённые инструменты; таких subcommands system.py не предоставляет. organization использует read-only `organization-check`, а смысловое решение и разрешённые записи выполняет агент по принятой задаче. knowledge выбирает реальные search или kb-read. Остальные команды и их параметры — в таблице выше. Перед execute нужен Result v1/critical/обязательный review; транспортный envelope, receipt доставки и receipt инцидента не являются Result. Сохранение и простой просмотр явно разделены в соответствующих рецептах.

## Точные зависимости и совместимость

У канонических recipe `dependency_paths` явно объявляет helper/files; добавляются
SKILL/YAML, standards, вложенные skill, общее ядро, inputs и выбранные local
bindings. Известные kinds metrics/marketing/research/action-summary выбирают
свой recipe; custom kind требует explicit --skill или сохраняет полный набор.
Unknown/undeclared/ambiguous selection использует консервативные прежние bindings;
missing declared dependency блокирует. По свободному тексту зависимости не
угадываются. --harness закрепляет только выбранный adapter/profile; без него
выполнение остаётся общим. --source-id выбирает существующую принятую specification.

Task format1 сохраняет flat `bindings`. Additive `binding_scope` version1
содержит selection/paths/config facets/source_snapshots/sha256, тот же scope/hash
у evidence. Всегда закреплены identity/owner/schema и permissions; по recipe —
relevant local bindings, выбранные sources, marketing definitions/decisions,
knowledge/proactive features, выбранный harness. Release сравнивается по
совместимости форматов. Whole config/release hashes остаются у flat bindings
для старых readers; новые evidence могут обновить эти pins только при новом
успешном execute. Исторический admission содержит hash **одного** raw config
snapshot, полный выбранный source facet и точную specification projection;
pre-persistence recheck ловит редактор вне общего lock. Несвязанный source или
README другого harness не требует перевыполнения. Изменение selected метода,
input, accepted source или rights останавливает действие.

Новые scopes содержат facet `documentation`, включая пустой: только релевантные
регистрации, исходные значения, consumers и checker. Правило релевантно, если
consumer уже входит в выбранные пути или корень его source потребляется
выбранным config facet. Старый scope без этого facet пересчитывается прежним
алгоритмом и не дополняется; schemas/selection и исторические seals не меняются.
Правки ядра по-прежнему требуют сверки текущих активных привязок.

Старые задачи остаются full-bound. `decide ... resume` с owner/revision сохраняет
прежние bindings/scope/evidence в history и явно пересчитывает выбранный метод;
старый формат не сужается автоматически. Delivered admission проверяется по
сохранённым определениям/времени, без чтения нового provider.

## Хуки и экспорт

`hooks-check <event-or-scenario> [--payload file.json] [--task id]` — тот же fixed
dispatcher без native предпосылок. Поддержаны SessionStart/PreToolUse/PostToolUse/
PreCompact/Stop и шесть ID из hooks/manifest.yaml. `hooks-project codex|claude`
по умолчанию только preview; `--apply --enabled` допустим у адаптированной
компании с writable config и решением на native projection; handlers активны
только при отдельно принятом `hooks.enabled: true`. `hooks` optional, отсутствие означает выключено; `disabled`
содержит exact scenario IDs. Компания сама фиксирует включение/выключение.
Генератор не меняет provider trust. Current proof в .system/hooks-project-<harness>.json
и native settings доставляются только explicit paths; дедупликация disposable
в ignored cache. Неизвестная секция или отсутствующий proof даёт conflict.
После переноса root regenerate → trust-check, до этого native-unverified.

`export` optional: schema_version1, approved_by exact company owner, files[]
точных путей, roots[] разрешённых каталогов, binary_suffixes[], baseline null
либо approved full SHA. Это действующее разрешение компании, не общий allowlist.
External remote требует export **и** permissions.external_delivery/remotes;
отсутствие export сохраняет legacy local-only Git. Guard проверяет исходные
bytes всех достижимых новых trees/paths, modes и high-signal credential forms;
явный binary suffix не отключает secret scan. Baseline должен быть ancestor
всех новых commits; чужая merge history, missing/shallow/replace/graft/alternate
история блокируют. Exact baseline path/mode/blob — ранее approved content,
новый alias всё равно проверяется. Проверка не доказывает полную анонимность.

Commit готовит точный tree в owned scratch worktree и вызывает обычный Git
commit с действующими native hooks. Проверяются effective config/hooks,
produced tree/parent, затем branch CAS и exact index/readback; чужие staging и
working files сохраняются. Existing locks не удаляются; consumed/replaced
lock снимается только при прежней own inode identity. Split/sparse index,
relative hooksPath и несовпадающий conditional config явно blocked. Прерванное
index install после CAS остаётся visibly dirty: сохранить оба состояния и
согласовать их, автоматического reset нет. Deliver заново проверяет immutable candidate
после fetch и до normal push; false/missing native hook не отменяет guard.
Release структурно проверяет все refs с отдельным policy; company proof/data
и development documents туда не допускаются.

## Платформа и точные bytes

Один низкоуровневый stdlib platform_runtime → core → существующие operations. POSIX flock и Windows msvcrt nonblocking lock освобождаются при завершении процесса; lock files не удаляются. Windows и WSL не пишут один checkout одновременно. UTF-8/LF запись заменяет файл атомарно после file fsync; POSIX также fsync parent directory. Windows power-loss durability directory не обещается. Ошибка до replace сохраняет прежние bytes; после replace нужен readback до повтора.

Relative state paths всегда POSIX, unsafe Windows names и case/NFC collisions отклоняются без auto rename. Junction/reparse aliases не являются canonical источниками. Git attributes не нормализуют company/work/raw/evidence/native settings; LF объявлен только для общих методов. Локальные overrides диагностирует doctor; старые hashes/receipts не пересчитывать.

`doctor` читает prerequisites без PyYAML, не пишет и не синхронизирует. Setup отдельно получает закреплённые зависимости в ignored local runtime; повтор не требует загрузки. Источники — bootstrap.lock/requirements.txt; ограничения профилей — adapters. Native hooks имеют bounded process deadline, timeout завершает только их owned процесс, не фонового работника. Основные CLI проверки остаются обязательны.


Для обычного prepare-кандидата --finish завершает исходный checkout после remote readback; workspace_reconciled проверяется отдельно от status доставки. Default destination ignored; --task у prepare задаёт место в папке дела, --task у deliver допустим только для verified result и его acknowledgment. delivered-local-pending возвращает exit2, сохраняет readback и candidate; stage/reason_code/next_action указывают безопасное продолжение. Для Work сохраняется местный sync. Частично обновлённые bytes автоматически не перезаписываются при повторе.

### Хранение временных копий с 1.8.0

Новый prepare/update/rollback регистрирует принадлежащую CWS временную копию у исходного checkout, в ignored `.local/cws-candidates`. После полного `deliver --finish` её свидетельство сохраняется там же, отдельно от удаляемой `.git`. Перед созданием следующей копии CWS закрывает прежние подтверждённо завершённые, если их данные и Git-состояние не изменились. Последний успешный кандидат остаётся до следующей подготовки, поэтому обычный повтор finish по тому же пути сохраняется.

Кандидат временный даже при явно заданном пути. `prepare --keep-candidate` закрепляет намеренно сохраняемую копию. Чистая компания из create, постоянный backup и legacy-каталоги без нового marker в эту уборку не входят. Managed-кандидат нельзя использовать как общий источник objects/alternates для другого инструмента. Для постоянной работы используется исходная компания.

Неизвестный readback, незавершённое местное применение, unique Git objects, дополнительные worktrees, новые файлы, изменённые config/hooks, чужие locks и закреплённые копии сохраняются. Исторический success или возраст сами по себе не разрешают удаление. При четырёх незавершённых/сохраняемых новых копиях source создание следующей блокируется до завершения существующих; `candidate-status` показывает пути. Доставка и storage_status раздельны: подтверждённый результат остаётся доставленным, даже если последующая уборка невозможна.

Неизвестные или изменённые Git-admin файлы также сохраняются с момента регистрации; первый seal не разрешает стирать произвольные данные внутри `.git`. Отказ clone без созданного каталога можно повторить обычным prepare с тем же путём, remote, веткой и неизменной исходной версией: используется прежняя reservation под общим lock. Если исходное намерение изменилось, `candidate-release ID` освобождает только отсутствующую не связанную с Git попытку без удаления данных. Непроверенный partial clone сохраняется для разбора и не перезаписывается повтором.

Для принятого update/rollback CWS сохраняет точные bytes промежуточного index tree в той же исходной записи до изменения base.json. Git hash и каждый дочерний объект проверяются заново перед уборкой. Это компактное доказательство сохраняется после закрытия copy; произвольные dangling blobs/commits/trees таким способом автоматически не принимаются. Повреждённое доказательство удерживает candidate.

Проверяются также ignored-файлы. Только узкие воспроизводимые CWS/Python caches являются disposable; личные `.env`, произвольные `.local` и окружения не объявляются мусором. Запускать CLI кандидата готовым Python исходной компании: `/source/scripts/run.sh python /candidate/scripts/system.py --root /candidate <operation>`; в Windows тот же аргумент `python` у run.ps1. Так исполняется полученный код кандидата без установки ещё одной среды Python в каждой временной копии. Для update нужен origin исходной компании, указывающий на её общий рабочий Git. При отсутствии принятого origin witness завершения не угадывается, копия сохраняется.

Изменяющие CWS CLI-операции и Git entry points managed-кандидата используют общий внешний operation lock; sealing использует тот же lock. Read-only CLI validate занимает отдельный validation lock; уборка и её продолжение получают оба lock. Родитель preflight сохраняет operation lock до окончания дочерней проверки полученной версии. Самостоятельный validate не обещает атомарный снимок при параллельной записи и не заменяет проверки внутри сохранения или доставки.

Свидетельства и точные пути quarantine позволяют продолжить известный этап после сбоя. Изменённая или частично удалённая неизвестная quarantine сохраняется для разбора. Сторонние программы, обходящие эти locks, не становятся изолированными этим правилом; их неизвестные изменения блокируют уборку. Native поведение новой функции на конкретной ОС подтверждается отдельным прогоном.
