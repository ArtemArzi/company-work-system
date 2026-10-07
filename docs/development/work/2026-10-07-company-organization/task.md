# Company Organization и выпуск 1.3.1

Статус: кандидат реализации 1.3.1; targeted проверки и независимый behavioural
walkthrough пройдены, полный regression/release/result acceptance выполняются.
Владелец результата — продукт `company-work-system`.
Поручение владельца 07.10.2026: добавить навык Company Organization, тщательно
продумать его поведение и выпустить продукт 1.3.1 в `origin/main`. Установленный
Work и компании этим поручением не обновляются.

## Наблюдаемый результат

В основе 1.3.1 агент может по точному набору новых, изменённых, перемещённых или
удалённых файлов найти существующего владельца состояния, выбрать одно
каноническое место, обновить карты/ссылки/потребителей и проверить итог. Новый
навык не создаёт второй статус, не переносит неоднозначные или чужие файлы
молча и не смешивает организацию репозитория с Git-синхронизацией.

## Основания и управляющие документы

- `AGENTS.md`, `docs/development/PLAN.md`, `docs/development/RELEASE.md`.
- `template/standards/system-design.md`: один владелец, узкие контракты,
  малое проверенное изменение и восстановление.
- `template/standards/system-evolution.md`: канонические места, карты как ссылки,
  поиск потребителей при переносе и один текущий владелец.
- `template/standards/skill-authoring.md`: короткий SKILL, парный workflow,
  safe YAML, quick_validate и поведенческие проверки.
- `template/standards/task-validation.md` и `template/standards/template-update.md`:
  точные проверки, release-only пакет, отдельное применение к компаниям.
- Существующие `entity-before-write`, `entity-after-write` и `method-impact`
  остаются быстрыми локальными сигналами. Hook не запускает сеть, LLM, агента,
  Git-синк или автоматическое перемещение.

Внешнее исследование не требуется: контракт основан на текущем коде и принятых
методах продукта, не зависит от меняющегося внешнего API. MCP: not applicable.

## Выбранное решение

1. Добавить канонический `skills/company-organization/SKILL.md` и парный
   `workflow.yaml`, доступные через проекции Codex и Claude. Навык применяется
   к организации файлов и документов; обычный Git preflight/deliver остаётся у
   `company-context`/`company-deliver`, создание метода — у `company-author`.
2. Добавить read-only `organization-check --paths-file <json>` в общий CLI.
   Он принимает только точные канонические пути внутри company root и возвращает
   структурные факты: существование, распознанный тип, ближайшую карту,
   присутствие в обязательной карте, битые и входящие локальные ссылки,
   структурированные YAML/JSON consumers и отдельно immutable task/result pins.
   Обычному документу команда не придумывает владельца или место.
3. Семантический выбор выполняет агент по ближайшей карте и владельцу состояния:
   `keep`, `merge`, `move`, `link`, `create` или `leave-open`. Создание разрешено
   только когда существующего владельца нет. Перед move/delete обязательны
   входящие ссылки и потребители; неоднозначность остаётся предложением.
4. После изменения запускаются selective organization-check и полный `validate`;
   методы дополнительно проходят `method-impact`, навыки — quick_validate.
   Доставка точных путей остаётся отдельной операцией.
5. Выпуск повышается ровно до 1.3.1, отражается в `release.yaml` и истории README,
   собирается в новый изолированный release/bundle и отправляется обычным
   push `HEAD:main` с readback.

Серьёзная альтернатива — только текстовый SKILL без программного шва. Она проще,
но не даёт воспроизводимого оракула для путей, карт и ссылок. Автоматическое
перемещение прямо из hook отвергнуто: hook видит не весь бизнес-смысл и не должен
самостоятельно менять файлы.

## Контракты и границы

- Проверка не читает `.git`, `.local`, `.system`, runtime/projection каталоги,
  не следует по symlink/junction и не пишет файлы. Перед чтением каждого
  найденного документа, карты и structured manifest повторно применяется
  `core.path`/portable no-alias contract; alias-файл вне входного списка тоже
  не читается и возвращается как неполное покрытие.
- Вход — JSON-массив 1–128 уникальных относительных путей без `..`, абсолютных
  путей и выхода за company root. Отсутствующий путь допустим как сигнал
  move/delete; его входящие ссылки всё равно показываются.
- Markdown-ссылки разрешаются тем же правилом, что общий validator: внешние URL
  и anchors не считаются локальными файлами; выход из root и отсутствующая цель
  являются ошибкой.
- Обязательные карты сохраняются только для уже признанных самостоятельных
  сущностей. Для обычного документа ближайший README — контекст, а не
  автоматическое доказательство назначения.
- Move/merge/delete запрещён, если путь закреплён **другой или исторической**
  задачей в `task.inputs`, `bindings`, `binding_scope`, `Result.sources`,
  `output`, `evidence`, source admission или history. Старый файл, hash, receipt
  и history сохраняются byte-exact; их нельзя переписать ради нового места или
  зелёного validate. Неполная проверка Markdown/YAML/JSON/task consumers означает
  `leave-open`.
- Recipe объявляет реальные `dependency_paths`: organization helper, общий
  validator, runtime и system-evolution. Read-only inspection сначала формирует
  immutable `work/<id>/inputs/organization-snapshot.json`: точные target/map/
  consumer paths, исходные hashes либо `absent`, findings и полнота scan. Только
  этот snapshot становится task input; изменяемые live files не входят в inputs/
  bindings собственной organization task, а объявлены её output scope. Перед
  каждой записью preimage затрагиваемого пути сравнивается с исходным snapshot;
  после собственных правок live bytes сравниваются с ожидаемыми postimage
  hashes/`absent` точного change set, включая новые destinations, а нетронутые
  пути — с исходным snapshot. Concurrent drift даёт conflict. Местный
  `config.bindings.organization`, если
  он принят компанией, попадает в обычный operation facet. Несвязанные карты не
  входят в selected scope; legacy task остаётся на прежнем консервативном наборе.
- Новый operation `organization` является агентной фазой, не скрытой командой
  записи. `organization-check` — единственный новый CLI subcommand.
- Никаких изменений native hook manifest/settings/trust, company config,
  business state, прав, внешних источников или установленного Work.

## Проверки и оракулы

| Риск / требование | Проверка | Ожидаемый результат |
| --- | --- | --- |
| Новый стандарт/skill без карты | synthetic fixture + `organization-check` | точная обязательная карта и `mapped=false` |
| Move/delete ломает ссылки | отсутствующий target с двумя Markdown consumers | оба входящих consumer показаны, записи нет |
| Обычная заметка получает выдуманное место | обычный `.md` вне entity contract | `owner/placement=semantic-required`, без автоматического destination |
| Path traversal/absolute/symlink/duplicate | negative unit cases | команда отклоняет вход |
| Alias вне переданного paths попал в общий scan | внешний symlink/junction среди Markdown/YAML/JSON | содержимое не прочитано; coverage неполное, mutation остаётся `leave-open` |
| Закреплённый source/output хотят перенести | synthetic verified и delivered tasks с inputs/bindings/Result.sources/history | immutable pin показан; move/merge/delete не выполняется, hashes/receipts неизменны |
| Собственная organization task меняет target и карту | input содержит только immutable snapshot; live target/map объявлены output scope | target и карта изменены, Result проверен, snapshot byte-exact, concurrent drift отклонён |
| Selected dependency closure | scoped organization task с input/map/local binding и несвязанной картой | выбранное изменение блокирует; несвязанное не отменяет допуск; legacy остаётся full-bound |
| Проверка случайно пишет | hashes/tree до и после | bytes и Git status неизменны |
| Skill/recipe расходятся | quick_validate + repository validate | 13 skills, ссылки и проекции валидны |
| Поведение навыка | независимый isolated walkthrough пяти сценариев | reuse владельца без второго статуса; create+route только без владельца; safe move чинит consumers; ambiguity сохраняет bytes; повтор идемпотентен |
| Regression общего продукта | `python3 -m unittest discover -s checks -v` | полный suite PASS |
| Release содержит development/company data | реальный release + bundle mirror + чистая компания | только template history; 1.3.1 и skill доступны |
| Публичная доставка | push + exact remote readback | `origin/main` равен проверенному product commit |

Новый тест должен доказать оракул известным плохим примером: удалить ссылку на
skill из карты либо подать traversal и получить отказ; затем восстановленный
кандидат проходит. Поведение инструкций дополнительно проверяет свежий агент в
изолированных временных копиях: ему передаются только опубликованный SKILL,
сырой сценарий и разрешённые fixtures, без ожидаемого ответа. Root сравнивает
фактические diff/решения с пятью критериями строки выше. Локальные fixtures не
доказывают native skill discovery, реальное применение в компании или
бизнес-эффект.

## Порядок реализации

1. Зафиксировать tests для read-only inspection и нового skill contract.
2. Реализовать общий helper и CLI, переиспользуя `core.path`, `documents`,
   `entity_kind` и текущую логику ссылок без второго validator.
3. Добавить SKILL/YAML, проекции, карты и точные инструкции runtime/evolution.
4. Обновить версии, историю выпуска и документы разработки.
5. Выполнить targeted checks, quick_validate, template validate, полный suite и
   независимый isolated walkthrough; исправить относящиеся замечания.
6. Сделать локальный guarded commit, затем из чистого exact commit собрать
   release/bundle и проверить новую синтетическую компанию. Только после этих
   обязательных artifact evidence передать тот же exact commit свежему
   whole-result reviewer. Допустим предварительный code review, но PASS всего
   результата возможен лишь после release/bundle/company proof.
7. После итогового PASS выполнить обычный push `HEAD:main` и remote readback.

## Условия остановки и восстановление

Остановить зависимый шаг при расхождении `origin/main`, неясном праве на
перемещение конкретного файла, противоречии принятому system-evolution,
необъяснённой регрессии, неуспешной независимой приёмке или неоднозначном
release history. До push восстановление — удалить только новые bytes этого
кандидата или сделать новый исправляющий commit без reset/force. После push —
обычный исправляющий выпуск; историю не переписывать. Две попытки без новых
фактов прекращают текущий подход.

<!-- task-delivery:plan:start -->
```yaml
task-delivery:scope:
  include:
    - template/skills/company-organization/
    - template/.agents/skills/company-organization
    - template/.claude/skills/company-organization
    - template/scripts/organization.py
    - template/scripts/system.py
    - template/scripts/validation.py
    - template/scripts/dependencies.py
    - template/standards/system-evolution.md
    - template/standards/runtime.md
    - template/skills/README.md
    - template/hooks/README.md
    - template/README.md
    - template/release.yaml
    - checks/test_organization.py
    - checks/test_system.py
    - checks/test_platform.py
    - README.md
    - docs/development/README.md
    - docs/development/PLAN.md
    - docs/development/work/2026-10-07-company-organization/
  exclude:
    - company instances and Work installation
    - native hook settings, trust and activation
    - client data, credentials and external sources
    - global host configuration
```
<!-- task-delivery:plan:end -->

Независимая приёмка плана: первый кандидат REJECT
`company-organization-plan-reject-20261007-1ff3e1b2-r1`: добавлены immutable
task/result pins, behavioural oracle, alias-safe scan, exact dependency scope и
artifact-before-result-review. Второй кандидат REJECT
`company-organization-plan-reject-20261007-06706083-r2`: live targets ошибочно
были task inputs. Исправлено на immutable diagnostics/bytes snapshot плюс live
output scope и CAS; чужие/исторические pins сохраняются. Итог плана PASS
`company-organization-plan-pass-20261007-fa850066-r3`, 0 Critical/High/Medium.
После записи receipt текущий документ точечно перепроверен тем же plan reviewer:
PASS `company-organization-plan-pass-20261007-cf6e1164-r4`, 0
Critical/High/Medium. Итоговая независимая приёмка: pending; должна выполняться
другим свежим reviewer.

## Реализация и текущие доказательства

- Добавлены канонический skill/recipe и обе проекции; карта теперь содержит 13
  skills. Version source и корневой README подняты до 1.3.1.
- `organization-check` читает только внутренние small JSON manifests и
  канонические Markdown/YAML/JSON bytes; возвращает placement/maps, broken и
  inbound links, structured consumers, immutable task pins, exact preimages и
  coverage. `--expect` проверяет sealed snapshot, company config, consumers,
  preimages и неизменность полной границы scan.
- Targeted: 7/7 `test_organization` PASS; repository validate PASS
  (`entities=23`, `skills=13`, `tasks=0`); skill quick_validate PASS; py_compile
  и `git diff --check` PASS.
- Независимый isolated walkthrough прошёл пять сценариев: reuse существующего
  владельца без второго STATUS; create+route только при отсутствии владельца;
  move с правкой двух Markdown maps, YAML и JSON; ambiguity/alias сохраняет
  bytes; повтор не создаёт diff. Он обнаружил false-success `--expect` при новом
  alias после снимка. Первое неверное допущение: hashes наблюдавшихся consumers
  достаточны без закрепления самой границы scan. Исправление сравнивает coverage;
  прежний reproducer теперь exit 2 `organization inspection coverage changed`,
  regression включён в targeted 7/7.
- Первый полный sandbox run: 170 PASS, 1 opt-in skip, два environment-only
  отказа (localhost socket запрещён и PowerShell/WSL vsock недоступен). Вне
  sandbox полный suite прошёл; после последних scanner hardening правок точный
  финальный кандидат повторно прошёл 173 tests за 1181.983s, `OK (skipped=1)`.
  Единственный skip — явный opt-in real-network bootstrap; локальные Windows/WSL,
  localhost, release/update и все 7 organization cases выполнены.
