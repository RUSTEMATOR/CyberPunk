# PLAN_SCENARIST_BOT.md — сценарист внутри бота (запрос 27.10)

Владелец (скриншот регулярной задачи «Сегодня разбирать было нечего: пустой список dialogs»): «Не работает, давай
придумаем что-то иное, не scheduled Claude».

Было: бот складывал готовые разговоры и дела в data/dialogs/pending.json, регулярная задача Claude Code раз в 3 часа
читала файл и писала out-*.json. Задача видела другой файл или другое время, разборы не доходили.

Стало: бот разбирает сам, через тот же Claude API, что пишет нарратив (`MODEL_NARRATIVE`), без файлов и без задачи.

1. Джоб диалогов (каждые несколько минут): забрать исходы старых файлов (если ещё придут), собрать новые дела, потом
   `dialogs.prepare` — до `judge_per_run` готовых записей (разговор молчит `idle_min`, дела молчат `deeds_idle_min`):
   для каждой карточка персонажа, собеседник или список дел, меню событий.
2. Вызовы Claude — вне сессии базы (новый вид нарратора `scenarist`, ответ JSON: один исход по `OUT_FORMAT`, правила
   `balance.dialogs.rules` и пределы `limits` в данных). Таймаут 60 с, без повтора.
3. `dialogs.apply_judged`: тот же разбор, что был у файлов (`_apply`): пределы, событие только из меню, письмо игроку
   с последствиями и пинг. Заметки сценариста о недостающих механиках — в data/dialogs/notes.md.
4. Claude не ответил (нарратор выключен, бюджет, ошибка) — запись ждёт следующего прохода; после `judge_tries` неудач
   закрывается без последствий.
5. Файл pending.json больше не пишется (`file_dump: false`), регулярную задачу «исходы диалогов» можно удалить.

Код: `app/services/dialogs.py` (prepare, apply_judged, _apply), `app/scheduler/jobs.py` (dialogs_job),
`app/narrator/prompts.py` (`scenarist`), `schemas.NarratorKind`, `balance.narrator.max_tokens.scenarist`,
`balance.dialogs` (`bot_judge`, `judge_per_run`, `judge_tries`, `file_dump`).
