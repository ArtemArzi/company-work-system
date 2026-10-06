# Полезные хуки и стандарт их создания

Статус: реализация1.2.0 проверена и принята независимо; product commit/push/bundle в работе. Native provider events/trust не подтверждены, host/Work не включены.
Общий прогресс и порядок: [PLAN](../../PLAN.md#следующий-этап-скорость-и-хуки).
Область: главный company-work-system, template и его проверенные release/update кандидаты. Work, глобальные host configs, сайт и сторонние плагины не изменять.

## Основания и границы

В обучении Красинского K046, S0205–S0208 (54:17–55:21): типичные ошибки поиска/дубли файлов/незавершённость и автоматическая проверка при операции. K204, S0953–S0954 (04:15:06–04:15:42): учитывать последствия для соседних команд и аналитики. Это идеи из ASR-расшифровки, без просмотра видео и готового технического протокола. Ниже — наш способ применения, не обещание эффекта лектора. Источник принадлежит Work knowledge и не включается в release компании.

Источники проверены 06.10.2026: [Codex hooks](https://learn.chatgpt.com/docs/hooks), [Claude Code hooks](https://code.claude.com/docs/en/hooks). Codex command/MCP hooks поддерживаются; prompt/agent handlers пропускаются. Выбираем локальные command handlers для общей основы. В Codex project config нужен trust; изменённое определение требует новой проверки доверия. PostToolUse не отменяет завершённую операцию. Claude PreToolUse command timeout сам по себе не блокирует инструмент. Значит, полномочия и обязательная преддоставочная проверка остаются внутри CLI, hook лишь дополнительный вызов той же проверки. Не добавлять молчаливый approve/rewrite к permission events.

Локально Codex CLI 0.160.0 обнаружен, features list показывает hooks stable true. Это наличие возможности, не активация product hooks. Текущие оба product profiles hooks не заявляют; Claude native execution ранее blocked OAuth. Документы providers могут обгонять установленную версию: native events/tool aliases/schema/trust проверять на каждой заявленной версии. Explicit-file-read и обычный CLI остаются работоспособными при выключенных, недоступных или недоверенных hooks.

## Один механизм

Новая каноническая декларация hooks/manifest.yaml содержит ID, version, owner, цель/наблюдаемый сбой, событие, применимые tool/path selectors, fixed handler ID, dependencies, severity, enabled policy, timeout, fallback и положительный/отрицательный сценарии. Manifest — данные, не произвольная shell-команда. Исполняемые операции только из allowlist scripts/hooks.py; он вызывает существующие core/validation/delivery helpers, без второго validator и Git пути. Адаптеры генерируют project-local .codex/hooks.json и принадлежащую продукту секцию .claude/settings.json из manifest; определения туда вручную не копировать. Генерация preview → merge exact owned IDs → проверка, не перезапись чужих settings. AGENTS.md остаётся главным входом, CLAUDE.md не создавать.

Собственность native секции доказывать стабильным product namespace в fixed dispatch arguments и точным event/matcher/command соответствием, а не наличием произвольного поля ID, которого provider может не поддерживать. Перед записью генератор сравнивает обнаруженную owned секцию с ожидаемой предыдущей проекцией; чужой обработчик, неоднозначный matcher/command или местное изменение дают preview/conflict без перезаписи. Сохранять порядок и остальные настройки. Генерация идемпотентна; повтор не добавляет копии. Native command содержит fingerprint канонического определения и исполняемых dependencies; изменение метода/manifest/handler меняет проекцию и требует штатной проверки доверия Codex. Одного trust hash неизменной командной строки недостаточно, если поменялся её скрипт. Dispatcher сверяет ожидаемый fingerprint с текущими bytes до проверок, mismatch даёт явный failure и CLI fallback. Машинно производное описание предыдущей проекции, если нужно для merge, не становится вторым источником метода/прав. Копирование в новый каталог требует проверенной регенерации путей, без зависимости от стартового cwd.

Один standards/hook-authoring.md определяет контракт; skill-authoring и существующий company-author SKILL/YAML дают маршрут создания/изменения hook как метода. Карты standards/skills/company docs указывают новые канонические пути; не создавать отдельный журнал хуков, новую систему задач, отдельные рецепты исполнения или второй PLAN. Активность/местные ограничения хранить у company/config; технические receipts/дедупликация производные, бизнес-состояние у work/<id>/task.json. Документы разработки не включать в template.

## Набор сценариев

| Hook / момент | Что делает | Режим и граница |
| --- | --- | --- |
| context-entry / SessionStart, resume, после compact | Даёт ссылки на карту, владельца дела, следующий шаг и уже полученную версию | Локально, без fetch/sync. Новая задача всё ещё получает один штатный preflight; событие сессии не заменяет начало задачи |
| entity-before-write / PreToolUse известной Edit/Write/apply_patch | Для распознанных новых стандартов/skills/workflows проверяет каноническое место и доказанный duplicate ID | Блокирует только конкретное нарушение общей проверки. Не угадывает смысл по имени документа; обычные новые бизнес-файлы разрешены |
| entity-after-write / PostToolUse | Для изменённой сущности проверяет metadata/ссылки и напоминает обновить соответствующую карту | Проверка только затронутого блока; неизвестный shell write покрывается финальным validate. Никакого полного suite после каждого инструмента |
| task-continuation / PreCompact и Stop основного агента | Проверяет наличие актуального next_action/evidence у явно выбранной задачи, напоминает сохранить наблюдаемый сбой | Информационный режим по умолчанию, без автозаписи transcript. Stop не делает push/review и не превращает каждое сообщение в новую задачу |
| method-impact / перед изменением выбранного метода и при завершении author/update | Показывает фактически затронутые задачи/процессы/блоки по dependency closure | Только имеющиеся явные связи; при неизвестном влиянии — open и консервативная проверка, не выдуманный список отделов |
| publication-check / штатный commit/deliver и распознаваемый вызов CLI | Переиспользует общий history/path guard из первой задачи | Авторитетный барьер внутри CLI; native hook дополнителен. Произвольный shell/MCP/direct push не считается полностью перехваченным |

Все сценарии реализуются и проверяются, но активация каждого native mapping зависит от доказанного host event. Если событие недоступно, тот же сценарий вызывается в общем цикле CLI или остаётся явно advisory; не заявлять native покрытие. Для новых классов записей нужны действительные права компании, их нельзя назначить manifest-ом. Эти hooks не запускают агентов, LLM, сеть, таймеры или Git синк.

## Протокол исполнения и защита от лишней работы

- Один dispatch на событие; нормализованный JSON stdin с ограничением размера, в stdout только event-specific ответ. Различать pass, advisory, proved violation и handler failure. Сбой/timeout hook не превращать в PASS; обязательный CLI check остаётся fail-closed до доставки. Объяснение короткое, с путём и способом продолжения.
- Канонический company root/identity проверять, cwd в подкаталоге допустим; не выбирать website/чужой Git через случайный ближайший repo. Tool input/paths/prompt являются недоверенными данными: никаких eval/shell=True/исполнения YAML, обхода путей, чтения секретов или transcript целиком.
- Предварительные tool checks распознают только доказанный формат действия; regex shell-команды не считать sandbox/security boundary. Native permission deny допустим для доказанного нарушения; allow/approve/изменение входа не выдавать. Действительные полномочия принадлежат host и CLI.
- Дедупликация по company/task/revision/definition hash и фактическому изменению зависимостей; receipt не разрешает пропустить обязательные проверки. Не использовать чат-текст как признак завершения. При отсутствии task context выводить только безопасную навигацию, не создавать задачу самовольно.
- Не запускать полный preflight при каждом событии; одна проверка remote на самостоятельную задачу и штатная свежесть перед конечной доставкой. Не добавлять повторные subagent reviews: размер приёмки по task-validation. Простой текст/маленькая правка не получает обязательный review из hook.
- Stop/compact/подагенты: не блокировать черновики, вопросы, ожидание владельца, отмену или остановку пользователя; исключить рекурсивный Stop, повторы одного замечания и повторный запуск после interrupt. Advisory максимум один раз на revision/сбой, повтор только при новом изменении. Обрабатывать native loop indicators, но сохранять собственный предел. Interrupt не запускает continuation.
- Дешёвый dispatch: proposed local budget 1 секунда, предупреждение 2 секунды, timeout максимум 3 секунды; baseline/p95 и фактический overhead сравниваются на тех же задачах. Полная история Git выполняется в штатной доставке отдельно, не в tool hook. Дорогой context/repository scan на каждое событие запрещён. Это технические пределы, не лимиты бизнеса.
- Одновременные root/subagent события не пишут в одно дело: hooks read-only по умолчанию; если в будущем нужен write, существующие lock/atomic revision checks и отдельное обоснование. Не хранить raw prompt/transcript/tool output в новых логах. Факт и исправление сбоя записывает агент у существующего владельца задачи.

## Создание человеком и развитие компанией

Человек говорит агенту: «Не создавай второй план» или «После нового стандарта проверь карту». Агент находит существующий check, фиксирует пример ошибки у задачи, готовит объявление/fixed handler/тест и объясняет, когда hook помогает и когда не действует. Новые права/экспорт/фоновые процессы требуют отдельного решения владельца. Технические шаги человек не программирует.

Изменённый hook проходит общий company-author → validate → необходимую по размеру независимую приёмку → обычную Git доставку. Новая основа включает канонический manifest/helpers/тонкие adapters в release allowlist и method bindings. Company update candidate сохраняет местные settings, disabled hooks, overrides и чужие native definitions; доверие не переносить и не подделывать. Изменённый hash требует предусмотренного host review, до него fallback действует. Rollback восстанавливает методы, не удаляет новые задачи или чужие настройки. Обратное предложение в основу содержит только разрешённые обезличенные definitions/fixed code/tests, без компании/host credentials/transcripts/history.

## Проверки и критерий завершения

1. Сначала схема и стандарт, затем fixed dispatcher и шесть сценариев; затем генераторы двух adapters, integration в карту/author/update/release, затем проверки native и полного пути. Зависит от принятого контракта dependency closure/history guard в первой задаче; допускается начать независимые payload fixtures/standard раньше кода интеграции.
2. Unit/CLI bad cases: неверные event/schema/tool input/oversize/path/cwd/company; дубликат и обычный документ; забытая карта; устаревший task revision; disabled/untrusted/missing script/handler crash/timeout; параллельные события; subagent/Stop/compact/interrupt повторы; прочие settings и staged bytes сохраняются.
3. Adapter fixtures проверяют stdin/stdout/exit code отдельно для Codex и Claude; это protocol tests. Native smoke в отдельной учебной компании должен доказать фактический event, matcher, доверие, скрипт и решение, включая negative/timeout/disabled. Только затем профиль получает native статус с version/evidence. Нет доступа Claude — записать blocker, не объявлять native проверенным и не обходить OAuth/trust. Полный CLI fallback обеих сред всё равно обязателен.
4. Copy → adapt → author hook → выполнить задачу → validate → одна scoped delivery → новая сессия → update двух компаний с local hooks → rollback/restore → обезличенное предложение. Сравнить overhead без/с hooks: одинаковые workload/repeats, число dispatch/check/fetch/push/review. Новые hooks не должны увеличивать число обязательных синков/независимых reviews; неисправный hook не должен терять бизнес-данные.
5. Приёмка всего изменения свежим независимым task_result_reviewer, не автором/plan acceptor; требуется фактический выигрыш первой задачи, корректное invalidation и доступное покрытие hooks. Native недоступность и live/provider/полевой эффект остаются явно отдельными. Результат: полный комплект механизма/стандарта/сценариев/тонких adapters и штатных обновлений, с честной матрицей статусов.

## Сделано, осталось, продолжение

Сделано: сверены два фрагмента обучения, официальный протокол обоих providers и нынешние ограничения профилей; выбран reuse общего CLI и command hooks. Проверено: product hooks ещё не включены, чужие конфигурации не изменены. Осталось: фактическая payload/native проверка и реализация. Продолжить с H1 после приёмки; не устанавливать Hookify/архивы или новый plugin lifecycle.

Независимая приёмка: company-work-system-performance-hooks-plan-pass-20261006-ca6552e-7d4f9c-r1. Свидетельство: [общий verdict двух задач](../2026-10-06-hooks/evidence/plan-review.json). Замечание о собственности native definitions исправлено: namespace, точное сравнение проекции и fingerprint методов; повторная проверка PASS. Уточнено reuse существующего release guard с раздельными policy profiles, повторная проверка PASS. Схемы A1 ещё не приняты, код и активация не выполнены.

### Ошибка сохранения плана

Наблюдение: первый scoped commit остановлен Git с `Author identity unknown`; ожидаемые семь документов остались staged, исходные байты сохранены. Причина: в текущей среде нет применимой author identity, хотя предыдущий product commit использовал технического автора Company Work System. Исправление: взять уже существующего технического автора из product HEAD и передать user.name/email только через одноразовые git -c, не менять local/global config. Проверка git var GIT_AUTHOR_IDENT с этими параметрами PASS. Конечный commit/push/readback фиксируется в ignored .local/performance-hooks-plan-delivery.json; отдельный повторный синк receipt не нужен.

### H1/H2: bounded реализация

A1 принят: company-work-system-a1-pass-20261006-9182c4f-source-snapshot-r1;
план/архитектура/общий task-validation сохраняются. Task Delivery implement,
native workflow без нового controller; входной product preflight root готов,
локальный 6ffe7f6 при remote 9182c4f, тот же проход переиспользован worker.

Сделано: canonical six-scenario manifest, fixed dispatcher, fingerprint
manifest/handler/helpers/методов и выбранного adapter, безопасный exact-owned
merge для двух project-local настроек, стандарт hook-authoring и честные
common-code/native-unverified профили. Состояние задачи не переносится в hook;
raw prompts/transcripts не записываются. Activation текущего продукта/Work,
global settings, login/trust bypass, version/commit/push не выполнялись.

Проверено на реальных shared helpers: `PYTHONPATH=checks python3 -m unittest -v
test_hooks`, 30/30 PASS, 6.888s. Python compile и diff whitespace PASS.
Полный вывод — [implementation-suite](evidence/implementation-suite.txt), exact
owned hashes/команда/границы — [implementation.json](evidence/implementation.json).
Первый общий проход 26/27 выявил ошибку: development slug с датой ошибочно
проверялся как company ID. Исправлен только product маршрут; повтор PASS.
Проверены два JSON протокола, native-флаг disabled/untrusted, malformed/oversize,
fingerprint helper change, denial/advisory, timeout, Stop/compact/subagent/
interrupt, revision/dedup, concurrent settings edit, maps и scopes/root paths.
Нативный host/trust эти fixtures не подтверждают.

Локальный overhead — [dispatch-overhead](evidence/dispatch-overhead.json), 1
warmup + 20 одинаковых повторов без/с dispatcher: context p95 20.782ms,
обычная note 19.451ms, method-impact 43.726ms; median overhead 18.270–21.047ms.
Внутри этого замера fetch/push/review/network=0. Измерен common-code, без
provider/process startup; это расход локальной проверки, не заявленное
ускорение host или бизнес-эффект. Промежуточные 2 parser и 5 изолированных
projection checks ранее PASS; окончательный проход supersedes их seam gap.

API: dispatch(root,harness,event,payload,task_id=None) принимает canonical
scenario либо supported native event; native=False — fallback независимо от
activation. method-impact дополнительно принимает явный paths batch и
показывает actual task bindings/declared recipe closure. project(root,harness,
apply=False,enabled=False) — preview; apply требует adapted company writable
config, product read-only. Оба adapters остаются native-unverified.

После полного прохода уточнены exact поля ownership receipt и фактическое
имя общего CLI `hooks-check`; 3 затронутые проверки PASS, 0.829s. Product CLI
`hooks-project codex` реально вернул preview/enabled=false/changed=false.
Отпечатки до/после и targeted evidence разделены в implementation.json.
Для обычной business note общая entity_kind распознаёт отсутствие сущности
до metadata scan; отдельный validator не создавался.

Persistence amendment принят: company-work-system-hooks-proof-plan-pass-20261006-
6ffe7f6-93b1. Единственный proof теперь tracked
`.system/hooks-project-<harness>.json`; это derived exact projection state, не
метод/право/trust. Legacy ignored receipt мигрирует только при отсутствии нового
proof, валидной схеме и exact actual groups. При новом proof legacy вообще не
читается. Malformed/mismatch/обрыв settings → proof дают detectable conflict
с сохранением bytes; namespace сам по себе не принимается. Continuation dedup
остаётся ignored. Clone/regeneration меняет абсолютный root, статус native
unverified сохраняется; release/proposal не получают company proof.

После amendment: `PYTHONPATH=checks python3 -m unittest -v test_hooks`, 35/35 PASS,
7.590s. Exact hashes/границы — [implementation-proof](evidence/implementation-proof.json).
Проверены migration/precedence, malformed/mismatch preservation, interruption
и relocation. PostToolUse теперь читает actual file через shared validator:
payload записи не доказывает реально записанные metadata. При добавлении
interruption fixture обнаружен NameError из-за переноса строки соседнего теста;
исправлено размещение assertion, targeted 5/5 и общий 35/35 PASS. Contract и
проверки не ослаблены. Runtime code compile и whitespace PASS.

Root продолжает полноценный clone/update/rollback/backup/restore и whole-result
приёмку; child не менял их modules, version, index, commits или внешний state.

Продолжение root: один common CLI вызов dispatch/project, выборочный validator,
ссылки карт/author/release/update; полный integration/suite и независимая
приёмка всего outcome остаются у root. Native smoke требует фактического
event/matcher/trust на каждой версии; Claude OAuth/access остаётся open.

### H2: отзыв права во время projection

Whole-result reviewer воспроизвёл ошибку: local_work отзывался во время final
definition_hash, но projection записывал settings/proof. Первое неверное
допущение — проверка writable config до lock достаточно защищает запись;
общий lock сам по себе не запрещает внешнюю правку файла. Принятый A1 уже
требует shared byte snapshot; новый validator/контракт прав не нужен.

Repair-forward в том же scope: config_snapshot под company lock; shared
config(writable=True) обязан описывать те же parsed/raw bytes. Config SHA
добавлен к CAS settings/proof/legacy. Final fingerprint вычисляется перед
byte CAS, проверка повторяется перед первой записью и перед отдельной записью
proof. После собственной settings write ожидаются точные bytes общего atomic
writer, без принятия последующей чужой правки. При config change возвращается
conflict; apply не меняет activation/trust. Если settings уже записаны, новый
proof не создаётся и повторный preview обнаруживает mismatch.

Предварительно 5/5 affected tests PASS, 0.729s: отзыв права на втором
definition_hash сохраняет exact прежние settings/proof; изменение config
после settings write не создаёт proof, повтор не принимает unknown section;
initial unauthorized, interruption и idempotence сохраняют поведение.
Полный focused hooks rerun ждёт join текущего root full-suite; после него
сохранить evidence и передать затронутую поправку прежнему result reviewer.

После root full-suite join: `PYTHONPATH=checks python3 -m unittest -v test_hooks`,
37/37 PASS, 7.849s; compile hooks/test_hooks и owned whitespace PASS.
[Authorization repair evidence](evidence/authorization-repair.json) фиксирует
exact owned/shared hashes, команду, контрпримеры и границы. Полный root suite
до окончательного объединения имел 118 tests и две отдельные не-hooks проблемы;
его текущий результат не объявлен PASS. Дальше root объединяет остальные
исправления и передаёт эту область прежнему whole-result reviewer для
targeted revalidation. Native/OAuth/trust остаются unverified.

### V: положительный обратный поток хуков

Observed rc1 gap: proposal allowlist исключал hooks/code, proposal-candidate поддерживал только new standards; negative proof test не доказывал весь обратный путь. Repair-forward accepted within H evolution: exact sanitized hooks/manifest.yaml, hooks/README.md, scripts/hooks.py/checks, owner approval hashes и общий history content/path guard. Содержимое approved files закреплено в памяти; перед export повторно проверены company config/permission и approval bytes. Canonical code routing literals не считаются company file contents, actual company ID/known credentials запрещены. Automated scan не доказывает полную анонимность; exact owner решение остаётся необходимым. Proof/settings/arbitrary scripts/company/history не входят.

Product candidate отдельный, replacements возвращают base/candidate hashes, существующая product base сохраняется в Git. Trusted current validator не импортирует donated handler и не выполняет tests; до maintainer review это только предложение. Positive+negative proposal-repair.txt2/2PASS20.588s, включая inert poisoned donated code, exact hook replacement и отказ settings/proof/company/arbitrary script. Native статус не повышался.

37hooks checks PASS7.849s authorization-repair.json; полный source suite и same result reviewer в работе. Общий dispatch overhead исторической серии18–21ms, без fetch/push/review/network; это common Python invocation, не native latency. New config CAS не добавляет синков/приёмок. Следующий шаг — join suite/reviewer, release1.2.0 и product delivery в разрешённом объёме.

Full candidate source suite:132/132PASS939.529s, exit0/source_unchanged=true, source hashes in performance/evidence/full-suite-final.json. Hooks37 cases and two-company proposal/persistence included. Await final independent verdict then release/delivery. Provider-native callbacks/trust/OAuth not claimed.

Итоговая независимая приёмка PASS: company-work-system-result-pass-20261006-6ffe7f6-r2, все80recorded sourcehashes совпали, repair_list пуст. Доставка продукта и реальный isolated artifact — следующий разрешённый шаг; настоящий Work/companies/native activation отдельно. Receipt: ../2026-10-06-performance/evidence/result-review-r2.json.
