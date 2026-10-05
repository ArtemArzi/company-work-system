---
name: company-deliver
description: Для разрешённого внутреннего результата, кандидата обновления или обезличенного
  предложения через единый Git-путь.
---
# Сохранить и доставить общую версию

Метод — [standards/template-update.md](../../standards/template-update.md). Читайте парный [workflow.yaml](workflow.yaml) для входа/выхода, зависимостей и остановки. Канонический корень определить по company/config.yaml; в проекции ссылки разрешать от skills/company-deliver.

Создать свежий отдельный candidate командой prepare; commit только explicit paths после validate; deliver normal fast-forward и remote readback. Нельзя reset чужую работу/force push/победить смысловой спор чистым merge. Для update/proposal сначала соответствующая подготовка; generic доставку не дублировать.

Успешный readback завершает доставку. Receipt не является новым Result; последующий execute для него не нужен.

Общий цикл и evidence — [task-validation](../../standards/task-validation.md). Неизвестное/локальное/проверенное/live/применение различать. Результат и продолжение сохраняются у work/<id>/task.json.
