# Наблюдения после доставки

У каждой задачи три независимых слоя: `delivery` — Git-доставка с readback, `application` — свидетельство о применении, `effect` — свидетельство об эффекте. Verified artifact сам по себе не подтверждает последние два. Пока свидетельств нет, статус `unknown`; это не нулевой эффект.

Наблюдение записывает владелец компании для точного проверенного output. Перед ним агент читает task.json и проверяет содержание свидетельства: кто, когда, какой результат, в каком процессе, за какой период и на каком основании. Неизвестные KPI, период сравнения или причинность оставить в тексте неизвестными. Не приписывать результату применение только потому, что файл открыт или доставлен.

```sh
python3 scripts/system.py context --task first-summary
python3 - <<'PY'
import json
t = json.load(open('work/first-summary/task.json'))
print('revision:', t['revision'])
print('result sha256:', t['output']['sha256'])
print('result path:', t['output']['path'])
PY
```

После проверки владельцем подставить показанные revision и hash именно из `task.output.sha256`, не hash candidate/task.json. Например, свидетельство **отсутствия** применения:

```sh
python3 scripts/system.py observe first-summary application --expected-revision 3 --expected-result-sha256 REPLACE_WITH_OUTPUT_SHA256 --actor demo-owner --evidence "Владелец проверил рабочую встречу 10 октября: сводку не использовали, продолжили прежнюю таблицу. Причина пока неизвестна."
```

Число `3` и hash здесь только места для фактических токенов; не выполнять пример как готовую запись. После observe revision меняется. Перед следующим наблюдением снова прочитать задачу, убедиться, что output тот же, и получить его актуальные токены. Stale/missing/wrong hash или revision отвергаются без изменения задачи; нельзя автоматически освежать токены и повторять старое намерение.

| Фактическое свидетельство | Запись |
| --- | --- |
| Владелец подтвердил применение точной сводки на встрече | `application: observed`, evidence с процессом, датой и подтверждением |
| Владелец проверил и подтвердил, что её не применили | `application: observed`, evidence прямо говорит об отсутствии применения |
| Нет ответа от пользователей или подтверждения | Оставить `application: unknown`, не вызывать observe |
| Измерение показало рост времени подготовки после изменения | `effect: observed`, evidence с исходными величинами/периодом, отрицательным результатом и ограничениями причинности |
| Применение подтверждено, измерений эффекта ещё нет | `application: observed`, `effect: unknown` |

`observed` означает наличие свидетельства, а не положительный исход. Для отрицательного эффекта используется та же команда `observe ... effect` с новым актуальным revision/hash и честным evidence. Нового enum «negative» нет; знак, полнота и ограничения находятся в evidence. Учебные числа нельзя переносить в живые записи.

```sh
python3 scripts/system.py validate
python3 scripts/system.py commit work/first-summary/task.json --message "Record owner observation for exact result"
python3 scripts/system.py deliver /absolute/company-common.git --expected-base CURRENT_SHARED_SHA --task first-summary
python3 scripts/system.py summary
```

Сводка показывает отдельные delivery/application/effect и ссылку на output; сохранённая сводка отражает свой immutable snapshot, а не поздние наблюдения. Новый успешный результат сбрасывает application/effect в unknown, прежние свидетельства сохраняются в history и не переносятся на другой hash. Единственный текущий статус остаётся в task.json; второй журнал задач не создавать.
