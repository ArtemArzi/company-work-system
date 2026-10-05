# Полная реализация шаблона компании

## Независимая проверка candidate 0f9fc0d: исправления в работе

Whole-result reviewer /root/complete_template_acceptance воспроизвёл три сбоя. (1) Prefix allowlist принимал standards/../company/private-note.txt: первое неверное допущение — строковый prefix определяет реальную категорию файла. Проверить канонический lexical path без .. до exact-file approval/copy. (2) Наблюдения применения/эффекта переходили на новый artifact: первое неверное допущение — evidence относится к задаче независимо от версии результата. Привязать к exact output hash, сохранить прошлое в history и сбросить при новом output. Proof /tmp/company-review-adversarial-8ydzu4e9. (3) Сводка включала свой mutable task.json и другие изменяемые задачи; после execute/global validate source hash менялся. Proof /tmp/company-summary-review-rxd8dd5a. Существующая стратегия immutable result/evidence hashes применима: локальный неизменяемый snapshot входов сводки у её задачи, без ослабления source hashes и без второго текущего состояния. CLI/recipe и исходный end-to-end проверяются вместе. Эти замечания требуют исправления реализации, исходный принятый план/критерии сохраняются.

Исправлено: общий safe-path отвергает lexical .././двойной separator до чтения/копирования; наблюдения привязаны к точному result_sha256, предыдущие output/observations/delivery/review остаются в history, новый результат unknown; summary --task создаёт content-addressed snapshot наблюдённых revisions/hashes и исключает себя. Ссылки/hashes сохраняют строгость; SKILL/YAML/CLI/runtime обновлены вместе. В tick используется helper под существующим lock, повторный flock устранён. Авторские 3 regressions PASS39.874s, исходный ticks PASS. Независимый reviewer повторил пять targeted cases, PASS50.692s и закрыл R1–R3; final SHA/release/full-suite acceptance pending. [Первоначальный verdict и исправления](evidence/result-review-initial.json). Документированные примеры ошибок сохранены, specification/accepted plan не ослаблены.

Общий план/прогресс — [PLAN](../../PLAN.md). Эта задача владеет деталями решений, проверками и ошибками реализации; второй PLAN/STATUS не создаётся.

## История P0 и начала реализации

P0: применимые инструкции/принятые документы прочитаны. Отдельный Git расположен в studio/products/company-work-system. Python3.12.3, PyYAML6.0.1, Git2.43.0; CodexCLI0.160.0. ClaudeCode2.1.289 обнаружен по /home/artem/.local/bin/claude, отсутствовавшему в PATH. На P0 модельный доступ ещё не был проверен; актуальное свидетельство см. «Полный путь P8 и native границы».

Whole-plan P0 task_plan_reviewer /root/implementation_plan_acceptance: первоначальный REJECT, после исправления PASS (company-work-system-p0-plan-pass-20261005-release-isolation-9a74). Проверяющий отдельно подтвердил чистые refs/history release-only repo и двух компаний, сохранение местных файлов при v2. Итоговый reviewer /root/complete_template_acceptance назначен отдельно; первые замечания и их исправления выше.

P1–P8 в работе: создан канонический template/ с общей картой AGENTS, пятью стандартами/контрактом данных, базовой конфигурацией и модулями core/validation/operations/connectors/knowledge/delivery/lifecycle/system. Реализуемый путь включает atomic file+lock, task cycle/evidence, Git-доставку, три read adapters, ticks, derived graph и оба потока. Библиотека навыков, бизнес-блоки, runtime справка и полный тестовый набор ещё не завершены. Наличие кода не означает принятую готовность.

## Наблюдавшаяся ошибка P0

- Сбой: первый опыт /tmp/company-p0-tht1okt2 сохранял продуктовый PLAN в refs/main и origin/main компаний, хотя HEAD был чистым.
- Первое неверное допущение: checkout split SHA очищает refs/историю clone.
- Исправление: отдельный release-only bare; clone/fetch компаний исключительно из него, контроль всех reachable refs/history/blob paths. Использован development-recovery, repair-forward; старый опыт не удалён.
- Проверка: /tmp/company-p0-repaired-5te5rbml; alpha/beta сохранили company/local.md и work/result.md при v2; product PLAN blob отсутствует в release, все достижимые пути чисты. Независимо воспроизведено plan reviewer, PASS.

## Последние решения владельца

Документы разработки перенесены в docs/development. Главный вход компании только AGENTS.md; CLAUDE.md удалён по прямому уточнению пользователя. Claude Code адаптация сохраняет explicit-file-read общей карты, без дублирующего файла инструкций. Native discovery/реальная новая сессия проверяются отдельно, не приписываются Python subprocess.

## Что осталось и как продолжать

Следующая разрешённая операция: завершить полный suite и свежую независимую итоговую приёмку; затем выпустить exact local candidate и проверить две копии из release-only repo. Библиотека, блоки и contracts завершены; замечания reviewer исправлять по их evidence. Git release-only выбор уже принят; research заново не проводить. Реальные аккаунты/фон/GitHub/бизнес-правила/поле остаются open. End-to-end/suite выполнены (см. ниже); итоговая независимая приёмка после material corrections ещё требуется.

## Проверки P1–P7 и текущие дефекты

Созданы восемь блоков, 12 пар SKILL/YAML, один message-to-action recipe, runtime справка и thin Linux adapter profiles по 14 возможностям. Штатный skill-creator quick_validate: 12/12 passed, [вывод](evidence/skill-validation.json). Общий validate: 21 entity/12 skills/0 tasks в чистой основе. Это структурные свидетельства, не полный выпуск.

Первый behavioral suite: 21 тест, 18 прошли; [полный вывод](evidence/tests-initial.txt). Два update tests отказали из-за empty-path root tree в `git rev-list --objects`; первое неверное допущение: каждый объект со space имеет непустой file path. Узкий reproducer вернул только root tree SHA с пустым именем. Исправлено распознавание root tree, строгий allowlist непустых paths сохранён. Повтор affected tests: 2 passed, evidence/update-tests-repaired.txt.

HTTP transport test внутри sandbox отказал до сервера: socket PermissionError. Причина — окружение, не source contract. Узкий запуск того же теста вне socket sandbox автоматически разрешён: 127.0.0.1 pagination/429/redirect rejection/cursor loop, 1 test passed. Никакого live аккаунта/внешнего запроса. MCP stdio, file export, known-bad result, state/lock, derived graph, ticks, common Git concurrency/readback/backup/proposal прошли первый прогон; после code corrections затронутое повторить.

Task Delivery controller сохранён degraded: owner перенёс единственный план; у released controller нет supported plan-path relocation. Старые state/pending obligations не переписаны. Implementation и native plan/result review продолжаются; controller verified completion не заявляется. Это служебная несовместимость и не блокирует разрешённую работу продукта.


## Полный путь P8 и native границы

Первый полный путь дошёл до rollback и отказал с NameError: subprocess не импортирован. [Сбой](evidence/full-path-first.txt), первое неверное допущение — reverse patch helper доступен в модуле. Добавлен отсутствующий import, исходный контракт сохранён. Повтор [полного пути и обратного потока](evidence/full-path-repaired.txt): 2 passed, копирование→задача→самопроверка→общая доставка/readback→новая версия→поздняя работа→rollback→backup/restore→новый subprocess; местное разрешённое обезличенное предложение доставлено в отдельный продуктовый кандидат тем же Git route, company history не импортирована. Локальные контрактные проверки после исправлений: 15 passed, evidence/local-contracts-repaired.txt.

[Native profiles](evidence/native-profiles.json): новый Codex read-only session выполнил реальные чтения пяти канонических файлов и вернул test-company/test-task/active/application unknown/точный следующий шаг. Первоначальный sandbox запрещал native app-server init; узкий read-only запуск разрешён auto-review. 45-секундный лимит прервал первый начатый model-read; более длинный bounded прогон завершён, exit0. В native каталоге было предупреждение о skill context budget; проверен explicit-file-read, auto discovery/full workflow не объявлены. Claude -p read-only permission profile: exit1, OAuth expired could not refresh. Никакого login, изменения credentials/settings или копирования memory не выполнялось.

Реальные company/API/GitHub/полевые действия не запускались; source exports и stdio/loopback transports проверяют программный контракт. Источники/лимиты/бюджет/правила конкретной компании остаются неизвестными до её адаптации. Настоящий native Claude walkthrough блокирует только expired session; общий локальный комплект продолжает проверку. Полный итоговый suite — evidence/tests-candidate.txt: 26 tests passed за149.718s, включая loopback HTTP в разрешённом окружении. Независимая complete-result acceptance pending.

После material corrections полный suite: 29/29 PASS182.165s, evidence/tests-review-repaired.txt; changed company-summary quick_validate PASS, чистый template validate21 entities/12 skills/0tasks. Следующий шаг — exact committed release-only/two-copy proof и финальный independent verdict.
