---
name: company-intake
description: 'Для разрешённого сообщения или транскрипта: сохранить исходный смысл,
  неизвестное и подтверждение поручения.'
---
# Принять сообщение в работу

Метод — [standards/task-validation.md](../../standards/task-validation.md). Читайте парный [workflow.yaml](workflow.yaml) для входа/выхода, зависимостей и остановки. Канонический корень определить по company/config.yaml; в проекции ссылки разрешать от skills/company-intake.

Сообщение не превращается в принятое решение автоматически. По исходному запросу подготовить acceptance, unknowns и owner; intake без подтверждения сохраняет waiting. Подтверждение/отмена — decide с текущей revision и основанием владельца. Не запускать массовый сбор переписки.

Ответ intake/decide содержит revision: сохранить её при подготовке следующего намерения и передать в execute через `--expected-revision N`. После конфликта требуется новое чтение и сверка намерения с состоянием, а не подмена revision. Контракт — [runtime](../../standards/runtime.md).

Общий цикл и evidence — [task-validation](../../standards/task-validation.md). Неизвестное/локальное/проверенное/live/применение различать. Результат и продолжение сохраняются у work/<id>/task.json.
