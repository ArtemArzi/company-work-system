---
id: template-update
version: 3
owner: product-maintainer
---
# Общий выпуск и местное развитие

Продукт выпускает только split template history в отдельный release-only repo. Clone companies из product repo запрещён: даже чистый HEAD может сохранять чужие refs/objects. Company/work/локальные решения/источники принадлежат компании. Источник основы и SHA находятся в .system/base.json; это происхождение установки, не второй статус задач.

Один Git-механизм для результата, обновления и предложения: отдельный кандидат от свежей общей версии → точные проверки → обычный fast-forward push → remote SHA/файлы readback. Грязную чужую работу не stage/reset. Stale candidate остановить/подготовить от новой базы; push отказ/timeout сверить перед повтором. Конфликт сохраняет обе работы; даже чистый merge не принимает спорный смысл. Внешний remote требует действительного разрешения и allowlist; local tests не доказывают GitHub доступ.

Update готовится отдельно с общим предком; merge new release сохраняет local delta. Проверять protected company/work и все local binding base hashes, потребителей/переименование/удаление и старые активные задачи. Активная задача с несовместимой версией останавливается до rebind, историческая проверка не переносится. Изменение основного стандарта/местного binding требует принятой сверки exact hash. Не назначать всем обновлениям auto-merge. Повтор по SHA не создаёт дубль.

Rollback — обратный кандидат управляемых методов, сохраняющий поздние задачи/настройки; не обещает отменить внешние данные. Backup/restore в новую папку с проверкой hashes; существующую папку не затирать. В общий продукт возвращается заново подготовленная разрешённая методика/чистый воспроизводимый пример по явному allowlist и approval с hashes. Не экспортировать историю/сырые файлы компании, даже удалённые secrets из истории. Предложение отдельно от принятого выпуска.

Update/rollback закрепляют исходный HEAD и точные tracked bytes company/work/native settings/proof до clone. У нового кандидата локально выставляется core.autocrlf=false; исходный и host Git config сохраняются. Полный состав и hashes сверяются до и после изменения методов. Если прежние working bytes отличаются от Git blob, сохранить обе копии и запросить reconciliation владельца; не нормализовать данные, не stage их автоматически и не пересчитывать старые receipts. Отказ phase=clone возникает до merge: finish-update к такому кандидату неприменим, после согласования нужен новый кандидат.

Хуки optional и default off. Их manifest/code являются частью общих методов,
а hooks.enabled/disabled, .system/hooks-project-<harness>.json и native settings
принадлежат компании. Update/rollback сохраняют эти settings/proof; они не
возвращаются в прежнее состояние вместе с методом. После изменения code/root
нужен exact re-project и проверка trust в среде. Missing/malformed/mismatched
proof — conflict, CLI fallback остаётся доступным. В tracked-only backup
native settings/proof попадут только после explicit commit разрешённых путей;
ignored dedup/trust не копируются и не являются необходимой памятью бизнеса.

Обратное предложение hooks: отдельно написанные обезличенные hooks/manifest.yaml, hooks/README.md, scripts/hooks.py и checks проходят exact owner hash approval и общий content/path guard. Компания, native settings, ownership proof и её Git history исключены. proposal-candidate готовит отдельную product копию; для точных hook replacements возвращает base/candidate hashes, исходный метод остаётся в Git base. Trusted structural validator не импортирует donated handler и не запускает donated tests. До maintainer review это предложение, не выпуск и не подтверждение нового native handler.

Backup schema1 сохраняется: exact tracked bytes в проверенном tar + Git bundle. Restore в новый каталог проверяет состав manifest/Git/tar, пути, modes и SHA; не выполняет extractall. Единственное допустимое различие представления — доказанный canonical skills symlink/точный Windows Git placeholder. Исторические задачи/receipts/settings остаются byte-exact; ignored runtime/cache не копируются. Восстановление старого выпуска не расширяет его прежнюю совместимость с ОС. Перенесённым native settings нужны re-project/trust; Git filters/EOL не разрешают пересчитать доказательства.
