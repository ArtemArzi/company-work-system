---
name: company-context
description: Для входа нового сотрудника/сеанса и поиска текущего владельца; читать
  File Memory независимо от recall.
---
# Найти компанию и актуальную работу

Метод — [standards/runtime.md](../../standards/runtime.md). Читайте парный [workflow.yaml](workflow.yaml) для входа/выхода, зависимостей и остановки. Канонический корень определить по company/config.yaml; в проекции ссылки разрешать от skills/company-context.

Запустить context; затем прочитать фактический task.json, config и выбранные ссылки. Воспоминание не откатывает новое решение. Если синхронизация недоступна, сообщить SHA доступного снимка.

Общий цикл и evidence — [task-validation](../../standards/task-validation.md). Неизвестное/локальное/проверенное/live/применение различать. Результат и продолжение сохраняются у work/<id>/task.json.
