# Рабочая среда

Один метод — [AGENTS.md](../AGENTS.md), общие [skills](../skills/README.md) и CLI. Выберите [Codex](codex/profile.yaml) или [Claude Code](claude/profile.yaml). Установленные приложения и доступ к провайдерам проверяются отдельно от ОС.

| Среда | Первый запуск без Python | Обычная работа |
| --- | --- | --- |
| Windows без WSL | PowerShell: `& ./scripts/run.ps1 setup` | `& ./scripts/run.ps1 doctor`, затем `preflight` |
| Windows + WSL2 | В Linux-каталоге WSL: `sh scripts/run.sh setup` | `sh scripts/run.sh doctor`, затем `preflight` |
| macOS / Linux | `sh scripts/run.sh setup` | `sh scripts/run.sh doctor`, затем `preflight` |
| Codex Cloud | Проверить доступный Python/Git; если нужно, `sh scripts/run.sh setup` при разрешённой сети | Общий CLI, commits/PR; native command hooks недоступны при cloud orchestration |

Агент запускает setup при первом подключении, если окружение не готово. Он получает закреплённый uv, Python и PyYAML в `.local/runtime` / `.venv`; обычные команды ничего не скачивают. Источник и SHA uv — `scripts/bootstrap.lock`, Python — Astral python-build-standalone. Глобальные Python/PATH/registry/profile не меняются. Нет Git, сети, места или права — конкретный blocker; установка Git через штатный OS installer только в пределах действительных прав. Установка самого Codex/Claude и вход в аккаунт — отдельное действие владельца.

Windows и WSL используют отдельные clones; одновременная запись в один checkout запрещена. WSL1 не поддерживается. В WSL хранить clone в Linux home, а не `/mnt/c`. После переноса локальное окружение создаётся заново. `.local`/`.venv` не попадают в бизнес-память, Git или backup.

В Windows Git может заменить symlink файлом с точным текстом target. Validator принимает только доказанную canonical projection либо её отсутствие в explicit-read режиме; copied skill и изменённый target отклоняются. Агент читает `skills/<id>/SKILL.md` напрямую. Это не подтверждение автоматического поиска skills средой.

Claude Code >=2.1.277 может читать AGENTS.md непосредственно, если проект/родители не содержат перекрывающий CLAUDE.md или выключенный loader. При неизвестной версии/loader читать AGENTS.md явно. Второй файл инструкций не создавать. Настройки чужого проекта/домашнего каталога не менять ради обхода.

Хуки default off. Windows projection объявляет PowerShell явно; Codex Windows override запускает отдельный PowerShell command, Claude использует `shell: powershell`. Проекция привязана к точному root/interpreter/method hash; при переносе regenerate → проверка trust в среде. CLI остаётся основным путём. Native hooks/discovery, Cloud permissions и live accounts проверяются на реальном подключении и не следуют из теста ОС.

Первый программный проход: doctor → прочитать карту/config → один preflight → задача и её проверка → scoped commit/deliver по завершении. Частота и приёмка — [общий стандарт](../standards/task-validation.md). Форматы/операции — [runtime](../standards/runtime.md). Статусы проверок платформ приведены в двух profiles; кандидат не является готовым выпуском до приёмки.

Первичные источники: [Codex Windows](https://learn.chatgpt.com/docs/windows/windows-sandbox), [WSL](https://learn.chatgpt.com/docs/windows/wsl), [Codex hooks](https://learn.chatgpt.com/docs/hooks), [Claude setup](https://code.claude.com/docs/en/setup), [Claude memory](https://code.claude.com/docs/en/memory), [Claude hooks](https://code.claude.com/docs/en/hooks), [uv Python](https://docs.astral.sh/uv/guides/install-python/).

## Границы проверки версии 1.3.0

Общий runtime, установка без Python, повтор без сети и настоящий CLI проверены на Ubuntu24.04 x86_64, macOS14 ARM64 и WindowsServer2022 x86_64 (Actions37566264076, runtime1872bf7). В WSL2 Ubuntu24.04 x86_64 проверены настоящий CLI, установка, offline-повтор и затронутые исправления; полный запуск7f22 учитывается отдельно в доказательствах продукта. Windows10/11, другие версии ОС/архитектуры и облачные сессии не проверены этим запуском. Закреплённый архив для архитектуры сам по себе не означает проверенную поддержку.

Эти проверки относятся к общему runtime. Обнаружение навыков, trust/events хуков и права Codex/Claude требуют проверки в конкретной компании; они не подтверждаются тестом ОС.
