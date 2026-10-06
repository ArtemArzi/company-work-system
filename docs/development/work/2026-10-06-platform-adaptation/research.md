# Основания адаптации, 06.10.2026

Baseline: product main 7b38b92cb0f792481b1a27652f31a4df13f457c2, preflight ready/current, clean tree, основа1.2.0. Пользователь подтвердил: адаптировать и доставить только продукт; клиентское обновление позже отдельно. Общее устройство и методы не исследуются заново.

## Внутренний аудит

Два read-only explorer рассмотрели независимые пути; автор плана объединил findings. Runtime: core.lock fcntl; write directory open/fsync после replace; history.Batch selector над stdout pipe; exact-root string comparison Git/Python; str(relative_to) в core/dependencies/lifecycle/validation/operations/knowledge; locale-dependent text IO; нет .gitattributes. Hooks: POSIX SIGALRM; единый shlex.join независимо от shell; backslash relative path selectors; validator требует24настоящих symlink даже для explicit read. Оба существующих профиля os Linux; native proof не переносится между ОС. Исходные source references: template/scripts/core.py:42–93,129; history.py:24,70,105,129–169,245; delivery.py:97,163–206,274; lifecycle.py:67,123–167; validation.py:334–363; hooks.py:85–92,162–179,258,322–335,451–466; checks/test_system.py:85–92,160,460,514; checks/test_history.py:88–139,194–323; checks/test_hooks.py:158–227. Внешние программы/таймеры не запускались explorer; fullsuite не повторялась.

Локально доступен Linux/WSL Python3.12.3/Git2.43.0. Read-only Windows probe: PowerShell5.1 и Git for Windows есть; python/python3 — WindowsApps alias, py отсутствует. Alias не запускать как доказанный Python и не открывать Store. macOS не доступна локально. Host/global config/credentials не менялись. Windows+WSL не должны одновременно писать в один checkout: разные OS lock backend не доказали взаимное владение; отдельные clones соединяет общий Git.

## Первичные внешние источники и выводы

- [Codex Windows](https://learn.chatgpt.com/docs/windows/windows-sandbox): native Windows поддерживается самим harness с PowerShell и sandbox; наличие такой поддержки не подтверждает переносимость нашего Python/Git-кода.
- [Codex WSL](https://learn.chatgpt.com/docs/windows/wsl): WSL2 выполняет Linux tooling, рекомендуется repo в Linux home, WSL1 не поддерживается новыми версиями. Windows mounted paths и native Windows — другие сценарии.
- [Codex skills](https://learn.chatgpt.com/docs/build-skills): repo discovery использует .agents/skills, разрешает symlink. Канонический SKILL может читаться явно; auto-discovery требует собственной проверки host. Копии метода не нужны.
- [Codex hooks](https://learn.chatgpt.com/docs/hooks): есть commandWindows override; session cwd не гарантирует корень; cloud orchestration не поддерживает обычные command hooks. Native shell/версию надо сверить, fallback общий CLI. Облачный профиль не равен desktop remote.
- [Claude setup](https://code.claude.com/docs/en/setup): Windows native/WSL/macOS/Linux; native shell может быть PowerShell, Git Bash — отдельный путь. Установка всех инструментов не требуется.
- [Claude AGENTS](https://code.claude.com/docs/en/memory): прямое чтение AGENTS.md с2.1.277, при отсутствии project/parent CLAUDE файлов; provider config может изменить это поведение. Один AGENTS сохраняется; при отключённой поддержке — явное чтение, не создание второго файла.
- [Claude hooks](https://code.claude.com/docs/en/hooks): Windows command hook принимает shell=powershell; native hooks требуют настроек и самостоятельного proof. Нельзя без проверки переносить Bash quoting в PowerShell.
- [Python3.12 msvcrt](https://docs.python.org/3.12/library/msvcrt.html): nonblocking byte-range lock LK_NBLCK, unlock LK_UNLCK; исключение при занятости. Рассмотрено вместо сторонней lock-библиотеки: достаточно одного backend, без новой зависимости. POSIX flock сохраняется.
- [Python select](https://docs.python.org/3/library/select.html): Windows selector работает с sockets, не subprocess pipes. Bounded reader thread/queue сохраняет streaming, deadline/framing и не читает весь blob.
- [Python os](https://docs.python.org/3.12/library/os.html): replace/fsync — разные гарантии. На Windows синхронизировать temp file, закрыть его, атомарно заменить; Unix directory fsync сохранить. Не заявлять power-loss durability Windows directory entry без собственного доказательства. Отказ после replace должен явно требовать readback, не слепой повтор.
- [Git attributes](https://git-scm.com/docs/gitattributes): eol=lf задаёт bytes текста при checkout. Новые исходники/документы будут UTF-8/LF; старые hash/filters не переписывать ради проверки. Старые bindings и backups требуют отдельного согласования при смене bytes.
- [GitHub standard runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners): standard Windows/macOS/Linux runners доступны публичным репозиториям бесплатно. [Manual workflows](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow): workflow_dispatch требует workflow на default branch и write permission. Новый CI — manual only, contents read, без секретов/таймеров/large runners. Если действующий доступ не допускает этот bounded test, сохранить workflow и явно отметить непроверенную ОС.

## Сравнение решений

Один runtime helper вместо отдельных систем по ОС; stdlib fcntl/msvcrt вместо установки полного стороннего пакета; один bounded pipe reader вместо трех Git scanners; explicit canonical skill read при отсутствии native projections вместо обязательного admin/DeveloperMode/дублирования SKILL. .gitattributes + явный UTF-8/LF вместо отключения пользовательских global filters. Thin OS entries в двух существующих harness profiles вместо дополнительной бизнес-памяти и схемы состояния.

Новые опасные имена/case/Unicode collisions проверять перед переносимым checkout/doctor; существующие данные не переименовывать автоматически. Native Windows/macOS runtime proof, actual provider instruction/skill/hook proof, shared Git, live business effect — отдельные уровни. Linux mocks не являются Windows/macOS execution.

## Первый запуск без Python

Пользователь явно добавил получение runtime dependencies. Выбран project-local bootstrap вместо global winget/brew/apt и собственного CPython installer для каждой ОС: фиксированный uv0.12.23, официальные release SHA256; uv получает CPython из Astral python-build-standalone, не обещаем binary от python.org. Один Python bootstrap и тонкие shell download launchers, локальные cache/runtime/.venv; no profile/registry/PATH writes. Git остаётся отдельной проверяемой предпосылкой, privileged installation не выдумывается.

Источники: [uv Python](https://docs.astral.sh/uv/guides/install-python/), [uv install](https://docs.astral.sh/uv/getting-started/installation/), [uv CLI](https://docs.astral.sh/uv/reference/cli/), [официальный release0.12.23](https://github.com/astral-sh/uv/releases/tag/0.12.23). --no-bin, --no-registry и install-dir подтверждены CLI reference; uv release/tag/digests проверены read-only GitHub API. Download/setup ещё не выполнен.
