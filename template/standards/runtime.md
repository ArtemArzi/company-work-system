---
id: runtime
version: 2
owner: product-maintainer
---
# Форматы и подготовленные команды

Для компании origin должен указывать на её общий операционный Git, а не release-only основу продукта. External URL допускается только по permissions.external_delivery/remotes; нового права preflight не выдаёт. При отсутствии общего remote сначала адаптация владельцем. Новая компания из create сохраняет release provenance отдельно; источник обновлений не является общей очередью сотрудников. Product adapter `python3 scripts/product.py preflight` использует ту же функцию для явно разрешённого репозитория продукта, без переноса этого права в компании.

Для сохраняемой action-summary: intake задачи сводки → `summary --task id` → сохранить JSON-кандидат → execute → validate → общий commit/deliver. Sources результата указывают на неизменяемый snapshot в work/id/inputs с наблюдёнными revisions/hashes исходных задач; текущие task.json не являются источником исторического результата. Без --task команда только показывает текущее состояние. Подмена snapshot отвергается общим hash-validator.

Наблюдения application/effect содержат evidence/actor/at/result_sha256 для точного output. Новый результат сбрасывает их в unknown; прежние подтверждения и доставка сохраняются в history. Подтверждение старой версии не переносится на новую.

Одна программа: `python3 scripts/system.py [--root <company>] <operation>`. JSON stdout содержит фактический результат; exit2 и stderr — отказ, не PASS. Python3.12+, PyYAML6.0.1, Git2.43+, Linux flock и relative symlinks. Никаких shell-команд из YAML, собственного scheduler-сервиса или автоматически исполняемых migrations. Нужный метод выбирает агент; CLI выполняет точный известный шаг.

| Действие | Команда / нужный вход |
| --- | --- |
| Проверка перед работой | `preflight [--remote allowed-url-or-local-path] [--branch main]`: ready/blocked, local/remote SHA; только safe fast-forward, exit2 при blocked |
| Вход и проверка | `context [--task id]`, `validate`, `self-test` (изолированные known-bad примеры), `summary [--task id]` |
| Принять сообщение | `intake id --request text --owner name --acceptance file.json [--confirmed] [--unknown text] [--input relative-path] [--independent-required]` |
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
| Обратный поток | `proposal sanitized-package new-output-directory`; `proposal-candidate package product-root product-remote new-candidate` для нового стандарта; потом те же commit/deliver. Изменение существующего метода или иной вид пакета требует явной связи потребителей у сопровождающего |

Все paths результатов/источников внутри компании относительны и без symlink обхода. Source material и самостоятельные сущности связать из ближайшей карты. ID 2–64 символа, `[a-z][a-z0-9-]...`. task.json — единственный текущий статус с revision/history, owner/request/acceptance_hash, pinned bindings, inputs/output/critical/evidence/errors/next_action. Статусы: draft/active/waiting/blocked/verified/cancelled. Доставка local/pending/delivered; application/effect unknown/observed отдельно. Повтор intake того же ID/смысла идемпотентен; другой смысл — конфликт, не перезапись. decide использует текущую revision.

Acceptance JSON до исполнения: `{"kind":"note"}` или `{"kind":"metrics","expected_count":2,"expected_total":5,"period":"agreed-period","unit":"agreed-unit"}`. Это учебный пример формата, не правило компании. Менять acceptance автора результата ради успеха запрещено. Result v1: company_id/kind/summary/next_action/limitations и sources `[ {path,sha256} ]`; metrics дополнительно complete=true, period/unit, records `[{id,value}]`, total. Проверяется оригинальная выгрузка, не только самосогласованный sum. Critical: request_alignment, counterexample, limitations[], references[]; смысловой проход остаётся assessment автора. При обязательной независимости review: reviewer не owner, verdict=pass, artifact_hash exact JSON; само наличие review поля не предоставляет host-изоляцию.

Sources: config.sources[id] содержит type/export path или HTTP endpoint или MCP command/read_tool/allowed_read_tools; approved_by/account/scope/period/unit, max_age_seconds/max_pages/timeout_seconds. Envelope v1: company_id/account/scope/period/unit/observed_at (UTC с timezone)/complete/records/next_cursor. Лимиты должны быть приняты для источника; пустое значение блокирует. HTTP GET без перенаправления; MCP stdio initialize/version/tools negotiation и только принятый read tool. Произвольные внешние writes не реализованы; неопределённый исход иной записи требует source readback до повтора.

Местное правило: company/standards/<id>.md c frontmatter id/version/owner и причиной/областью/проверкой; config.bindings[operation]={base,base_sha256,local,approved_by,reason,checks}. Загрузить local по binding; base hash change требует exact reconciliation. Неизвестное принятие не обходить. Версия выпуска — release.yaml; происхождение компании — .system/base.json с source/installed/version/previous.

Git delivery не очищает dirty tree и не включает чужие staged paths. Обычный push сохраняет конкуренцию; устаревший кандидат готовится заново/merge по владельцу, force не используется. Сведения получены из общего SHA — не live интеграция. Backup охватывает tracked разрешённые файлы/историю; ignored secrets и данные внешних систем не входят. Restore проверяет manifest, reconstructs Git files, не извлекает произвольный tar поверх живой компании.

Sanitized-package создаётся заново вне истории компании: approval.json имеет approved_by, permission=share-sanitized-method, purpose, files {relative-path:sha256}. Только standards/skills/workflows/checks. Владелец подтверждает обезличивание exact файлов; scanner не доказывает анонимность. Предложение не является принятым общим методом.

Рецепт workflow v1: id/version/owner/standards/capabilities/inputs/output/steps/on_error/stop. Steps имеют уникальный ID, operation из фиксированного каталога либо skill существующего канонического пакета, depends_on только предыдущих шагов. На каждом шаге агент применяет общий task-validation; full recipe не запускается автоматически по имени YAML. Возможности не выдаются декларацией, ограничения профиля проверяются до действия.
