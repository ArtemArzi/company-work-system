---
name: company-incident
description: 'Для воспроизводимой ошибки: первое неверное допущение, challenge research,
  малое исправление и повтор исходного теста.'
---
# Разобрать сбой и исправить причину

Метод — [standards/task-validation.md](../../standards/task-validation.md). Читайте парный [workflow.yaml](workflow.yaml) для входа/выхода, зависимостей и остановки. Канонический корень определить по company/config.yaml; в проекции ссылки разрешать от skills/company-incident.

incident сохраняет observed/assumption/proposal в исходной task.json, не в новом журнале. После двух попыток без новых фактов остановить подход. Требования/тесты не ослаблять. Метод развивается через разрешённое предложение/выпуск; изменение unknown правила компании адресовать owner.

Общий цикл и evidence — [task-validation](../../standards/task-validation.md). Неизвестное/локальное/проверенное/live/применение различать. Результат и продолжение сохраняются у work/<id>/task.json.
