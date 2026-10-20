---
name: company-deliver
description: Для разрешённого внутреннего результата, кандидата обновления или обезличенного
  предложения через единый Git-путь.
---
# Сохранить и доставить общую версию

Метод — [standards/template-update.md](../../standards/template-update.md). Читайте парный [workflow.yaml](workflow.yaml) для входа/выхода, зависимостей и остановки. Канонический корень определить по company/config.yaml; в проекции ссылки разрешать от skills/company-deliver.

Создать свежий отдельный candidate командой prepare; commit только explicit paths после validate; deliver normal fast-forward и remote readback. Нельзя reset чужую работу/force push/победить смысловой спор чистым merge. Для update/proposal сначала соответствующая подготовка; generic доставку не дублировать.

Успешный readback завершает доставку. Receipt не является новым Result; последующий execute для него не нужен.

Доставка не подтверждает применение и эффект. Для отдельного observe сохранить revision и output.sha256 при чтении task/context для наблюдения; передать `--expected-revision N --expected-result-sha256 HEX`. При конфликте сверить свидетельство с новой версией. `observed` не означает положительный эффект. Контракт — [runtime](../../standards/runtime.md).

Общий цикл и evidence — [task-validation](../../standards/task-validation.md). Неизвестное/локальное/проверенное/live/применение различать. Результат и продолжение сохраняются у work/<id>/task.json.


Для обычной компании prepare remote без destination выбирает новую ignored копию; --task ID размещает её в work/ID/.local. Явный путь внутри исходного checkout обязан быть ignored; существующие копии не удалять. Вернувшиеся path/base сохраняют выбранную копию и исходную общую версию. Выполнять работу и commit разрешённых paths в этой копии. У подготовленного этим способом кандидата deliver remote --expected-base BASE --task ID --finish подтверждает remote, включая acknowledgment, и отдельно завершает исходную чистую папку. Для ожидания/отказа без verified result не передавать --task в deliver.

workspace_reconciled=true означает завершение исходной копии. delivered-local-pending означает: отправка подтверждена, исходная папка ещё не согласована. Сохранить candidate и квитанцию, объяснить stage/reason_code/next_action; не повторять бизнес-работу или push вслепую. Повтор того же candidate завершает допустимый локальный шаг; partial checkout или новые правки требуют сверки, автоматического rollback нет. Старый произвольный clone без prepare witness автоматически не завершается.

Work со своим ownedGit использует штатный sync/finish_sync по местному профилю; общий --finish его не заменяет. Для update/proposal сохраняется их принятый маршрут. После полного завершения не запускать новый sync/fetch ради заключительного сообщения.
