# codex: Linux explicit-file-read

Единственный вход — [AGENTS.md](../../AGENTS.md). Второго файла инструкций нет. Установка глобальных настроек не требуется. Перед задачей явно прочитать AGENTS, config, canonical SKILL и парный YAML. Относительные ресурсы читать от skills/<name>, а не рассчитывать на current working directory.

[Матрица](profile.yaml) разделяет common-core, native execution/discovery и live. Новый Python subprocess доказывает восстановление File Memory, но не загрузку инструкций самой средой. Проекция — relative symlink, проверена только на заявленном Linux; Windows/cloud не допущены. Hooks не активированы; реальная изоляция требует host/provider.

Общий порядок начала рабочего прохода — preflight → перечитать обновлённые инструкции → context. Это выполнение агентом команды по AGENTS, не установленный SessionStart-hook или фоновая синхронизация. Отказы/сохранение локальной работы описаны один раз в task-validation.
