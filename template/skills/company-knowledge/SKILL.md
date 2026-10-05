---
name: company-knowledge
description: Для поиска по алиасам/первичному материалу или принятой внешней KB; граф
  остаётся производным.
---
# Найти знание и связи с основаниями

Метод — [standards/system-evolution.md](../../standards/system-evolution.md). Читайте парный [workflow.yaml](workflow.yaml) для входа/выхода, зависимостей и остановки. Канонический корень определить по company/config.yaml; в проекции ссылки разрешать от skills/company-knowledge.

search пересобирает derived hash при изменении источника; causal relation не допускается. При graph off прямое чтение тех же источников. kb-read только если включён принятый connector; нет KB — использовать файлы, не строить второй источник решения.

Общий цикл и evidence — [task-validation](../../standards/task-validation.md). Неизвестное/локальное/проверенное/live/применение различать. Результат и продолжение сохраняются у work/<id>/task.json.
