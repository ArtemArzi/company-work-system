---
id: hook-authoring
version: 1
owner: product-maintainer
---
# Создание и изменение хуков

Общий маршрут — [skill-authoring](skill-authoring.md) и company-author; цикл
проверки — [task-validation](task-validation.md). Сначала найти наблюдаемый сбой
и существующий check. Человек описывает правило обычными словами, агент
показывает конкретный хороший и плохой случай, применимость и ограничение.

Канонический [manifest](../hooks/manifest.yaml) schema_version=1 содержит ровно
context-entry, entity-before-write, entity-after-write, task-continuation,
method-impact, publication-check. Каждая запись: id/version/owner,
purpose/failure, events/tools/paths, fixed handler/dependencies, severity,
enabled/timeout/fallback, positive/negative. ID handler совпадает с ID сценария;
произвольный command, shell, eval, YAML execution запрещены. Добавление другого
сценария требует отдельного изменения fixed dispatcher, метода и проверок.

`scripts/hooks.py` вызывается подготовленным CLI или command hook. Нормализованный
JSON ограничен 256 KiB; stdin/stdout отдельно от task.json. Содержимое tool input
не исполняется и не сохраняется в журнал. Обычные paths ограничены явно заданным
root; cwd внутри подкаталога допустим. Product root узнаётся по template/config
и docs/development/PLAN, его владельцы остаются в docs/development/work. Новое
company business state в product не создаётся.

Product root дополнительно имеет `.git` directory и `scripts/product.py` как
маршрут разработки. Его projection только preview; `--apply` допускается у
адаптированной компании с действующими owner/local_work permissions.

PreToolUse распознаёт Write, Edit и framed apply_patch. Только доказанные
нарушения места/duplicate ID общей `validation.entity_changes` могут вернуть
permissionDecision=deny. Неполный patch не является доказанным новым metadata.
PostToolUse сообщает metadata/link/map проблемы и не отменяет действие.
Методические dependencies берутся из actual task.bindings и общего scoped
changed_bindings, без догадок об отделах. Publication hook только напоминает
об обязательном history/path guard внутри CLI; полный scanner на каждый tool
не запускается.

Общий `method-impact` также принимает `{"paths":["standards/method.md"]}`:
только канонические пути относительно явного root, без shell/внешних файлов.
Потребители recipes выводятся через общий dependency closure; undeclared
рецепт остаётся open. Бизнес-блоки без явной связи не назначаются.

Stop/PreCompact главного агента advisory: next_action/evidence и текущая revision
у явно выбранной задачи. Unknown task получает навигацию. Ни decision=block,
ни continue=false, ни автоматического нового turn. subagent, interrupt и
stop_hook_active пропускаются. Повтор напоминания ограничен task revision,
definition/dependency fingerprint и текущими continuation facts. Производный
cache использует общий lock/atomic write; он не является допуском к доставке.
Никаких raw prompts/transcripts/tool outputs в новых логах.

Native activation — явное company config `hooks.enabled: true`; отдельные ID
можно отключить `hooks.disabled`. Common CLI fallback работает независимо.
Preview проецирует event/matcher/command из канонического метода в project-local
`.codex/hooks.json` либо принадлежащие продукту группы `.claude/settings.json`.
Fixed argv содержит namespace `company-work-system/hooks/v1` и definition hash.
Сравнить всю предыдущую owned группу (включая matcher/event/command/timeout),
сохранить чужие настройки/порядок. Нет ожидаемой предыдущей проекции, смешанная
группа, местная правка или неизвестное совпадение — conflict, без записи.
Повторная генерация идемпотентна. Disabled projection удаляет только доказанную
старую owned секцию. Перенос каталога требует новой генерации.

Proof предыдущей проекции — единственный tracked company-owned
`.system/hooks-project-<harness>.json`, производный из exact generated native
групп. Он не определяет новый метод/право/trust, переносится штатным
clone/update/backup/restore и исключён из release/proposal. Continuation dedup
остаётся ignored cache. Legacy `.system/cache/hooks-project-<harness>.json`
читается только при отсутствии нового proof; миграция требует exact совпадения
actual групп и проверенной схемы. Mismatch/malformed дают conflict и сохраняют
все старые bytes; namespace сам по себе proof не заменяет. После успешной
миграции legacy больше не читается. Обрыв settings → proof write оставляет
обнаруживаемый mismatch; повтор не принимает неизвестную секцию автоматически.

Apply закрепляет raw config компании под общим lock и проверяет shared writable
контракт для той же версии: identity, owner и local_work. Непосредственно перед
settings write и отдельно перед proof write сравниваются config, settings,
proof и fingerprint методов. Изменение даёт conflict; отзыв права до первой
записи сохраняет прежние файлы. Изменение после settings write не создаёт новый
proof и оставляет обнаруживаемый конфликт для повторного preview. Сам apply
не меняет hooks.enabled или native trust.

Fingerprint включает manifest, fixed handler и исполняемые helpers/dependencies,
стандарты и выбранный adapter profile. Dispatcher сравнивает его до checks;
изменённые bytes дают явный handler failure и CLI fallback. Изменённая команда
требует штатной native trust проверки, обход/копирование trust запрещены.
Сбой, malformed payload, missing script или timeout не означают PASS.
Внутренний лимит 3 секунды; нет full preflight/repository scan/review на событие.

Для каждого изменения: проверить схему, хорошие и плохие cases, оба протокола
stdin/stdout/exit, коллизии/чужие settings, повтор/disabled, fingerprint code
change, task revision и root/subdirectory/foreign paths. Fixture доказывает
common-code/protocol; native статус требует реального host event/matcher/trust,
negative и timeout на указанной версии. До этого profile остаётся native-unverified.
OAuth/доступ исправляет владелец; проверка не предоставляет права.

Update/rollback сохраняют company activation/disabled/чужие definitions. Если
старое определение не совпадает или proof отсутствует, оставить conflict и
выполнить fallback; proof/cache не переносить в публичный release. Обратное предложение
содержит только разрешённый обезличенный метод/код/tests, без company tasks,
host credentials, transcripts или истории компании.
