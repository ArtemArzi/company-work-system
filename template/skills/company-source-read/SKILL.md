---
name: company-source-read
description: 'Для чтения принятой выгрузки/API/MCP: проверить account/company/scope/период/полноту
  и сохранить основания.'
---
# Получить данные узким инструментом

Метод — [standards/data-access.md](../../standards/data-access.md). Читайте парный [workflow.yaml](workflow.yaml) для входа/выхода, зависимостей и остановки. Канонический корень определить по company/config.yaml; в проекции ссылки разрешать от skills/company-source-read.

Использовать только config.sources. read возвращает envelope; при отсутствии/неполноте/429/не той компании остановить зависимое. У новой записи после timeout исход unknown до provider readback; эта read-only программа writes не реализует. Для новой компании прежний account не переносится.

Для сохраняемого результата при подготовке намерения один раз прочитать task/context (или ответ intake/decide), сохранить revision и передать её в `execute --expected-revision N`. При конфликте перечитать контекст и согласовать кандидат с новым состоянием; подстановка последней revision в прежний кандидат запрещена. Точный контракт — [runtime](../../standards/runtime.md).

Общий цикл и evidence — [task-validation](../../standards/task-validation.md). Неизвестное/локальное/проверенное/live/применение различать. Результат и продолжение сохраняются у work/<id>/task.json.
