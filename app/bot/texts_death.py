"""Player-facing strings of death and legacy (PLAN 7, DESIGN.md 6, 13): the zero-hp moment, the death screen, the
obituary, the legacy screen, the heirs, Secure Your Soul, /lineage and /graveyard.

Style: docs/style_ru.md, terms: docs/terms_ru.md (corporations in Latin, МаксТак, «Посмертие»). Strings that carry
names are used with `texts.esc` by the callers.
"""

from __future__ import annotations

from app.bot.ui.style import ICONS as I
from app.bot.ui.style import header

# -- zero hp ------------------------------------------------------------------------------------------------------------

ZH_EVAC = (
    I["trauma"] + " Trauma Team ({tier}) забирает тебя с асфальта. Счёт за эвакуацию: {price} эдди, что не покрыли наличные, "
    "стало долгом. Здоровье: {hp}."
)
ZH_EVAC_LATE = I["trauma"] + " Подписка Trauma Team ({tier}) оплачена, но бригада не доехала."
ZH_HEART = I["heart"] + " Сердце останавливается. Через секунду заводится запасное: Второе сердце выгорает, зато ты дышишь. Здоровье: {hp}."
ZH_PENALTY = " (штраф за повторные спасброски: −{n})"
ZH_SAVE_OK = I["ok"] + " Спасбросок: {roll} против {need}{penalty}. Ты лежишь при смерти, но дышишь. Здоровье: {hp}."
ZH_SAVE_FAIL = I["fail"] + " Спасбросок: {roll} против {need}{penalty}. Не вышло."
ZH_PENDING = I["heart"] + " Сердце не заводится. Остаётся один шанс: глюк."
ZH_GLITCH_OK = I["glitches"] + " Глюк сработал: {roll} против {need}. Тебя откачали. Здоровье: {hp}."
ZH_GLITCH_FAIL = I["glitches"] + " Глюк не помог: {roll} против {need}."

OFFER_TITLE = header("death", "Ты умираешь.")
OFFER_TEXT = (
    I["glitches"] + " Спасбросок провален: {roll} против {need}{penalty}. Можно потратить глюк и бросить ещё раз. "
    "Глюков у тебя: {glitches}. Один раз за жизнь."
)
BTN_OFFER_GLITCH = "Потратить глюк"
BTN_OFFER_END = "Принять конец"

# -- the death screen ---------------------------------------------------------------------------------------------------

DEATH_TITLE = header("death", "{name}: жизнь окончена.")
DEATH_CAUSE = I["obituary"] + " Причина: {cause}."
DEATH_FLAVOR: dict[str, str] = {
    "combat": "Тебя не стало в перестрелке.",
    "cyberpsychosis": "После срыва ты так и не вернулся.",
    "old_age": "Сердце остановилось во сне, без шума.",
    "corp_river": "Тело нашли рыбаки у дамбы. Компания выразила соболезнования и не явилась на похороны.",
    "radiation": "Доза с завода догнала тебя. Медкапсула была в двух часах полёта, на чужом небе.",
    "time_out": "Часы под кожей дошли до нуля. Тело осталось молодым, а жизнь кончилась на полушаге.",
    "collector": "Долг закрыли без тебя.",
    "prison": "Из камеры ты не вышел.",
    "overdose": "Последний брейнданс не отпустил.",
    "implant_failure": "Хром отказал, и тело его не удержало.",
    "wounds": "Раны оказались сильнее: тело на последнем пределе больше не держало ни одного удара.",
    "maxtac": "МаксТак закрыл дело.",
    "blackwall": "За Чёрным заслоном теперь звучит твой голос, а город стирает то, что от тебя осталось.",
    "own_will": "С этой жизнью покончено по твоей воле. Город заметил это не сразу.",
    "left_city": "Утренний автобус увёз тебя в Пустоши. Найт-Сити ты больше не увидишь.",
    "bounty_hunt": "Охотники брали тебя стаей и взяли. Чужой счёт пополнился на сумму, которую город назначил за твою голову.",
    "dgame": "Где-то на острове щёлкает табло: минус один игрок, плюс сто тысяч в копилку. Богачи за стеклом поднимают бокалы.",
    "legend_alive": "Ты уходишь живым, когда город стал мал. Имя остаётся в Зале легенд, а в барах ещё долго рассказывают истории, половина из которых правда.",
    "end_tower": "Башня Арасаки горит до утра. Тебя больше никто не видит, а на стенах Уотсона ещё год рисуют твоё лицо.",
    "end_nomads": "Колонна уходит в Пустоши на рассвете. Город ещё долго ждёт возвращения, а ты уже дома.",
    "end_orbit": "Челнок уходит в небо на столбе огня. Найт-Сити становится точкой, потом пропадает.",
    "end_mayor": "Твой портрет висит в каждом участке. Город ещё много лет живёт по твоим законам.",
    "possessed": "Она открывает твои глаза, разминает твои пальцы и выходит в город. Те, кто знал тебя, ещё долго будут спорить, кто из вас теперь отвечает на звонки.",
    "legend": "Над городом ещё горит то, что от тебя осталось. Тела не нашли. В барах спорят, правда ли всё кончилось, и пьют за тебя стоя.",
}
DEATH_BY = I["maxtac"] + " Тебя взял {hunter}, МаксТак."
DEATH_NOTE = I["legacy"] + " Некролог ушёл в общую беседу. Род продолжается, если ты решишь, кто его продолжит."
BTN_DEATH_NEXT = "Наследие"

# the duty hunt (a MaxTac player takes another player's fugitive)
DUTY_TARGET_KILLED = "{name}: цель не взята живой. Дело закрыто, тело уходит в морг."
DUTY_TARGET_DYING = "{name}: цель падает и не дышит. Её судьба решается без тебя."

# -- the obituary ---------------------------------------------------------------------------------------------------------

OBIT_HUNTER = "Дело закрыл охотник МаксТак: {hunter}."

ACHV_NONE = "то, что вообще начал"
ACHV: dict[str, str] = {
    "gigs": "заказов за жизнь: {n}",
    "fights": "выигранных схваток: {n}",
    "street_cred": "уличная репутация {n}",
    "fame": "слава {n}",
    "status": "статус {n}",
    "career_rank": "карьера, ранг {n}",
    "children": "дети: {n}",
    "years": "прожил {years}",
    "wealth": "{n} эдди за душой",
}

# -- /graveyard -----------------------------------------------------------------------------------------------------------

GRAVE_TITLE = header("grave", "Кладбище")
GRAVE_ROW = I["obituary"] + " <b>{name}</b>: {role}, {age}. Причина: {cause}. Уличная репутация: {cred}. {achv}"
GRAVE_ROW_BY = I["obituary"] + " <b>{name}</b>: {role}, {age}. Причина: {cause}, дело закрыл {hunter}. Уличная репутация: {cred}. {achv}"
GRAVE_ACHV = "Запомнят за: {achv}."
GRAVE_EMPTY = "Пока никто не лёг в землю. Город уверен, что это ненадолго."
NO_ROLE = "без роли"

# -- /lineage, the profile ----------------------------------------------------------------------------------------------

LINEAGE_TITLE = header("lineage", "Род {family}")
LINEAGE_COUNT = I["legacy"] + " Прожито жизней: {n}."
LINEAGE_GLITCH = I["glitches"] + " Бонус глюков за жизнь «с нуля»: +{n}."
LINEAGE_ROW_ALIVE = I["profile"] + " {n}. <b>{name}</b>, {role}, {age}. {how}. Жив."
LINEAGE_ROW_DEAD = I["obituary"] + " {n}. <b>{name}</b>, {role}, {age}. {how}. Погиб: {cause}. Уличная репутация: {cred}."
LINEAGE_MORE = "…и ещё жизней раньше: {n}."
LINEAGE_EMPTY = "Рода пока нет: он начинается с первого персонажа."
HOW: dict[str, str] = {
    "founder": "основатель рода",
    "child": "наследник, ребёнок",
    "relative": "наследник, родственник",
    "protege": "протеже предшественника",
    "engram": "энграмма предшественника",
    "scratch": "жизнь с нуля",
}
BTN_LINEAGE = "Род"
BTN_BACK = "Назад"

# -- the legacy screen -------------------------------------------------------------------------------------------------------

LEGACY_TITLE = header("legacy", "Наследие: {name}")
LEGACY_INTRO = "Род {family} не заканчивается на одном человеке. Кто продолжит?"
LEG_RELATIVE = (
    I["family"] + " <b>Ребёнок или родственник.</b> Достаётся {money}% денег, жильё, {debt}% долга, отношения семьи, "
    "{cred}% репутации и враги из банд. Возраст не меньше {age}."
)
LEG_PROTEGE = (
    I["xp"] + " <b>Протеже: {name}.</b> Связи с фиксерами, {cred}% репутации и навык «{skill}» на {penalty} ниже твоего. "
    "Денег и жилья нет."
)
LEG_ENGRAM = (
    I["engram"] + " <b>Энграмма.</b> Ты просыпаешься в новом теле: {skills}% навыков, человечность на {pen} ниже, "
    "раз в {weeks} нед. чужой голос в голове."
)
LEG_SCRATCH = I["new"] + " <b>С нуля.</b> Ничего не переходит, зато рода хватает на +{bonus} к стартовым глюкам."
LEG_NO_RELATIVE = I["family"] + " <b>Ребёнок или родственник.</b> Живых родных нет."
LEG_NO_PROTEGE = I["xp"] + " <b>Протеже.</b> Ученика у тебя не было."
LEG_NO_ENGRAM = I["engram"] + " <b>Энграмма.</b> «Secure Your Soul» не была оплачена."
BTN_LEG_RELATIVE = "Ребёнок или родственник"
BTN_LEG_PROTEGE = "Протеже"
BTN_LEG_ENGRAM = "Энграмма"
BTN_LEG_SCRATCH = "С нуля"

LEG_PICK_TITLE = header("lineage", "Кто продолжит род?")
LEG_PICK_ROW = "{name}, {role}, {age}. Отношение: {rel}."
LEG_PICK_BTN = "{name}, {age}"
LEG_KIND_ROLE: dict[str, str] = {"child": "ребёнок", "sibling": "брат или сестра", "parent": "родитель", "relative": "родственник"}
LEG_PICK_EMPTY = "Некого выбрать."

LEG_CHOSEN = "Наследник выбран: <b>{name}</b>. Теперь стартовый набор."
LEG_CHOSEN_SCRATCH = "Начинаешь с нуля. Стартовых глюков будет больше на {bonus}. Выбери стартовый набор."
BTN_LEG_CHANGE = "Наследие"

# the block on the sheet of an heir
SHEET_HEIR = I["legacy"] + " <b>Наследие ({how}):</b>"
SHEET_HEIR_MONEY = "Деньги: +{n} эдди."
SHEET_HEIR_DEBT = "Долг: {n} эдди."
SHEET_HEIR_HOME = "Жильё: {home}."
SHEET_HEIR_CRED = "Репутация: улица {street}, статус {status}, слава {fame}."
SHEET_HEIR_SKILL = "Навыки: {skills}."
SHEET_HEIR_ENEMIES = "Враги: {gangs}."
SHEET_HEIR_FAMILY = "Родные: {n}."
SHEET_HEIR_FIXERS = "Фиксеры помнят: {names}."
SHEET_HEIR_ENGRAM = "Человечность на {pen} ниже, раз в {weeks} нед. чужой голос."
SHEET_HEIR_GLITCH = "Бонус рода: +{bonus} к стартовым глюкам."
SHEET_HEIR_NAME = "Имя остаётся: {name}."

# -- Secure Your Soul (the property menu) ---------------------------------------------------------------------------------

ENGRAM_BTN = "Secure Your Soul"
ENGRAM_TITLE = header("engram", "Secure Your Soul (Arasaka)")
ENGRAM_TEXT = (
    "Слепок личности лежит в хранилище Arasaka и не стареет. Если тебя не станет, он проснётся в новом теле: "
    "{skills}% навыков, человечность на {pen} ниже, раз в {weeks} нед. чужой голос в голове. Цена: {price} эдди, платить наличными."
)
ENGRAM_HAVE = "Слепок записан. Договор действует, пока ты жив."
ENGRAM_BUY = "Купить за {price}"
ENGRAM_DONE = I["engram"] + " Слепок записан. Arasaka обещает, что он проснётся, когда ты уже не сможешь."
ENGRAM_UNAVAILABLE = "Услуга ещё не продаётся: слепки души появятся в {year} году."
ENGRAM_NO_EDDIES = "Не хватает эдди: нужно {price} наличными."
ENGRAM_JAIL = "Из камеры договор не подписать."

# -- /admin_kill ------------------------------------------------------------------------------------------------------------

ADMIN_KILL_USAGE = "/admin_kill [@ник или id] [причина]. Причины: {causes}. Ответом на сообщение игрока: /admin_kill [причина]."
ADMIN_KILL_DONE = "{name}: жизнь окончена. Причина: {cause}."


# owner report 29.10 («не знает, почему умер»): what brought the health to zero, by the screen of the pressed button
ZH_WHY = "💔 Здоровье упало до нуля: {what}."
ZH_AGAIN = "Это уже {n}-й раз на нуле за эту неделю: с каждым разом выкарабкаться труднее."
ACT_WORDS: tuple[tuple[str, str], ...] = (
    ("cm", "в бою"), ("duty", "в бою"), ("raid", "в налёте"), ("craid", "в налёте"), ("graid", "в налёте"),
    ("sortie", "на вылазке"), ("tdun", "на вылазке"), ("dgame", "в игре на выживание"), ("dghost", "в игре на выживание"),
    ("ev", "событие недели"), ("week", "прожитая неделя"), ("gig", "заказ"), ("crime", "криминал"),
    ("rip", "операция у риппера"), ("clinic", "клиника"), ("immuno", "иммуноподавители"),
    ("act", "занятие"), ("city", "дело в городе"), ("cnpc", "стычка с жителем города"), ("cplace", "место в городе"),
    ("trip", "поездка"), ("dealer", "вещества"), ("vdealer", "вещества"), ("jail", "тюрьма"), ("gym", "спортзал"),
    ("vrace", "гонка"), ("purge", "Судная ночь"), ("work", "работа"), ("chall", "испытание"), ("capri", "Каприкорн"),
    ("possess", "ИИ в голове"), ("bw", "Чёрный заслон"), ("net", "Сеть"), ("pl", "дело с игроком"),
)


def act_word(act: str) -> str:
    screen = act.split(":", 1)[0]
    if screen == "text":
        return "ответ в чате"
    for prefix, word in ACT_WORDS:
        if screen == prefix or screen.startswith(prefix):
            return word
    return "действие в игре"
