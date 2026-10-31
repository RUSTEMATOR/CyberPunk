"""Player- and admin-facing texts of the narrator milestone (M8). Russian, short. Icons only through `app/bot/ui/style.py`.

Style: /tmp/claude-0/style_ru.md. Terms: /tmp/claude-0/terms_ru.md (corporations in Latin and not declined).
Telegram HTML; anything typed by a player or written by Claude goes through `esc` before it is put in.
The prompts for Claude are not here: they live in `app/narrator/prompts.py`.
"""

from __future__ import annotations

import html

from app.bot.ui.style import header

esc = html.escape

# -- budget notices to the admins ----------------------------------------------------------------------------------

BUDGET_NOTICE_WARN = (
    "Расход на Claude: {spent} из {limit} USD за месяц ({pct}%). "
    "На 90% бот перейдёт на заготовки и авторские тексты."
)
BUDGET_NOTICE_FALLBACK = (
    "Расход на Claude: {spent} из {limit} USD за месяц ({pct}%). Лимит выбран: до начала следующего месяца "
    "бот пишет заготовками и авторскими текстами. Игра идёт как обычно."
)

# -- /admin_budget -------------------------------------------------------------------------------------------------

BUDGET_TITLE = "<b>Бюджет Claude, {month}</b>"
BUDGET_TOTAL = "Потрачено: {spent} из {limit} USD ({pct}%)."
BUDGET_MODES = {
    "ok": "Режим: Claude пишет тексты.",
    "warn": "Режим: Claude пишет тексты, расход выше порога предупреждения.",
    "fallback": "Режим: заготовки и авторские тексты, Claude не вызывается.",
}
BUDGET_STATE_OFF = "Claude выключен: нет ключа ANTHROPIC_API_KEY или LLM_ENABLED=false. Тексты берутся из заготовок."
BUDGET_STATE_PAUSED = "Пауза после череды ошибок API: ещё {seconds} с заготовки, потом новая попытка."
BUDGET_NO_PRICES = "Цены в .env не заполнены (PRICE_NARRATIVE_IN и другие): расход считается нулевым, лимит не сработает."
BUDGET_KINDS_TITLE = "<b>По типам</b>"
BUDGET_KIND_ROW = "{kind}: вызовов {calls}, {cost} USD, вход {inp}, кэш {cache}, выход {out}"
BUDGET_EMPTY = "Вызовов в этом месяце ещё не было."
BUDGET_MODELS = "Модели: нарратив {narrative}, быстрая {fast}."

# -- the free action (PLAN 8.4) ----------------------------------------------------------------------------------------

FREE_PLAN = "<i>Твой план: {plan}</i>"

# -- the lineage note for the narrator (PLAN 8.7) -----------------------------------------------------------------------
# Built from the lineage rows, not written by Claude: it costs nothing and cannot invent a past.

LEGACY_HEAD = "Род {family}, жизнь по счёту {n}."
LEGACY_HOW = {
    "child": "Эта жизнь: ребёнок, наследник ({who}).",
    "relative": "Эта жизнь: родственник, наследник ({who}).",
    "protege": "Эта жизнь: протеже ({who}).",
    "engram": "Эта жизнь: энграмма ({who}).",
    "scratch": "Эта жизнь: с нуля, после ({who}).",
}
LEGACY_PAST_ONE = "Прошлая жизнь: {name}, {role}, умер в {age}, причина: {cause}."
LEGACY_PAST_MANY = "Прошлые жизни: {rows}."
LEGACY_PAST_ROW = "{name} ({role}, {age}, {cause})"

# -- local legends in the world history (PLAN 8.9) -----------------------------------------------------------------------
# Plain facts written by the code from the game's own data; Claude reads them later in the history digest.

LEGEND_DEATH_TITLE = "Смерть: {name}"
LEGEND_DEATH_TEXT = "{name}, {role}, умер в {age}, причина: {cause}. Уличная репутация {cred}, район {district}."
LEGEND_GIG_TITLE = "{name} закрыл заказ «{title}»"
LEGEND_GIG_TEXT = "Заказ {tier}-го уровня, район {district}: «{title}». Работа сделана, о ней ещё говорят."
LEGEND_DYNASTY_TITLE = "Династия {family}"
LEGEND_DYNASTY_TEXT = "Жизней в роду {family}: {n}. Район знает эту фамилию."

# -- a talk with an NPC (PLAN 8.6) -------------------------------------------------------------------------------------

CHAT_BUTTON = "Поговорить"
CHAT_HEAD = header("talk", "Разговор: {name}") + ", {role}"
CHAT_YOU = "<i>Ты:</i> {text}"
CHAT_NPC = "<b>{name}:</b> {text}"
CHAT_PROMPT = "Напиши реплику сообщением. Реплик осталось: {left}."
CHAT_WAIT = "<i>{name} думает над ответом...</i>"
CHAT_SILENT = "{name} молчит и смотрит в сторону. Скажи ещё раз."
CHAT_DONE = "Разговор окончен: больше {name} сегодня не скажет."
CHAT_OFF = "Сейчас поговорить не выйдет."
CHAT_END = "Закончить"
CHAT_TO_CARD = "К карточке"
