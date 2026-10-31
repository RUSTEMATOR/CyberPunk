"""Texts of the interaction between players (docs/EPIC_PLAYERS.md). Everything here is seen by the player.

The world shows the others as if they lived next door: no years, no mail, no explanation of the mechanics.
`g` is the gender of the one who acted: "m" or "f"; `v(g, m, f)` picks the verb form.
"""

from __future__ import annotations

from app.bot.texts import esc
from app.bot.ui.style import ICONS as I
from app.bot.ui.style import header


def v(g: str, m: str, f: str) -> str:
    return f if g == "f" else m


BUTTON = "Свои"
TITLE = header("relations", "Свои")
INTRO = "Другие наёмники Найт-Сити. Всё, что ты сделаешь, они увидят, как только откроют окно."
EMPTY = "Других живых персонажей в городе пока нет."
LINE = "<b>{name}</b> · {district} · {roles}\n    связь: {label} ({bond})"
OFFERS_BUTTON = "Предложения ({n})"
BACK = "Назад"

CARD = header("relations", "{name}") + "\n{district} · {roles}\nСвязь: <b>{label}</b> ({bond}/{max})"
CARD_STATUS = I["eddies"] + " {eddies} эдди · ОД {ap}"
CARD_HELP = (
    "<i>Эдди</i> — перевод, {fee}% берёт посредник.\n"
    "<i>Выпить</i> — обоим настроение +{mood}, связь крепнет.\n"
    "<i>Словечко</i> — местный фиксер станет теплее к адресату.\n"
    "<i>Записка</i> — пара строк от тебя.\n"
    "<i>Время вместе</i> — тренировка, прогулка или покатушки: оба получаете одно и то же.\n"
    "<i>Поручение</i> — задание с наградой, которую ты отдаёшь вперёд.\n"
    "<i>Переспать, свидание, дуэль</i> — только если согласится."
)
BTN_GIFT = "Эдди"
BTN_DRINK = "Выпить · {ap} ОД"
BTN_VOUCH = "Словечко · {ap} ОД"
BTN_NOTE = "Записка"
BTN_HOOKUP = "Переспать"
BTN_DATE = "Свидание"
BTN_DUEL = "Дуэль"
BTN_CHILD = "Ребёнок вместе"
DONE_TODAY = " ✓"  # a pair action already done today (button suffix)

GIFT_TITLE = header("eddies", "Перевод: {name}")
GIFT_TEXT = "Сколько? Посредник берёт {fee}%. Сегодня ещё можно отправить {left} эдди. На счету {eddies}."
GIFT_AMOUNT = "{amount}"

NOTE_PROMPT = (
    header("note", "Записка: {name}") + "\n"
    "Напиши ответом, до {max} символов. Твоё сообщение бот удалит, записку получит адресат."
)
CANCEL = "Отмена"

DUEL_TITLE = header("fight", "Дуэль: {name}")
DUEL_TEXT = (
    "До первой крови: {stat} + {skill} + d10, у кого больше. Хром и перки считаются.\n"
    "Проигравший теряет до {dice} здоровья, но на ногах остаётся. Ставка уходит победителю.\n"
    "Выбери ставку. Дуэль состоится, если вызов примут."
)
DUEL_NO_STAKE = "Без ставки"
DUEL_STAKE = "{stake} эдди"

BLOCKED = {
    "away": ("Вы сейчас в разных городах. Выпить, провести время, свидание, ночь, дуэль, совместное дело и ребёнок — "
             "только когда оба на одном месте. Записки, деньги и поручительство доходят куда угодно."),
    "eddies": "Не хватает эдди.",
    "cap": "На сегодня хватит: за сутки можно отправить не больше {cap} эдди.",
    "today": "Сегодня вы это уже делали.",
    "notes": "Записок на сегодня хватит.",
    "jail": "Из камеры этого не сделать.",
    "gone": "Этого человека уже нет в городе.",
    "amount": "Такой суммы нет.",
    "empty": "Пустую записку не отправить.",
    "pending": "Предложение уже ждёт ответа.",
    "stake": "Такой ставки нет.",
}
NO_AP = "ОД кончились. Нажми «Прожить месяц»."

# what the sender sees
GIFT_DONE = I["money_in"] + " {name} получит <b>{net}</b> эдди. Посредник взял {fee}."
DRINK_DONE = I["afterlife"] + " Стакан, другой, разговор до закрытия. Настроение +{mood}."
VOUCH_DONE = I["fixer"] + " Ты замолвил{a} словечко перед фиксером. {name} это пригодится."
NOTE_DONE = I["note"] + " Записка ушла: {name} прочтёт её, как только откроет окно."
OFFER_SENT = {
    "hookup": I["heart"] + " Ты позвал{a} {name} к себе. Ответ придёт, когда {name} откроет окно.",
    "date": I["heart"] + " Ты позвал{a} {name} на свидание. Ответ придёт, когда {name} откроет окно.",
    "duel": I["fight"] + " Вызов брошен{stake}. Ответ придёт, когда {name} откроет окно.",
    "child": I["heart"] + " Разговор о ребёнке начат. Ответ придёт, когда {name} откроет окно.",
}
OFFER_STAKE = ", ставка {stake} эдди"
BOND_LINE = I["relations"] + " Связь: {name} — {label} ({bond})."
PING = '<a href="tg://user?id={tg}">{who}</a>, к тебе {what}. /play'
PING_WHAT = {
    "gift": "пришли эдди", "drink": "заглянули выпить", "vouch": "за тебя замолвили словечко",
    "note": "записка", "offer": "есть предложение", "reply": "пришёл ответ", "attack": "наведались с недобрым",
    "sold": "купили твой лот на рынке", "reinf": "зовут на помощь в бой, открой «Предложения»", "crew": "заглянули из банды: объявлен налёт, нужен твой бросок в /crew",
}

# offers waiting for the answer
OFFERS_TITLE = header("heart", "Предложения")
OFFERS_LEFT = "Ещё ждут ответа: {n}."
OFFER_TEXT = {
    "hookup": "<b>{name}</b> зовёт тебя к себе. Что будет дальше, останется за кадром.",
    "date": "<b>{name}</b> зовёт тебя на свидание: неон, лапша на набережной, разговор до утра.",
    "duel": "<b>{name}</b> вызывает тебя на дуэль до первой крови. {stake}",
    "child": "<b>{name}</b> хочет ребёнка вместе с тобой. Ребёнок будет в обеих семьях, каждый из вас платит половину расходов.",
}
OFFER_DUEL_STAKE = "Ставка: {stake} эдди."
OFFER_DUEL_FREE = "Без ставки, на интерес."
BTN_ACCEPT = "Согласиться"
BTN_DECLINE = "Отказать"
NO_OFFERS = "Предложений нет."

ANSWER_DONE = {
    "hookup": I["heart"] + " Ночь вышла долгой. Остальное останется между вами.",
    "date": I["heart"] + " Вечер удался: город на пару часов стал тише.",
    "child": I["heart"] + " Вы решились. Скоро в двух домах будет на одного человека больше: <b>{child}</b>.",
}
DUEL_WIN = I["fight"] + " Дуэль. Соперник: {name}. {mine} против {theirs}: <b>победа.</b>"
DUEL_LOSE = I["fight"] + " Дуэль. Соперник: {name}. {mine} против {theirs}: <b>поражение.</b>"
DECLINED = "Ты отказал{a}. {name} узнает об этом."
STALE = "Это предложение уже неактуально."

# what arrives (menu notices); `heir` adds a prefix: the letter was meant for an ancestor
HEIR_PREFIX = "Весточка для твоего предка: "
GOT_GIFT = "{name} перевёл{a} тебе {net} эдди."
GOT_DRINK = "{name} заглянул{a} к тебе выпить. Настроение +{mood}."
GOT_VOUCH = "{name} замолвил{a} за тебя словечко. {fixer} теперь к тебе теплее (+{n})."
GOT_VOUCH_NOFIXER = "{name} замолвил{a} за тебя словечко, но фиксера в твоём районе нет."
GOT_NOTE = "Записка от {name}: «{text}»"
GOT_OFFER = "{name} ждёт твоего ответа: «Отношения» → «Свои» → «Предложения»."
REPLY_YES = {
    "hookup": "{name} согласил{asy}. Ночь вышла долгой. Настроение +{mood}.",
    "date": "{name} согласил{asy} на свидание. Вечер удался. Настроение +{mood}.",
}
REPLY_DUEL_WON = "Дуэль. Соперник: {name}. Победа: +{stake} эдди, уличная репутация +{cred}."
REPLY_DUEL_LOST = "Дуэль. Соперник: {name}. Поражение: −{stake} эдди, здоровье −{hp}."
REPLY_DUEL_LOST_FREE = "Дуэль. Соперник: {name}. Поражение: здоровье −{hp}."
REPLY_DUEL_WON_FREE = "Дуэль. Соперник: {name}. Победа: уличная репутация +{cred}."
REPLY_NO = "{name} отказал{a}: «{offer}»."
REPLY_EXPIRED = "Ответа на «{offer}» так и не пришло."
OFFER_NAMES = {"hookup": "переспать", "date": "свидание", "duel": "дуэль", "child": "ребёнок вместе"}
REPLY_CHILD = "{name} соглашается: у вас ребёнок — <b>{child}</b>. Теперь у него два дома."


def a(g: str) -> str:
    """The past-tense ending: "" for him, "а" for her."""
    return v(g, "", "а")


def asy(g: str) -> str:
    """согласился / согласилась."""
    return v(g, "ся", "ась")


def name(s: str) -> str:
    return f"<b>{esc(s)}</b>"


# -- hire: the merc exchange (phase 2) ---------------------------------------------------------------------------------

MERC_STATUS_ON = I["gigs"] + " Ты на бирже наёмников, доля {share}%. Тебя могут позвать в заказ."
MERC_STATUS_OFF = I["gigs"] + " Ты не на бирже наёмников."
BTN_MERC = "Биржа наёмников"
MERC_TITLE = header("gigs", "Биржа наёмников")
MERC_TEXT = (
    "Встанешь на биржу — другие смогут позвать тебя подмогой в заказ. Тебе ничего не нужно делать: "
    "ты прикрываешь их своим билдом, а потом получаешь долю оплаты и опыт.\n"
    "Доля — сколько процентов оплаты ты берёшь. Чем она выше, тем реже зовут."
)
MERC_NOW_ON = "Сейчас: <b>на бирже</b>, доля {share}%."
MERC_NOW_OFF = "Сейчас: <b>не на бирже</b>."
BTN_MERC_ON = "Встать на биржу"
BTN_MERC_OFF = "Уйти с биржи"
BTN_SHARE = "{share}%"
BTN_SHARE_ON = "• {share}%"

# on a gig brief
BTN_BACKUP = "Подмога"
BTN_BACKUP_SET = "Подмога: {name}"
HIRE_TITLE = header("relations", "Подмога")
HIRE_TEXT = (
    "Наёмник с биржи идёт с тобой. На каждой проверке он добавляет +{bonus}, а если в деле он сильнее тебя, "
    "то разницу. В громкой драке он берёт одного врага на себя. Заплатишь долю от оплаты, только если она будет."
)
HIRE_LINE = "<b>{name}</b> · доля {share}% · {skills}"
HIRE_NONE = "На бирже сейчас никого. Позови своих: «Отношения» → «Свои» → «Биржа наёмников»."
BTN_NO_BACKUP = "Без подмоги"
HIRE_PICKED = I["relations"] + " Подмога: <b>{name}</b>, доля {share}%."
HIRE_CLEARED = "Идёшь один."
HIRE_RESULT = I["relations"] + " Доля {name}: {cut} эдди."
HIRE_RESULT_NONE = I["relations"] + " Подмога: {name}. Оплаты нет, так что и доли нет."
GOT_HIRE = "{name} позвал{a} тебя подмогой: «{title}», итог «{outcome}». Твоя доля: {cut} эдди{xp}."
GOT_HIRE_XP = ", опыт ({skill}) +{n}"
HIRE_ASSIST = I["relations"] + " Подмога ({name}): +{n} к проверке, уже в итоге."


# -- time together and player quests (phase 1b) ------------------------------------------------------------------------

USERNAME = " (@{u})"
BTN_TOGETHER = "Время вместе · {ap} ОД"
BTN_QUEST = "Поручение"
TOGETHER_TITLE = header("relations", "Время вместе: {name}")
TOGETHER_TEXT = (
    "Раз в сутки, {ap} ОД. Платишь ты, получаете оба одно и то же.\n"
    "<i>Тренировка</i> — {train_cost} эдди, оба качаете навык, который ты выберешь (опыт +{train_xp}).\n"
    "<i>Прогулка по городу</i> — оба настроение +{walk_mood}.\n"
    "<i>Покататься</i> — оба настроение +{ride_mood}, вождение +{ride_xp}."
)
BTN_T_TRAIN = "Тренировка"
BTN_T_WALK = "Прогулка"
BTN_T_RIDE = "Покататься"
TOGETHER_SKILL = header("xp", "Тренировка вместе: {name}") + "\nКакой навык качаете?"
TOGETHER_DONE = {
    "train": I["xp"] + " Вы тренировались вместе: {skill}.",
    "walk": I["district"] + " Город на пару часов ваш: неон, уличная еда, разговоры ни о чём.",
    "ride": I["car"] + " Ночная трасса, открытые окна, город мимо.",
}
GOT_TOGETHER = {
    "train": "{name} позвал{a} тебя тренироваться: {skill} +{xp}, настроение +{mood}.",
    "walk": "{name} вытащил{a} тебя гулять по городу. Настроение +{mood}.",
    "ride": "{name} позвал{a} тебя покататься. Вождение +{xp}, настроение +{mood}.",
}

QUEST_TITLE = header("quest", "Поручение: {name}")
QUEST_TEXT = (
    "Что поручишь? Награду ты отдаёшь сразу, она лежит у посредника. Справится за {days} дн. — получит её. "
    "Откажется, не успеет или провалит — вернётся тебе."
)
QUEST_LINE = "{n}. {text}"
QUEST_REWARD_TITLE = header("quest", "Поручение: {name}") + "\n«{text}»\nКакая награда? На счету {eddies}."
QUEST_SENT = I["quest"] + " Поручение «{text}» отправлено, награда {reward} эдди отложена. Ждём ответа."
QUESTS_HEAD = "<b>Твои поручения</b>"
QUESTS_LINE = "• «{text}» от {name}: {reward} эдди, до {until}"
OFFER_QUEST = "<b>{name}</b> поручает тебе: «{text}». Награда {reward} эдди, срок {days} дн."
QUEST_TAKEN = I["quest"] + " Поручение принято: «{text}». Срок {days} дн."
GOT_QPAY = "Поручение «{text}» от {name} выполнено: +{eddies} эдди."
GOT_QDONE = "{name} выполнил{a} твоё поручение: «{text}»."
GOT_QFAIL = "Поручение «{text}» от {name} провалено: срок вышел."
REPLY_QUEST_YES = "{name} взял{asy} за поручение: «{text}»."
REPLY_QUEST_NO = "{name} отказал{asy} от поручения «{text}». {refund} эдди вернулись к тебе."
REPLY_QUEST_FAIL = "{name} не справил{asy} с поручением «{text}». {refund} эдди вернулись к тебе."
REPLY_QUEST_EXPIRED = "На поручение «{text}» так и не ответили. {refund} эдди вернулись к тебе."
OFFER_NAMES["quest"] = "поручение"
BLOCKED["quests"] = "У тебя уже {max} поручения. Сначала разберись с ними."


# -- crews (MMO part) --------------------------------------------------------------------------------------------------

BTN_CREW = "Банда"
CREW_TAG = " · «{crew}»"
CREW_TITLE = header("war", "Банды")
CREW_INTRO = (
    "Банда — это свои люди надолго: она переживает смерти персонажей. Внутри банды связь растёт вдвое быстрее, "
    "в рейде ты получаешь +{raid} к проверке, если кто-то из своих уже ударил сегодня, а общак пополняют все вместе. "
    "Вступить можно только по приглашению."
)
CREW_LIST_HEAD = "<b>Банды города</b>"
CREW_LIST_LINE = "«{name}» · {n} чел. · рейдов выиграно: {raids}"
CREW_LIST_EMPTY = "Банд пока нет. Будь первым."
BTN_FOUND = "Основать банду · {cost}"
FOUND_PROMPT = (
    header("war", "Название банды") + "\n"
    "Напиши название ответом: от {min} до {max} символов. Основание стоит {cost} эдди. Твоё сообщение бот удалит."
)
CREW_HEAD = header("war", "«{name}»")
CREW_LEADER = "Главарь: <b>{name}</b>"
CREW_BANK = I["eddies"] + " Общак: <b>{bank}</b> эдди"
CREW_RAIDS = I["legends"] + " Рейдов выиграно: {n}"
CREW_MEMBERS = "<b>Состав</b> ({n}/{max})"
CREW_MEMBER = "• {name}{leader}"
CREW_MEMBER_LEADER = " (главарь)"
CREW_LOG = "<b>Последнее</b>"
BTN_DEPOSIT = "В общак"
BTN_PAYOUT = "Выплата"
BTN_KICK = "Выгнать"
BTN_LEAVE = "Выйти из банды"
DEPOSIT_TITLE = header("eddies", "В общак «{name}»") + "\nСколько кладёшь? На счету {eddies}."
PICK_MEMBER = header("war", "{what}") + "\nКого?"
WHAT_PAYOUT = "Выплата из общака"
WHAT_KICK = "Выгнать из банды"
PAYOUT_AMOUNT = header("eddies", "Выплата: {name}") + "\nСколько? В общаке {bank}."
LEAVE_CONFIRM = "Точно уйти из «{name}»? Если ты последний, банда распадётся, а общак достанется тебе."
BTN_LEAVE_YES = "Да, уйти"
CREW_BLOCKED = {
    "in_crew": "Этот человек уже в банде.",
    "no_crew": "Ты не в банде.",
    "eddies": "Не хватает эдди.",
    "full": "В банде нет мест.",
    "name": "Название от {min} до {max} символов.",
    "leader": "Это может только главарь.",
    "bank": "В общаке столько нет.",
    "gone": "Этой банды или человека уже нет.",
}
CREW_FOUNDED = I["war"] + " Банда «{name}» основана. Зови своих: карточка игрока → «В банду»."
CREW_DEPOSITED = I["eddies"] + " В общак ушло {amount}."
CREW_PAID = I["eddies"] + " {name} получит {amount} из общака."
CREW_KICKED = "{name} больше не в банде."
CREW_LEFT = "Ты больше не в банде."
BTN_INVITE = "В банду"
OFFER_TEXT["crew"] = "<b>{name}</b> зовёт тебя в банду «{crew}». {stake}"
OFFER_SENT["crew"] = I["war"] + " Приглашение в банду ушло. Ответ придёт, когда {name} откроет окно."
OFFER_NAMES["crew"] = "вступить в банду"
OFFER_NAMES["corp"] = "работать в твоей корпорации"
REPLY_YES["crew"] = "{name} теперь в твоей банде «{crew}»."
CREW_JOINED = I["war"] + " Ты в банде «{crew}»."
GOT_CREWPAY = "Выплата из общака «{crew}»: +{eddies} эдди."
GOT_CREW_DISSOLVED = "Банда «{crew}» распалась. Общак, {eddies} эдди, остался тебе."
GOT_CREW_LEADER = "Ты теперь главарь банды «{crew}»."
GOT_CREW_KICKED = "Тебя выгнали из банды «{crew}»."
POST_FOUNDED = "🔥 В городе новая банда: <b>«{name}»</b>. Главарь — {who}."
POST_JOINED = "🔥 {who} теперь в банде <b>«{crew}»</b>."
BLOCKED["in_crew"] = "Этот человек уже в банде."
BLOCKED["bond_low"] = "Для ребёнка вместе нужна связь хотя бы «кореша»: проводите больше времени вместе."
BLOCKED["kids_max"] = "Детей уже столько, сколько можно вырастить."
BLOCKED["baby_soon"] = "Младший ещё совсем маленький: следующий ребёнок — через пару прожитых месяцев."


# -- pvp and the market ------------------------------------------------------------------------------------------------

BTN_ATTACK = "Напасть · {ap} ОД"
BTN_REVENGE = "Ответка · {ap} ОД"
ATTACK_TITLE = header("fight", "Нападение: {name}")
ATTACK_TEXT = (
    "Твой билд против билда цели: лучшее из (РЕФЛ + стрельба) и (ТЕЛО + ближний бой) + d10. У цели +{def_}: это её улица.\n"
    "<i>Ограбить</i> — заберёшь {pct}% эдди цели (не больше {cap}).\n"
    "<i>Проучить</i> — цель потеряет до {dice} здоровья, а ты получишь уличную репутацию.\n"
    "Никто не умирает. Проиграешь — сам потеряешь здоровье и получишь розыск. У цели будет {days} дней на бесплатную ответку."
)
ATTACK_REVENGE = I["fight"] + " Это ответка: без ожидания и защиты новичков."
BTN_ROB = "Ограбить"
BTN_BEAT = "Проучить"
ATTACK_WIN = I["fight"] + " {mine} против {theirs}: <b>победа.</b>"
ATTACK_LOSE = I["fight"] + " {mine} против {theirs}: <b>не вышло.</b>"
ATTACK_ROB_DONE = " Заберёшь {steal} эдди — сколько найдётся у цели."
ATTACK_BEAT_DONE = " Цель запомнит этот вечер."
PVP_BLOCKED = {
    "away": "Цель в другом городе. Напасть можно только на месте: сначала переезжай туда же («Мир»).",
    "pvp_off": "Ты вне стычек. Включи их в «Свои».",
    "pvp_target_off": "Этот человек вне стычек.",
    "pvp_newbie": "Новичков не трогают первые {weeks} недель жизни в городе.",
    "pvp_cooldown": "Стычка с ним уже была недавно. Между стычками с одним человеком {days} дня.",
    "pvp_recent": "После нападения выйти из стычек можно только через {days} дня.",
}
PVP_STATUS_ON = I["fight"] + " Стычки: <b>участвуешь</b>. На тебя могут напасть, ты тоже можешь."
PVP_STATUS_OFF = I["fight"] + " Стычки: <b>не участвуешь</b>."
BTN_PVP_OFF = "Выйти из стычек"
BTN_PVP_ON = "Участвовать в стычках"
GOT_ATTACK_ROB = "{name} ограбил{a} тебя: −{lost} эдди. Можно ответить бесплатно в течение {days} дней: карточка → «Ответка»."
GOT_ATTACK_BEAT = "{name} подкараулил{a} тебя: −{lost} здоровья. Можно ответить бесплатно в течение {days} дней: карточка → «Ответка»."
GOT_ATTACK_FAIL = "{name} пытал{asy} напасть на тебя и получил{a} отпор. Уличная репутация +{cred}. Ответка твоя в течение {days} дней."
GOT_REVENGE_ROB = "{name} вернул{a} должок: −{lost} эдди."
GOT_REVENGE_BEAT = "{name} вернул{a} должок: −{lost} здоровья."
GOT_REVENGE_FAIL = "{name} пытал{asy} отомстить и получил{a} отпор. Уличная репутация +{cred}."
POST_ATTACK = "⚔️ Стычка. Напали: {who}. Цель: {target}. {result}"
POST_RESULT = {"rob": "Ограбление удалось.", "beat": "Цель избита.", "fail": "Цель отбилась."}

BTN_MARKET = "Рынок"
MARKET_TITLE = header("shop", "Рынок")
MARKET_INTRO = "Оружие и броня от своих. Купленное сразу в инвентаре. Продавец получает деньги минус {fee}%."
MARKET_LINE = "{n}. <b>{item}</b> — {price} эдди · {seller}{mine}"
MARKET_MINE = " (твой лот: нажми, чтобы снять)"
MARKET_EMPTY = "Пока пусто. Выставь что-нибудь своё."
BTN_SELL = "Продать своё"
SELL_TITLE = header("shop", "Что продаёшь?")
SELL_NONE = "Продавать нечего: оружия и брони в инвентаре нет."
SELL_LINE = "{n}. {item}{eq} — в магазине {price}"
SELL_EQ = " (надето)"
PRICE_TITLE = header("shop", "{item}") + "\nЗа сколько? В магазине стоит {price}."
PRICE_BTN = "{pct}% · {price}"
MARKET_LISTED = I["shop"] + " {item} на рынке за {price} эдди."
MARKET_BOUGHT = I["shop"] + " Куплено: {item} за {price} эдди. Уже в инвентаре."
MARKET_WITHDRAWN = I["shop"] + " {item} снят с рынка и вернулся к тебе."
MARKET_BLOCKED = {
    "gone": "Этого лота уже нет.",
    "own": "Это твой лот.",
    "eddies": "Не хватает эдди.",
    "have": "Такая вещь у тебя уже есть.",
    "limit": "У тебя уже {max} лотов на рынке.",
    "price": "Такой цены нет.",
}
GOT_SOLD = "Рынок: {item} продан, +{eddies} эдди."


# -- echoes and joint deeds --------------------------------------------------------------------------------------------

BTN_STASH = "Тайник"
STASH_TITLE = header("property", "Тайник на будущее")
STASH_TEXT = (
    "Спрячь эдди в своём районе ({district}) и назначь, через сколько лет его можно найти. Найдёт первый, "
    "кто окажется здесь в тот год или позже: чужой персонаж или твой наследник. Ты сам его уже не откроешь."
)
STASH_MINE = "<b>Твои тайники</b>"
STASH_LINE = "• {district}: {eddies} эдди, откроется через {left} {years}{found}"
STASH_FOUND_MARK = " — найден"
STASH_PICK_AMOUNT = "Сколько прячешь? На счету {eddies}."
STASH_PICK_YEARS = header("property", "Тайник: {eddies} эдди") + "\nЧерез сколько лет его можно будет найти?"
BTN_YEARS = "{n} {years}"
STASH_HIDDEN = I["property"] + " Тайник спрятан: {eddies} эдди, {district}. Откроется через {n} {years}."
STASH_BLOCKED = {"limit": "У тебя уже {max} тайника. Пусть их сначала найдут.", "eddies": "Не хватает эдди.",
                 "gone": "Так не выйдет."}
STASH_FOUND = "Тайник! Записка внутри подписана: {name}. {eddies} эдди теперь твои."
GOT_STASH = "Твой тайник нашли: {name} забрал{a} {eddies} эдди."
GOT_STASH_OWN = "Твой тайник нашёл твой же наследник: {eddies} эдди вернулись в семью."
GOT_CAMEO = "Где-то в городе {name} встретил{a} тебя."

BTN_JOB = "Совместное дело"
JOB_TITLE = header("gigs", "Совместное дело: {name}")
JOB_TEXT = (
    "Ты выбираешь дело и свой подход, напарник — свой, когда примет. Оба бросают проверки: у обоих успех — полная "
    "оплата каждому и репутация, у одного — половина, у никого — раны и розыск. Разные подходы прикрывают друг друга: "
    "+{syn} к обеим проверкам."
)
JOB_LINE = "{n}. <b>{name}</b> — {pay} эдди каждому, сложность {dv}. <i>{text}</i>"
JOB_APPROACH_TITLE = header("gigs", "{job}") + "\nКак действуешь ты?"
JOB_APPROACHES = {"loud": "Громко", "quiet": "Тихо", "hack": "Взлом", "talk": "Уговорить"}
JOB_APPROACH_HELP = "Громко: РЕФЛ + стрельба. Тихо: РЕФЛ + скрытность. Взлом: ИНТ + нетраннинг. Уговорить: ХЛАД + убеждение."
OFFER_TEXT["job"] = "<b>{name}</b> зовёт тебя на дело «{job}»: {pay} эдди каждому. Сам идёт: {approach}. Выбери свой подход."
OFFER_SENT["job"] = I["gigs"] + " Предложение отправлено. Дело состоится, когда {name} выберет свой подход."
OFFER_NAMES["job"] = "совместное дело"
BTN_JOB_TAKE = "{approach}"
JOB_RESULT = {
    "full": I["ok"] + " <b>Дело сделано чисто.</b>",
    "half": I["danger"] + " <b>Сделали наполовину.</b>",
    "fail": I["fail"] + " <b>Дело сорвалось.</b>",
}
JOB_SCORES = "Ты: {mine} (нужно {dv}). Напарник: {theirs}.{syn}"
JOB_SYN = " Подходы разные: +{n} обоим."
REPLY_JOB = {
    "full": "Дело «{job}», напарник {name}: чисто. +{pay} эдди, уличная репутация +{cred}.",
    "half": "Дело «{job}», напарник {name}: наполовину. +{pay} эдди.",
    "fail": "Дело «{job}», напарник {name}: сорвалось. Розыск +{heat}, здоровье −{hp}.",
}


# -- the fixer's contracts (owner request: career paths) -----------------------------------------------------------------

OFFER_NAMES["contract"] = "заказ фиксера"
OFFER_CONTRACT = (
    "<b>{name}</b>, фиксер ({rank}), предлагает заказ «{job}».\n"
    "<i>{text}</i>\n\n"
    "План: <b>{approach}</b> ({check}), сложность {dv}, к броску +{bonus} за план.\n"
    "Куш {pool} эдди: фиксеру {fixer_pct}% ({fixer}), тебе {mine_pct}% (<b>{mine}</b>).\n"
    "Провал: здоровье и розыск."
)
BTN_CONTRACT_TAKE = "Беру"
CONTRACT_DONE = I["ok"] + " <b>Заказ выполнен.</b> Твоя доля: {mine} эдди."
CONTRACT_FAIL = I["fail"] + " <b>Заказ сорвался.</b>"
CONTRACT_SCORE = "Бросок: {total} при сложности {dv} (план дал +{bonus})."
REPLY_CONTRACT_OK = "Заказ «{job}» выполнен: {name}. Твоя доля {cut} эдди."
REPLY_CONTRACT_FAIL = "Заказ «{job}» сорвался у исполнителя {name}. Опыт фиксера всё равно капнул."
