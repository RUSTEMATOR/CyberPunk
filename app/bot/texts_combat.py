"""Player-facing texts of the M5 milestone: gigs and combat. Russian, short.
Icons and formatting only through `app/bot/ui/style.py`.

Style: /tmp/claude-0/style_ru.md (clear first, slang as a seasoning, buttons of 1-3 words).
Telegram HTML; names typed by players go through `esc` before they are put in.
Kept apart from `texts.py` on purpose: that module is being rewritten by other hands.
"""

from __future__ import annotations

import html

from app.bot.ui.style import ICONS as I
from app.bot.ui.style import header

esc = html.escape

# -- shared buttons --------------------------------------------------------------------------------------

BACK = "Назад"
MENU = "Меню"
NEXT = "Дальше"
CANCEL = "Отмена"

# -- gigs: the board -------------------------------------------------------------------------------------------

GIGS_TITLE = header("gigs", "Заказы: {district}")
GIGS_FIXER = I["fixer"] + " Фиксер района: <b>{fixer}</b>."
GIGS_NO_FIXER = "В этом районе сейчас нет фиксера, а значит, и заказов."
GIGS_EMPTY = "Заказов пока нет. Загляни завтра."
GIGS_PICK = "Выбери заказ. Как действовать, решишь после брифинга."
GIG_LINE = "<b>{n}. {title}</b>\n" + I["eddies"] + " {pay} эдди · ур. {tier} · {kind}"
GIG_LOCKED = I["lock"] + " Заказы уровня {tier} откроются с уличной репутацией {cred}."
GIG_LOCKED_RANK = I["lock"] + " Контракты уровня {tier} откроются с рангом {rank}."
GIG_LOCKED_BOTH = I["lock"] + " Контракты уровня {tier} откроются с уличной репутацией {cred} и рангом {rank}."
GIG_RANK_LINE = "Ранг контрактов: <b>{rank}</b>. До уровня {tier} ещё {need} очк."
GIG_RANK_TOP = "Ранг контрактов: <b>{rank}</b>. Все уровни открыты."
GIG_DEPOSIT_BACK = "Залог {sum} эдди вернулся."
GIG_DEPOSIT_LOST = "Залог {sum} эдди сгорел."
GIG_RANK_GAIN = "Ранг контрактов +{gain} (всего {rank})."
GIG_RANK_OPEN = "Открыты контракты уровня {tier}."
BRIEF_DEPOSIT = I["eddies"] + " Залог: <b>{sum}</b> эдди. Вернётся при успехе, при провале сгорит."
GIGS_BUSY = "Ты уже на заказе. Сначала закончи его."
GIGS_CONTINUE = "Продолжить"

GIG_ERRORS: dict[str, str] = {
    "locked": "Для этого заказа не хватает уличной репутации.",
    "taken": "Заказ уже взял кто-то другой.",
    "busy": "Ты уже на заказе.",
    "no_ap": "Не осталось ОД. Проживи неделю, и они восстановятся.",
    "no_money": "Не хватает эдди на залог.",
    "gone": "Заказ уже снят. Открой доску заново.",
}

# -- gigs: the brief and the approaches --------------------------------------------------------------------------

BRIEF_FACTS = I["eddies"] + " Оплата: <b>{pay}</b> эдди · уровень {tier}\n" + I["enemy"] + " Цель: {target}"
BRIEF_AP = I["ap"] + " Заказ стоит {ap} ОД."
BRIEF_APPROACHES = "<b>Как действовать:</b>"
BRIEF_APPROACH_LINE = "{name}: {checks}"
BRIEF_CHECK = "{stat} + {skill}"
BRIEF_DV = "нужно {dv}"
BRIEF_LOUD_WARN = I["danger"] + " <i>Если идёшь громко и что-то срывается, будет драка.</i>"
BRIEF_FREE_BUTTON = "Свой план"

APPROACH_ICONS: dict[str, str] = {"loud": "weapon", "quiet": "stealth", "talk": "talk", "hack": "hack"}  # style keys
APPROACH_BUTTONS: dict[str, str] = {
    "loud": "Громко", "quiet": "Тихо", "talk": "Уговорить", "hack": "Взлом",
}
APPROACH_WORDS: dict[str, str] = {
    "loud": "громко", "quiet": "тихо", "talk": "уговорить", "hack": "взлом",
}
GIG_TYPE_WORDS: dict[str, str] = {
    "delivery": "доставка", "theft": "кража", "elimination": "устранение", "hack": "взлом",
    "escort": "сопровождение", "cyberpsycho": "охота на киберпсиха", "missing": "поиск пропавшего",
}
GIG_TYPE_SHORT: dict[str, str] = {  # the board's pick buttons, «1 · Эскорт»: 7 letters at most
    "delivery": "Курьер", "theft": "Кража", "elimination": "Мокруха", "hack": "Взлом",
    "escort": "Эскорт", "cyberpsycho": "Охота", "missing": "Поиск",
}

FREE_PROMPT = "<b>Одним сообщением опиши, как будешь действовать.</b>\nБот подберёт подход и навык, а сообщение потом удалит."
FREE_NOT_UNDERSTOOD = "Не получилось разобрать. Выбери подход кнопкой."

# -- gigs: the checks ------------------------------------------------------------------------------------------------

CHECK_HEAD = header("gigs", "{title}") + "\n<i>{approach}. Проверка {i} из {n}.</i>"
CHECK_PASSED_BEFORE = "{icon} Проверка {line}"
CHECK_PREV_OK = "пройдена"
CHECK_PREV_FAIL = "провалена"
CHECK_PASS = I["ok"] + " <b>Успех.</b>"
CHECK_PASS_LUCK = I["luck"] + " Удача вытянула. <b>Успех.</b>"
CHECK_FAIL = I["fail"] + " <b>Провал.</b> Но ещё можно вывернуться."
CHECK_FAIL_NOTHING = I["fail"] + " <b>Провал.</b> Крыть нечем."
BTN_LUCK = "Удача +{points} (запас {pool})"
BTN_GLITCH = "Переброс за глюк ({glitches})"
BTN_ACCEPT_FAIL = "Принять провал"

# -- gigs: the result ----------------------------------------------------------------------------------------------------

OUTCOME_TITLES: dict[str, str] = {
    "clean": "Чисто",
    "complication": "С осложнениями",
    "fail": "Провал",
    "disaster": "Катастрофа",
}
GIG_RESULT_HEAD = header("gigs", "{title}") + "\n{mark} <i>{outcome}</i>"
OUTCOME_MARKS: dict[str, str] = {"clean": I["ok"], "complication": I["danger"], "fail": I["fail"], "disaster": I["death"]}
GIG_PAY = I["money_in"] + " Оплата: <b>{pay}</b> эдди."
GIG_NO_PAY = I["eddies"] + " Оплаты нет."
GIG_FIXER_REL = I["fixer"] + " Фиксер {fixer}: отношение {delta}."
GIG_FACTION_REP = I["war"] + " Отношение к {faction}: {delta}."
GIG_FIGHT_NOTE = I["fight"] + " Бой: {result}."
GIG_PUBLIC = I["news"] + " <i>Об этом заказе ещё поговорят на улицах.</i>"

# -- the fight: pending, exits (shared by gigs, events and other sources) ----------------------------------------------------------

FIGHT_PENDING = I["fight"] + " Дело идёт к драке. Ещё можно попробовать обойтись без неё."
FACTION_NAMES: dict[str, str] = {"cyberpsycho": "киберпсих", "maxtac": "МаксТак", "ncpd": "NCPD"}

ZERO_HP_STUB = "Ты был на волоске, но тебя вытащили в последний момент. Здоровье: 1."

# -- the fight: the window ----------------------------------------------------------------------------------------------------

CM_TITLE = header("fight", "Бой: {faction}") + " · ход {tick} из {max}"
CM_TITLE_LATE = header("fight", "Бой: {faction}") + " · ход {tick}"
CM_REINFORCED = I["danger"] + " <b>К ним подошло подкрепление.</b> Остаётся бежать или сдаться."
CM_PLAYER = I["hp"] + " <b>Ты</b> {bar} {hp}/{max}"
CM_ARMOR = I["armor"] + " Броня {armor}"
CM_WEAPON = "в руке: {name}"
CM_FISTS = "кулаки"
CM_RAM = " · RAM {ram}/{max}"
CM_RAM_LINE = I["ram"] + " RAM {bar} {ram}/{max}"
CM_COVER = " · в укрытии"
CM_STUNNED = " · оглушён"
CM_IMPLANT_RUNNING = I["charge"] + " {name} {bar} {charge}% · <b>работает</b>"
CM_IMPLANT_IDLE = I["charge"] + " {name} {bar} {charge}%"
CM_IMPLANT_OFF = I["hack"] + " {name}: <s>не работает</s>, взлом"
CM_IMPLANT_AUTO = I["charge"] + " {name} {bar} {charge}% · автоуклонение {state}"
CM_ON = "вкл"
CM_OFF = "выкл"
CM_ENEMIES = "<b>Враги</b>"
CM_ENEMY = "{mark} {i}. {name} {bar} {hp}/{max}{status}"
CM_ENEMY_OUT_LINE = "{mark} <s>{i}. {name}</s> · {status}"
CM_ENEMY_MARKS: dict[str, str] = {"target": I["enemy"], "idle": I["foe"], "killed": I["death"], "taken": I["taken"], "fled": I["flee"]}
CM_ENEMY_STUNNED = " · оглушён"
CM_ENEMY_OUT = {"killed": "убит", "taken": "вырублен", "fled": "сбежал"}
CM_CHROME_UNKNOWN = "хром не опознан"
CM_ENEMY_RUNNING = "работает: {names}"
CM_ENEMY_CHROME = "хром: {items}"
CM_ENEMY_CHROME_ITEM = "{name} {charge}%"
CM_MARK_TARGET = "› "
CM_OLD = I["time"] + " <i>Ходы {from_}–{to}: урон по врагам {dealt}, по тебе {taken}.</i>"
CM_OLD_ONE = I["time"] + " <i>Ход {from_}: урон по врагам {dealt}, по тебе {taken}.</i>"
CM_TICK = "<b>Ход {tick}</b>"
CM_NOW = "<b>Сейчас</b>"
CM_NOTHING = "Пока тихо."
CM_NOTE_PREFIX = I["note"] + " <i>{text}</i>"
CM_FREE_ROUND = I["danger"] + " Враги бьют первыми."

# buttons
B_SHOOT = "Стрелять"
B_MELEE = "Ударить"
B_NONLETHAL = "Без убийств: {state}"
B_COVER = "Укрыться"
B_HACK = "Взлом"
B_FLEE = "Бежать"
B_EXITS = "Выход"
CM_FLEE_DV = I["flee"] + " <i>Бежать: нужно {dv}. «Выход» — все способы уйти без боя.</i>"
B_IMPLANT_ON = "Вкл. {button}"
B_IMPLANT_OFF = "Выкл. {button}"
B_AUTO = "{button}: авто {state}"
B_SHOT = "{button} ×{n}"
B_TARGET = "{mark}Цель {n}"
B_OK = "Дальше"

# hack menu
HACK_TITLE = header("hack", "Взлом") + "\n" + I["enemy"] + " Цель: {target}\n" + I["ram"] + " RAM: {ram}/{max}"
HACK_LINE = I["hack"] + " {name}: {ram} RAM, нужно {dv}"
HACK_BUTTON = "{name}"
HACK_NONE = "Сейчас запустить нечего."
HACK_NO_CHROME = "у цели нет хрома"

# exits in the fight
CX_TITLE = header("exit", "Выйти из боя") + "\n" + I["danger"] + " Против тебя: {faction}."
CX_BRIBE = "Взятка {cost} · нужно {dv}"
CX_TALK = "Уговорить · нужно {dv}"
CX_FLEE = "Бежать · нужно {dv}"
CX_SURRENDER = "Сдаться"
CX_NO_BRIBE = "Взяток здесь не берут."
CX_NO_TALK = "Разговаривать с ними бесполезно."
CX_NO_TALK_GATE = "Достучаться до него может только тот, кто его знает или лечит."
CX_HALF = I["note"] + " Половина из них уже выбыла: уйти или договориться теперь проще."
CX_FAIL_COST = I["danger"] + " Не выйдет — потеряешь ход, а враги ударят."

EXIT_LINES: dict[str, str] = {
    "bribe_ok": I["ok"] + " Эдди ушли в чужой карман, стволы опустились. Боя не будет.",
    "talk_ok": I["ok"] + " Тебя выслушали. Разошлись без стрельбы.",
    "flee_ok": I["ok"] + " Ты ушёл раньше, чем они сообразили.",
    "bribe_fail": I["fail"] + " Не вышло. Взятку {taken}, бой продолжается.",
    "talk_fail": I["fail"] + " Разговор не клеится. Они бьют первыми.",
    "flee_fail": I["fail"] + " Уйти не вышло, и тебе прилетает в спину.",
}
EXIT_TAKEN = "забрали"
EXIT_KEPT = "не взяли"
SURRENDER_LINES: dict[str, str] = {
    "released": I["surrender"] + " Тебя отпустили.",
    "robbed": I["surrender"] + " Тебя обчистили и отпустили.",
    "beaten": I["surrender"] + " Тебя избили и бросили.",
    "chrome_stripped": I["surrender"] + " Тебе вырезали хром и бросили.",
    "arrested": I["surrender"] + " Тебя задержали.",
    "captured": I["surrender"] + " Тебя взяли в плен и отпустили за выкуп.",
    "executed": I["surrender"] + " Сдаться не помогло: тебя убили.",
    "vanished": I["surrender"] + " Тебя увезли и держали, пока не надоело. Потом избили и выбросили на улицу.",
}

# the end of a fight
END_TITLES: dict[str, str] = {
    "won": header("legends", "Бой выигран"),
    "fled": header("flee", "Уход из боя"),
    "avoided": header("ok", "Разошлись без боя"),
    "surrendered": header("surrender", "Сдача"),
    "downed": header("death", "Ты на нуле"),
}
FIGHT_RESULT_WORDS: dict[str, str] = {
    "won": "победа", "fled": "уход из боя", "avoided": "разошлись миром", "surrendered": "сдача", "downed": "тебя вырубили",
}
END_STATS = I["death"] + " Убито: {kills}, вырублено: {takedowns}. Ходов: {ticks}.\n" + I["damage"] + " Урон: твой {dealt}, по тебе {taken}."
END_XP = I["xp"] + " Опыт: {skill} +{n}."
END_REP = I["war"] + " Отношение к {faction}: {delta}."
END_BETRAYED = I["danger"] + " {faction}: ты нарушил уговор, и это запомнят."
END_CONTINUE = "Дальше"

SKILL_WORDS: dict[str, str] = {"firearms": "стрельба", "melee": "ближний бой", "netrunning": "нетраннинг"}

ERRORS: dict[str, str] = {
    "ab_none": "Этой способности в бою нет.",          # owner request 27.10: chrome abilities in a fight
    "ab_tick": "Одна способность за ход: следующую — на следующем ходу.",
    "ab_cd": "Способность ещё не готова.",
    "no_med": "Инжектор пуст: заряд ещё восстанавливается.",
    "full_hp": "Здоровье и так полное.",
    "charge": "Не хватает заряда.",
    "disabled": "Имплант отключён взломом.",
    "running": "Уже работает.",
    "no_cancel": "Этот имплант не выключить, пока не кончится заряд.",
    "no_weapon": "Для этого нет оружия.",
    "no_target": "Целей не осталось.",
    "ram": "Не хватает RAM.",
    "no_chrome": "У цели нет хрома, взламывать нечего.",
    "shot_used": "В этом ходу залп уже был.",
    "reinforced": "К ним подошло подкрепление. Остаётся бежать или сдаться.",
    "over": "Бой окончен.",
}

# the log
LOG_YOU = "ты"
LOG_YOU_CAP = "Ты"
LOG_HIT = "{icon} {att} → {dfn}: урон <b>{dmg}</b>{down} ({total} против {dv})"
LOG_HIT_ICON = I["damage"]
LOG_HURT_ICON = I["psycho"]  # a hit that lands on you
LOG_MISS = I["dodge"] + " {att} → {dfn}: мимо ({total} против {dv})"
LOG_DODGE = I["dodge"] + " {att} → {dfn}: мимо, спасает {imp}"
LOG_DOWN_KILL = ", убит"
LOG_DOWN_TAKEN = ", вырублен"
LOG_DOWN_PLAYER = ", ты на нуле"
LOG_DOWN_ALLY = ", падает и не встаёт"
LOG_ROCKET = "{icon} {att} → {dfn}: урон <b>{dmg}</b>{down} ({imp})"
LOG_SHOT = I["weapon"] + " {att}: {imp}, залп ×{n}"
LOG_ACTIVATE = I["charge"] + " {who}: включён {imp}"
LOG_CANCEL = I["charge"] + " {who}: выключен {imp}"
LOG_EXPIRED = I["charge"] + " {who}: {imp}, заряд кончился"
LOG_OVERLOAD = I["humanity"] + " Перегрузка хрома: теряешь человечность"
LOG_COVER = I["cover"] + " Ты уходишь в укрытие"
LOG_HACK_OK = I["hack"] + " {att} → {dfn}: взлом «{prog}» прошёл{fx}"
LOG_HACK_FAIL = I["hack"] + " {att} → {dfn}: взлом «{prog}» не прошёл ({total} против {dv})"
LOG_FX_DAMAGE = ", урон {n}"
LOG_FX_STUN = ", оглушение {n}"
LOG_FX_PENALTY = ", атака −{n}"
LOG_FX_DRAIN = ", заряд −{n}"
LOG_FX_DISABLE = ", имплант отключён"
LOG_STUNNED = I["stun"] + " {who}: пропуск хода, оглушение"
LOG_FLEE_MORALE = I["flee"] + " {who} сдаёт назад и уходит"
LOG_FLEE_BLACKWALL = I["flee"] + " {who} видит, что течёт из раны, кричит и бежит без оглядки"
LOG_BLACKWALL = "🕳 Из-за Заслона приходит шум: {dfn} замирает, нервы горят помехами"
LOG_BLACKWALL_ECHO = "{icon} Эхо Заслона прыгает на {dfn}: {dmg} урона сквозь броню{down}"
LOG_FLEE_COWARD = I["flee"] + " {who} сбегает"
LOG_EXIT_FAIL = I["fail"] + " Выйти из боя не удалось, враги пользуются моментом"
LOG_REINFORCE = I["danger"] + " К ним подошло подкрепление"
LOG_CRIT = " · крит"
LOG_FUMBLE = " · осечка"

EXITCHK_HEAD: dict[str, str] = {
    "bribe": header("eddies", "Откупиться"),
    "talk": header("relations", "Договориться"),
    "flee": header("flee", "Бежать"),
}

LOG_HEAL = "💉 Ты вкалываешь инжектор: здоровье +{n}."  # owner request 24.10
B_HEAL = "Инжектор {left}/{max}"
