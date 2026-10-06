---
name: company-author
description: 'Для обоснованной повторяемой операции: reuse, стандарт, SKILL/YAML,
  правильный и плохой пример, версия.'
---
# Создать или улучшить навык

Метод — [standards/skill-authoring.md](../../standards/skill-authoring.md). Читайте парный [workflow.yaml](workflow.yaml) для входа/выхода, зависимостей и остановки. Канонический корень определить по company/config.yaml; в проекции ссылки разрешать от skills/company-author.

Сначала поиск/reuse и критерии действующего стандарта. Следовать доступному skill-creator, короткий SKILL + парный recipe; версия/источники/ошибка/повтор. safe YAML, quick_validate, общий validator, behavioral test и разные среды. Не устанавливать архивный комплект.

Хук — такой же метод: [hook-authoring](../../standards/hook-authoring.md) →
[канонический manifest](../../hooks/manifest.yaml). Использовать существующий
fixed handler и общий validator. YAML не исполняет новые команды; новый handler
требует изменения кода и отдельной проверки. Сначала `hooks-check`, затем
`hooks-project codex` или `hooks-project claude`: preview без активации.
Для изменения native settings нужны действующее решение компании и проверка
trust в самой среде; чужие определения сохраняются при конфликте.

Общий цикл и evidence — [task-validation](../../standards/task-validation.md). Неизвестное/локальное/проверенное/live/применение различать. Результат и продолжение сохраняются у work/<id>/task.json.
