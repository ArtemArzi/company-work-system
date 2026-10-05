---
name: company-execute
description: 'Для принятой задачи с заранее заданным выходом: выполнить разрешённое
  действие и проверить exact artifact.'
---
# Выполнить и проверить задачу

Метод — [standards/task-validation.md](../../standards/task-validation.md). Читайте парный [workflow.yaml](workflow.yaml) для входа/выхода, зависимостей и остановки. Канонический корень определить по company/config.yaml; в проекции ссылки разрешать от skills/company-execute.

Подготовить результат с source hashes и критический проход с сопоставлением запроса, контрпримером/ограничениями. execute запускает общий validator и сохраняет failures у задачи. Требуемая независимость не заменяется собственным критиком. При changed dependencies остановиться до exact-version resume.

Общий цикл и evidence — [task-validation](../../standards/task-validation.md). Неизвестное/локальное/проверенное/live/применение различать. Результат и продолжение сохраняются у work/<id>/task.json.
