# claude: Linux explicit-file-read

Единственный вход — [AGENTS.md](../../AGENTS.md). Второго файла инструкций нет. Установка глобальных настроек не требуется. Перед задачей явно прочитать AGENTS, config, canonical SKILL и парный YAML. Относительные ресурсы читать от skills/<name>, а не рассчитывать на current working directory.

[Матрица](profile.yaml) разделяет common-core, native execution/discovery и live. Новый Python subprocess доказывает восстановление File Memory, но не загрузку инструкций самой средой. Проекция — relative symlink, проверена только на заявленном Linux; Windows/cloud не допущены. Hooks не активированы; реальная изоляция требует host/provider.

Частота входного preflight, выбор проверки и завершение доставки — [task-validation](../../standards/task-validation.md). Вложенные навыки и уточнения той же задачи используют уже полученную версию. Команду выполняет агент по AGENTS; hooks по умолчанию выключены, фоновая синхронизация не установлена.

Общий [manifest](../../hooks/manifest.yaml) и [стандарт](../../standards/hook-authoring.md)
дают шесть локальных сценариев. `hooks-project claude` показывает
project-local definitions; запись требует явных `--apply --enabled`, а native
исполнение — принятого company `hooks.enabled` и штатного доступа/trust.
Установленная секция проверяется по namespace и точному прежнему
event/matcher/command/timeout; местная правка сохраняется как conflict.
Абсолютные Linux paths не зависят от cwd; перенос требует новой проекции.

`hooks-check` — общий CLI fallback при выключенном/недоступном native событии.
Fixture tests проверяют JSON stdin/stdout и отказ, не native host. Матрица hooks
остаётся common-code-tested/native-unverified; PostToolUse advisory не отменяет
запись, Stop не создаёт новый turn, timeout не заменяет CLI checks.

Native OAuth ранее истёк; login выполняет владелец. Без него native event/matcher
и реальное решение среды не заявляются проверенными.

Tracked `.system/hooks-project-claude.json` хранит только exact generated
owned groups. После clone/update/restore command получает новый абсолютный root
и остаётся native-unverified до штатной проверки среды. Legacy ignored proof
мигрирует только по точному совпадению и не читается при наличии нового.
