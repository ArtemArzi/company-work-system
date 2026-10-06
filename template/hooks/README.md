# Общие хуки

[manifest.yaml](manifest.yaml) — единственная декларация шести сценариев.
[Стандарт создания](../standards/hook-authoring.md) описывает договор;
[scripts/hooks.py](../scripts/hooks.py) содержит только подготовленные handlers.
Компания хранит решение `hooks.enabled` и `hooks.disabled` у
[config](../company/config.yaml). По умолчанию native hooks выключены.

Общий CLI `hooks-check` выполняет тот же сценарий при недоступном native
event. `hooks-project` показывает кандидат настроек; `--apply --enabled`
проецирует definitions после явного решения владельца. Trust проверяет сама
среда: генератор его не переносит. Установленные methods и новая директория
требуют повторной генерации абсолютных путей и native проверки.

Проекции `.codex/hooks.json` и `.claude/settings.json` не являются вторым
источником методов. Не редактировать продуктовую секцию вручную: отличающиеся
определения сохраняются и дают conflict. Чужие настройки сохраняются. Единственное
доказательство предыдущей проекции — tracked company-owned
`.system/hooks-project-<harness>.json`; оно переносится вместе с компанией и не
входит в release/proposal. Продолжение использует только ignored `.system/cache`.
Без proof существующая секция даёт conflict. Прежний ignored ownership receipt
может мигрировать только при отсутствии нового proof и exact совпадении native
групп; новый proof исключает чтение legacy. Неудача сохраняет старые bytes.
Обрыв между записью settings/proof выявляется повторным conflict.

Доставка и финальный validate обязательны независимо от hooks. Случайные Bash,
MCP writes, выключенные/untrusted hooks и native timeout не дают покрытие всех
записей. Hook не запускает сеть, синк, агентов, LLM, full suite или расписание.
