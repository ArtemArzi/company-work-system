---
id: runtime
version: 5
owner: product-maintainer
---
# Форматы и подготовленные команды

CLI вызывается через launcher выбранной [среды](../adapters/README.md). Примеры `python3 scripts/system.py <операция> ...` обозначают тот же CLI: в подготовленном локальном окружении заменить этот префикс на `sh scripts/run.sh` (POSIX) либо `& ./scripts/run.ps1` (PowerShell), сохранив операцию и аргументы. Глобальный python3, активация .venv и изменение PATH не требуются. Перед первой операцией агент выполняет setup, если окружение отсутствует; обычные команды сами зависимости не устанавливают.

Для компании origin должен указывать на её общий операционный Git, а не release-only основу продукта. External URL допускается только по permissions.external_delivery/remotes; нового права preflight не выдаёт. При отсутствии общего remote сначала адаптация владельцем. Новая компания из create сохраняет release provenance отдельно; источник обновлений не является общей очередью сотрудников. Product adapter `python3 scripts/product.py preflight` использует ту же функцию для явно разрешённого репозитория продукта, без переноса этого права в компании.

Для сохраняемой action-summary: intake задачи сводки → `summary --task id` → сохранить JSON-кандидат → execute → validate → общий commit/deliver. Sources результата указывают на неизменяемый snapshot в work/id/inputs с наблюдёнными revisions/hashes исходных задач; текущие task.json не являются источником исторического результата. Без --task команда только показывает текущее состояние. Подмена snapshot отвергается общим hash-validator.

Наблюдения application/effect содержат evidence/actor/at/result_sha256 для точного output. Новый результат сбрасывает их в unknown; прежние подтверждения и доставка сохраняются в history. Подтверждение старой версии не переносится на новую.

Одна программа: `python3 scripts/system.py [--root <company>] <operation>`. JSON stdout содержит фактический результат; exit2 и stderr — отказ, не PASS. Python3.12+, PyYAML6.0.1, Git2.43+; выбор ОС/первый запуск — adapters/README.md. Никаких shell-команд из YAML, собственного scheduler-сервиса или автоматически исполняемых migrations. Нужный метод выбирает агент; CLI выполняет точный известный шаг.

| Действие | Команда / нужный вход |
| --- | --- |
| Проверка перед работой | `preflight [--remote allowed-url-or-local-path] [--branch main]`: ready/blocked, local/remote SHA; только safe fast-forward, exit2 при blocked |
| Вход и проверка | `context [--task id]`, `validate`, `self-test` (изолированные known-bad примеры), `summary [--task id]` |
| Принять сообщение | `intake id --request text --owner name --acceptance file.json [--confirmed] [--unknown text] [--input relative-path] [--independent-required] [--skill canonical-id] [--harness codex/claude] [--source-id accepted-id]` |
| Решение владельца | `decide id confirm/cancel/resume --revision N --reason text --actor owner` |
| Результат и проверка | `execute id --artifact result.json --critical critical.json [--review review.json]` |
| Наблюдение применения | `observe id application/effect --evidence text --actor owner` |
| Источники / сверка | `source-read source-id`, `reconcile left.json right.json`, `episodes envelope.json --denominator N` |
| Проактивность / инцидент | `tick`, `incident id --observed text --assumption text --proposal text` |
| Знания | `search query`, `kb-read source-id` |
| Чистая компания | `create release.git new-directory --id company-id --owner owner` |
| Общая доставка | `prepare remote new-candidate`, `commit path... --message text`, `deliver remote --expected-base SHA [--task id]` для сохранения readback у задачи |
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

operation описывает фазу, а не обязательно имя команды. author/research/marketing — подготовка агентом через разрешённые инструменты; таких subcommands system.py не предоставляет. knowledge выбирает реальные search или kb-read. Остальные команды и их параметры — в таблице выше. Перед execute нужен Result v1/critical/обязательный review; транспортный envelope, receipt доставки и receipt инцидента не являются Result. Сохранение и простой просмотр явно разделены в соответствующих рецептах.

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
