"""Everything the player reads that does not depend on game data. Russian, short, plain.

Icons and formatting only through `app/bot/ui/style.py` (one icon at the start of a line or a title).

Texts are Telegram HTML. Anything typed by a player goes through `esc` before it is put in.
Texts that need game data (character sheet, profile, menu header) are built in `app/bot/views.py`.

Terms are the same on every screen: «эдди», «ОД», «здоровье», «настроение», «человечность»,
«уличная репутация», «статус», «слава», «розыск», «удача», «глюки», «жетоны».
"""

from __future__ import annotations

import html
import random

from app.bot.ui.style import ICONS as I
from app.bot.ui.style import header

esc = html.escape

# -- system -------------------------------------------------------------------------------

PRIVATE_ONLY = "Игра идёт в группе."
NO_GROUP = "Группа игры не назначена. Админ, выполни /admin_setgroup в нужном чате."
NOT_YOUR_WINDOW = "Это не твоё окно, чумба"
WINDOW_STALE = "Окно устарело, /play"
WINDOW_CLOSED_ANON = "Окно закрыто. /play"
ERROR_TITLE = "Сбой в сети. Попробуй ещё раз."
ERROR_TEMP = "Сбой в сети. Попробуй ещё раз."
BACK = "Назад"
MENU = "Меню"
CLOSE = "Закрыть"
CANCEL = "Отмена"


def window_closed(name: str) -> str:
    return f"{esc(name)} — окно закрыто. /play"


def window_header(owner: str | None, user_id: int) -> str:
    """First line of every window: whose window it is. The name links to the Telegram profile."""
    who = esc(owner) if owner else "игрок"
    return f'┃ Окно: <a href="tg://user?id={user_id}"><b>{who}</b></a>\n\n'


# -- character creation ---------------------------------------------------------------------

CREATE_PRESET_TITLE = (
    header("profile", "Кто ты в Найт-Сити?") + "\n"
    "Выбери, с чего начнёшь. Это ещё не роль: роль сложится из того, чем ты будешь заниматься."
)
CREATE_PAGE = "Страница {page} из {pages}"
CREATE_REROLL = "Перебросить"
CREATE_REROLL_LEFT = "Перебросить ({n})"
CREATE_ACCEPT = "Принять"
CREATE_BACK_TO_PRESETS = "Другой набор"
CREATE_REROLLS_LEFT = "Перебросов осталось: {n}."
CREATE_NO_REROLLS = "Перебросов нет."
CREATE_NAME_TITLE = header("profile", "Как тебя зовут?")
CREATE_NAME_PROPOSED = "Вариант: <b>{full}</b>"
CREATE_NAME_KEEP = "Взять это имя"
CREATE_NAME_RANDOM = "Другое имя"
CREATE_NAME_TYPE = "Ввести своё"
CREATE_GENDER_M = "Мужской"
CREATE_GENDER_F = "Женский"
CREATE_GENDER_NOW = "Пол: <b>{g}</b>. Кнопки ниже меняют его."
CREATE_NAME_PROMPT = (
    "<b>Ответь сообщением с именем.</b>\n"
    "От {min} до {max} символов, без ссылок и @юзернеймов. Твоё сообщение бот потом удалит."
)
CREATE_NAME_TOO_SHORT = "Имя короче допустимого. Минимум символов: {min}."
CREATE_NAME_TOO_LONG = "Имя длиннее допустимого. Максимум символов: {max}."
CREATE_NAME_LINK = "Ссылки и юзернеймы в имени запрещены."
CREATE_NAME_BAD = "В имени можно использовать только буквы, цифры, пробел, дефис и апостроф."
CREATE_NAME_ERROR = "<i>{reason}</i>\n"
CREATE_SAVE_FAILED = "Не удалось сохранить персонажа. Нажми /play и начни заново."

FIXER_GREETINGS: tuple[str, ...] = (
    "{fixer} смотрит на тебя поверх стакана. «{name}, значит. Новое лицо в районе {district}. Заказы есть, долги есть у всех. Не подведи.»",
    "«Слышал, в районе {district} новенький. {name}, да?» {fixer} не улыбается. «Заказы на доске. Работай чисто, и мы поладим.»",
    "{fixer}: «{name}? Запомни: {district} — мой район. Понадобятся эдди — приходи.»",
    "{fixer} кивает, будто ждал. «{name}. В районе {district} слабых не любят. Принесёшь результат — будут заказы.»",
    "«Так это ты {name}?» {fixer} чешет шрам. «Ладно. {district} большой, места хватит. Главное — не наступи не на того.»",
)
NO_FIXER_GREETING = "{district} встречает тебя молча, {name}. Фиксера здесь придётся искать самому."


def fixer_greeting(rng: random.Random, fixer: str | None, name: str, district: str) -> str:
    if fixer is None:
        return NO_FIXER_GREETING.format(name=esc(name), district=esc(district))
    return rng.choice(FIXER_GREETINGS).format(fixer=f"<b>{esc(fixer)}</b>", name=esc(name), district=esc(district))


# -- main menu -----------------------------------------------------------------------------------

# (key, label) in the order of DESIGN.md section 4. The key is also the screen name of the section.
MENU_SECTIONS: tuple[tuple[str, str], ...] = (
    ("profile", "Профиль"),
    ("gigs", "Заказы"),
    ("work", "Работа"),
    ("activities", "Активности"),
    ("relations", "Отношения"),
    ("property", "Имущество"),
    ("ripper", "Риппер"),
    ("crime", "Криминал"),
    ("city", "Город"),
    ("move", "Переезд"),
    ("daily", "Ежедневка"),
    ("week", "Прожить месяц"),
)
SECTION_LABELS = dict(MENU_SECTIONS)

MENU_AP_ZERO = "<i>Очки действий (ОД) кончились. Доступны профиль, ежедневка и «Прожить месяц».</i>"


def esc_notice(line: str) -> str:
    """A parked notice is already Telegram HTML written by the game: shown as a quote line."""
    return f"<i>{line}</i>"


NO_AP = "ОД кончились. Нажми «Прожить месяц»."

MENU_NO_CHARACTER = "Живого персонажа у тебя нет. Пора начать новую жизнь."
SOON_TITLE = "<b>{section}</b>"
SOON_TEXT = "Скоро. Этот раздел ещё не готов."

# -- stats and skills ---------------------------------------------------------------------------------

STAT_LABELS: dict[str, str] = {
    "BODY": "ТЕЛО", "REF": "РЕФЛ", "TECH": "ТЕХ", "INT": "ИНТ",
    "COOL": "ХЛАД", "EMP": "ЭМП", "LUCK": "УДАЧА",
}
SKILL_LABELS: dict[str, str] = {
    "firearms": "Стрельба", "melee": "Ближний бой", "stealth": "Скрытность",
    "netrunning": "Нетраннинг", "tech": "Техника", "medicine": "Медицина",
    "persuasion": "Убеждение", "driving": "Вождение",
}

# -- admin ---------------------------------------------------------------------------------------------

ADMIN_NOT_IMPLEMENTED = "{cmd}: пока не реализовано."
ADMIN_USAGE_GIVE = "Формат: /admin_give @игрок сумма (или ответом на сообщение игрока: /admin_give сумма)."
ADMIN_USAGE_SET = "Формат: /admin_set @игрок поле значение. Поля: {fields}."
ADMIN_NO_PLAYER = "Игрок не найден или у него нет живого персонажа."
ADMIN_GIVE_DONE = "{name}: {delta:+d} эдди, на счету {eddies}."
ADMIN_SET_DONE = "{name}: {field} = {value}."
ADMIN_SET_BAD_FIELD = "Поле {field} менять нельзя. Можно: {fields}."
ADMIN_SET_BAD_VALUE = "Нужно целое число, не меньше нуля."
ADMIN_SETGROUP_DONE = "Группа игры сохранена: {chat_id}."
ADMIN_BOOTSTRAP_CLAIMED = (
    "ADMIN_IDS пуст, поэтому админом назначен ты (id {user_id}). "
    "Чтобы это закрепить, впиши этот id в ADMIN_IDS в файле .env."
)
ADMIN_SETGROUP_ONLY_GROUP = "/admin_setgroup работает только в группе, не в личке."
ADMIN_SETGROUP_NO_RIGHTS = (
    "Внимание: у бота нет права удалять сообщения. Игра работает, но команды игроков и "
    "временные ответы останутся в чате. Сделай бота админом с правом «Удаление сообщений»."
)
ADMIN_SETGROUP_CHECK_FAILED = (
    "Не удалось проверить права бота. Убедись, что он админ группы с правом «Удаление сообщений»."
)

# -- the week ------------------------------------------------------------------------------------

WEEK_CONFIRM = (
    header("week", "Прожить месяц?") + "\n"
    "Непотраченные ОД ({ap}) сгорят. Зарплата, аренда и проценты по долгам посчитаются сами."
)
WEEK_LIVE = "Прожить месяц"
WEEK_TITLE = "<b>Неделя {n} позади.</b>"
WEEKS_TITLE = "<b>Позади {span} нед. Сейчас неделя {n}.</b>"
WEEK_NEXT = "Дальше"
WEEK_MONEY = "Зарплата +{salary}, аренда −{rent}, еда −{food}."
WEEK_TRAUMA = "Подписка Trauma Team −{trauma}."
WEEK_IDLE = "Зарплаты нет: за решёткой и в бегах не работают."
WEEK_DEBT = "Долги: проценты +{interest}, выплачено {paid}."
WEEK_MISSED = "Платёж не прошёл: {creditors}. Долг растёт, и о нём помнят."
WEEK_COLLECTOR = "За тобой уже идёт коллектор."
WEEK_HP = "Здоровье: {hp}."
WEEK_MOOD = "Настроение: {mood}."
WEEK_NO_CHANGE = "без изменений"
WEEK_INFECTION = "Инфекция ест тебя изнутри: −{dmg} здоровья."
WEEK_HEAT = "Розыск {heat}."
WEEK_BIRTHDAY = "День рождения. Тебе {age}."
WEEK_CHARGE = "Импланты снова заряжены."
WEEK_BUFFS = "Закончилось эффектов: {n}."
WEEK_VOWS = "Обеты в силе: {vows}."

FU_COMBAT_STUB = "Бой вышел коротким и грязным: −{damage} здоровья. <i>(полноценный бой появится позже)</i>"
FU_DEATH_SAVE_STUB = "Ты на краю, но ещё дышишь. Здоровье: 1. <i>(спасбросок появится позже)</i>"
FU_DEATH = I["death"] + " <b>Конец.</b> Причина: {cause}."
FU_JAIL = I["jail"] + " Тебя взяли. Срок: {weeks} нед."
FU_VOW_BREAK = I["vows"] + " Обет нарушен: «{vow}». Серия сгорела, но обет можно принять снова."
FU_HUMANITY_LOW = I["humanity"] + " Человечность на исходе. Каждый новый имплант теперь обходится дороже."
FU_HUMANITY_CRITICAL = I["danger"] + " Человечности почти не осталось. Ещё шаг — и ты перестанешь быть собой."
FU_CYBERPSYCHOSIS = "Срыв. <i>(киберпсихоз появится позже)</i>"

DEATH_CAUSE_NAMES: dict[str, str] = {
    "combat": "бой", "cyberpsychosis": "киберпсихоз", "old_age": "старость", "time_out": "время вышло", "radiation": "лучевая болезнь", "corp_river": "нашли в реке", "collector": "коллекторы",
    "prison": "тюрьма", "overdose": "передозировка", "implant_failure": "отказ импланта", "wounds": "раны", "maxtac": "МаксТак",
    "blackwall": "Чёрный заслон",
    "own_will": "по своей воле", "left_city": "отъезд из города навсегда", "legend": "уход в легенду", "legend_alive": "живым в Зал легенд", "end_tower": "финал: штурм башни Арасаки", "end_nomads": "финал: уход с кочевниками", "end_orbit": "финал: орбита", "end_mayor": "финал: вечный мэр", "possessed": "тело отдано ИИ", "dgame": "смерть в игре богачей", "bounty_hunt": "охота за головой рейд-босса",
}
DEATH_TITLE = header("death", "{name}: жизнь окончена.")
DEATH_LINE = I["obituary"] + " Причина: {cause}. Город забудет быстро."
DEATH_NEW_LIFE = "Новая жизнь"

# -- events ---------------------------------------------------------------------------------------

EVENT_QUEUE = "<i>Событие недели. Ещё в очереди: {n}.</i>"
EVENT_ONE = "<i>Событие недели.</i>"
EVENT_ON_DEBT = "в долг"
EVENT_LUCK = "Удача: +{points} (есть {pool})"
EVENT_GLITCH = "Переброс за глюк ({glitches})"
EVENT_ACCEPT_FAIL = "Принять провал"
EVENT_CONTINUE = "Дальше"
CHECK_PASS = I["ok"] + " <b>Успех.</b>"
CHECK_PASS_LUCK = I["luck"] + " Удача вытянула. <b>Успех.</b>"
CHECK_FAIL = I["fail"] + " <b>Провал.</b> Но можно попробовать вывернуться."
CHECK_FAIL_NOTHING = I["fail"] + " <b>Провал.</b> Вывернуться нечем."
CRIT_WIN = ", критический успех"
CRIT_FAIL = ", критический провал"
RESULT_LABEL = "Итог"
EFFECT_LABELS: dict[str, str] = {
    "eddies": "эдди", "hp": "здоровье", "mood": "настроение", "humanity": "человечность",
    "street_cred": "уличная репутация", "status": "статус", "fame": "слава", "heat": "розыск",
    "luck_pool": "удача", "glitches": "глюки", "debt": "долг",
}

EXIT_TITLE = header("fight", "Назревает бой")
EXIT_FACTION = I["danger"] + " Против тебя: {faction}. Опасность: {danger}/5"
EXIT_FIGHT = "Принять бой"
EXIT_BRIBE = "Взятка {cost} · нужно {dv}"
EXIT_TALK = "Уговорить · нужно {dv}"
EXIT_FLEE = "Бежать · нужно {dv}"
EXIT_SURRENDER = "Сдаться"
EXIT_NO_BRIBE = "Взяток здесь не берут."
EXIT_NO_TALK = "Разговаривать с ними бесполезно."
EXIT_NO_TALK_GATE = "Достучаться до него может только тот, кто его знает или лечит."
EXIT_OK_BRIBE = I["ok"] + " Деньги исчезают в чужом кармане, стволы опускаются. Боя не будет."
EXIT_OK_TALK = I["ok"] + " Слова сработали. Разошлись без стрельбы."
EXIT_OK_FLEE = I["ok"] + " Ты исчезаешь раньше, чем они успевают сообразить."
EXIT_FAIL_BRIBE = I["fail"] + " Не вышло. Взятку {taken}, а боя всё равно не избежать."
EXIT_FAIL_BRIBE_TAKEN = "забрали"
EXIT_FAIL_BRIBE_KEPT = "не взяли"
EXIT_FAIL_TALK = I["fail"] + " Разговор не клеится. Они бьют первыми."
EXIT_FAIL_FLEE = I["fail"] + " Уйти не вышло: получаешь в спину, и начинается бой."
EXIT_SURRENDER_OUT: dict[str, str] = {
    "released": I["surrender"] + " Тебя отпустили. Повезло.",
    "robbed": I["surrender"] + " Тебя обчистили до нитки и вышвырнули на улицу.",
    "beaten": I["surrender"] + " Тебя избили и отпустили.",
    "chrome_stripped": I["surrender"] + " Тебя вырубили и вырезали часть хрома.",
    "arrested": I["surrender"] + " Тебя арестовали.",
    "captured": I["surrender"] + " Тебя держали ради выкупа. Пришлось откупаться тем, что было.",
    "executed": I["surrender"] + " Слушать тебя не стали.",
    "vanished": I["surrender"] + " Тебя увезли и держали, пока не надоело. Потом избили и выбросили на улицу.",
}
EXIT_FACTION_NAMES: dict[str, str] = {"cyberpsycho": "киберпсих", "maxtac": "МаксТак", "netwatch": "NetWatch"}

# -- activities (PLAN 4.5) ------------------------------------------------------------------------------

ACT_TITLE = header("activities", "Активности")
ACT_STATUS = I["eddies"] + " {eddies} эдди · ОД {ap} · настроение {mood}"
ACT_JAIL_NOTE = "<i>Ты за решёткой: доступно только то, что можно делать в камере. Здесь это бесплатно, платишь только ОД.</i>"
ACT_PATH_HEAD = "<b>Путь без хрома</b> (нижние кнопки):"
ACT_VOW_WARN = "нарушит обет"

# -- vows (4.8) --
VOW_MILESTONE = I["vows"] + " «{vow}»: {weeks} нед. подряд. Открыт новый бонус."
BTN_VOWS = "Обеты"
BTN_RENAME = "Сменить имя"
RENAME_PROMPT = (
    header("profile", "Новое имя") + "\n"
    "Сейчас: {current}\n\n"
    "Ответь сообщением. Одно слово — новое имя, два и больше — имя и фамилия. "
    "От {min} до {max} символов, без ссылок. Твоё сообщение бот удалит."
)
VOWS_TITLE = header("vows", "Обеты")
VOWS_INTRO = (
    "Обет держат неделями, и за длинную серию открываются бонусы. "
    "Нарушишь — серия сгорит, а штраф придёт сразу. Сгоревший обет можно взять снова."
)
VOWS_HELD = "<b>Твои обеты</b>"
VOWS_HELD_LINE = "{name}: серия {weeks} нед."
VOWS_BONUS_NOW = "  Сейчас даёт: {mods}."
VOWS_BONUS_NEXT = "  Через {left} нед.: {mods}."
VOWS_NONE_HELD = "Ты пока не держишь ни одного обета."
VOWS_FREE = "<b>Можно взять</b>"
VOWS_FREE_LINE = "<b>{name}</b>\n{desc}"
VOWS_NONE_FREE = "Ты уже держишь все обеты."
VOWS_BTN_NO = "Не сейчас"
VOW_CONFIRM = "<b>{name}</b>\n{desc}\n\nЕсли нарушишь: серия сгорит, {cost}.\nПринять обет?"
VOW_CONFIRM_BTN = "Принять обет"
VOW_TAKEN = "Обет принят: «{name}». Серия пошла."
VOW_TAKE_HELD = "Этот обет ты уже держишь."
VOW_TAKE_CHROME = "В тебе есть хром. С ним этот обет не взять."
VOW_TAKE_GONE = "Такого обета нет."
VOW_NO_CHAR = "Обеты может давать только живой персонаж."
ACT_LABELS: dict[str, str] = {
    "train": "Тренировка", "study": "Учёба", "bar": "Бар", "braindance": "Брейнданс",
    "therapy": "Терапия", "rest": "Отдых", "gamble": "Азарт", "meditation": "Медитация",
    "martial_arts": "Боевые искусства", "service": "Служение общине",
}
ACT_LINE = "{name} · {ap} ОД{price}"
ACT_SHORT: dict[str, str] = {"martial_arts": "Рукопашка", "service": "Община"}  # button labels, 11 letters at most
ACT_PRICE = " · {eddies} эдди"
ACT_FREE_BUTTON = "{name} · {ap} ОД"
ACT_BLOCKED: dict[str, str] = {
    "jail": "В камере этого не сделать.",
    "eddies": "Не хватает эдди.",
    "skill_cap": "Этот навык уже на пределе. Здесь больше ничему не научат.",
    "wrong_skill": "Этот навык так не качается.",
    "chrome_free": "Терапия тебе не нужна: без хрома человечность и так полная.",
    "humanity_full": "Человечность на максимуме. Остальное забрал хром, и терапия его не вернёт.",
}
ACT_NO_AP = "ОД кончились. Нажми «Прожить месяц»."
ACT_SKILL_TITLE = (
    header("xp", "{title}") + "\n{what}\nЗа раз: {eddies} эдди, {ap} ОД, опыт +{xp}, настроение {mood}.\n"
    "Полоска — опыт до следующего уровня, справа — сколько ещё раз."
)
ACT_SKILL_WHAT = {
    "train": "Тренировка качает тело: то, что делают руки и ноги.",
    "study": "Учёба качает голову: то, что берут знаниями.",
}
ACT_SKILL_OTHER = {
    "train": "Нетраннинг, технику, медицину и убеждение качает учёба.",
    "study": "Стрельбу, ближний бой, скрытность и вождение качает тренировка.",
}
ACT_SKILL_AFTER = I["xp"] + " {line}"
# One line under each activity in the list: what it gives (numbers from balance.activities).
ACT_DESC: dict[str, str] = {
    "train": "тело: стрельба, ближний бой, скрытность, вождение · опыт +{xp}, настроение {mood}",
    "study": "голова: нетраннинг, техника, медицина, убеждение · опыт +{xp}, настроение {mood}",
    "bar": "настроение +{mood}, бывают встречи и знакомства",
    "braindance": "настроение +{mood}, но затягивает: копится зависимость",
    "therapy": "возвращает человечность ({dice})",
    "rest": "настроение +{mood}, здоровье +{hp}",
    "gamble": "ставка на проверку ХЛАД, выигрыш x2",
    "meditation": "проверка ХЛАД: настроение, человечность, розыск −1",
    "martial_arts": "только ближний бой, опыт +{xp} — больше, чем на тренировке",
    "service": "+{eddies} эдди, слава +{fame}, настроение +{mood}",
}
ACT_GAMBLE_TITLE = (
    header("slots", "Азарт") + "\n"
    "Ставка списывается сразу. Проверка {stat}, нужно {dv}. Выиграешь — получишь ставку x{mult}."
)
ACT_GAMBLE_STAKE = "Ставка {stake}"
ACT_GAMBLE_WIN = I["money_in"] + " Выигрыш: <b>{payout}</b> эдди на руки."
ACT_GAMBLE_LOSE = I["fail"] + " Проиграно {stake} эдди."
ACT_GAMBLE_CHECK_HEAD = header("slots", "Азарт") + " · ставка {stake} эдди"
ACT_GAMBLE_SLOTS = I["slots"] + " Автомат выдал {face} из {faces}."
ACT_AGAIN = "Ещё раз"
ACT_BACK_TO_LIST = "К списку"
ACT_EVENT_NEXT = "Похоже, что-то случилось."
ACT_QUEUED_BAR = "В баре что-то назревает."
ACT_QUEUED_ADDICTION = "Тебя снова тянет в брейнданс. Это уже не просто отдых."
ACT_OVERDOSE = "Брейнданс не отпускает. Сердце сбивается с ритма, а выдернуть разъём ты уже не успеваешь."
ACT_THERAPY_LINE = I["humanity"] + " Терапия вернула человечности: {n}."
ACT_ADDICTION = I["danger"] + " Зависимость от брейнданса: {n}."
ACT_PARTNER = "За стойкой ты с кем-то разговорился, и теперь у тебя есть пара."

# Result lines of each activity; the narrator replaces them when it is live.
ACT_FLAVOUR: dict[str, list[str]] = {
    "train": [
        "Пот, ржавое железо и тренер, который не знает слова «хватит».",
        "Час в подвальном спортзале Кабуки. Тело ноет, навык растёт.",
        "Повтор за повтором. Мышечная память эдди не просит.",
    ],
    "study": [
        "Пиратские чипы, ворованные учебники и лапша над клавиатурой. Голова гудит.",
        "Ночь над мануалами. К утру половина понятна, а вторая хотя бы знакома.",
        "Курсы в подвале, где преподаватель не спрашивает документы.",
    ],
    "bar": [
        "В «Посмертии» шумно, чужие разговоры текут мимо. Пара стаканов, и отпускает.",
        "Стойка, стакан, чужая болтовня. Пара часов без мыслей о долгах.",
        "Дешёвый виски и громкая музыка. Город ненадолго отстаёт.",
    ],
    "braindance": [
        "Чужая память, чужое тело. Выходишь с приторным привкусом чужого счастья.",
        "Разъём щёлкает, мир гаснет и загорается чужим светом. Возвращаться труднее, чем уходить.",
        "Два часа в чужой шкуре. Своя после этого кажется серой.",
    ],
    "therapy": [
        "Кабинет без окон, врач без имени. Он слушает, ты говоришь. Так человек собирается заново.",
        "Тебя собирают заново, слово за словом. Дорого, но работает.",
    ],
    "rest": [
        "Спишь, ешь и никуда не бежишь. Редкая роскошь.",
        "День на матрасе. Тело отдохнуло, голова ещё нет, но уже легче.",
        "Телефон выключен, дверь заперта. Город подождёт.",
    ],
    "meditation": [
        "Дышишь, пока шум города не становится далёким прибоем.",
        "Час на крыше в тишине. Мысли оседают, как пыль.",
    ],
    "meditation_fail": [
        "Мысли мигают, как неоновые вывески. Тишины не вышло, но и это передышка.",
        "Голова забита счетами и лицами. Медитация не идёт.",
    ],
    "martial_arts": [
        "Зал, где бьют честно и без брони. Синяков и пользы поровну.",
        "Стойка, удар, снова стойка. Учитель молчит, пока не поймёшь сам.",
    ],
    "service": [
        "Раздаёшь еду, чинишь соседям крышу. Эдди немного, зато тебя теперь знают по имени.",
        "Общине нужны руки, а не деньги. Работа есть, и спасибо тоже.",
    ],
    "gamble_win": [
        "Карта идёт. Крупье кривится, ты забираешь выигрыш.",
        "Фишки текут к тебе, а не от тебя. Везёт, пока везёт.",
    ],
    "gamble_lose": [
        "Стол забирает всё. Крупье даже не поднимает глаз.",
        "Фишки уходят, как вода в сток. «В следующий раз повезёт», — говорят за столом.",
    ],
}

# -- property and moving (PLAN 4.6) -----------------------------------------------------------------------

PR_TITLE = header("property", "Имущество")
PR_HOME = I["home"] + " Жильё: {home}. В неделю: аренда {rent}, еда {food}."
PR_NO_HOME = I["home"] + " Жилья нет, ночуешь где придётся. Еда {food} в неделю."
PR_WEAPON = I["weapon"] + " Оружие: {name}"
PR_ARMOR = I["armor"] + " Броня: {name}"
PR_NONE = "нет"
PR_TRAUMA = I["trauma"] + " Trauma Team: {tier}, {price} в неделю."
PR_NO_TRAUMA = I["trauma"] + " Trauma Team: подписки нет."
PR_BTN_HOME = "Жильё"
PR_BTN_WEAPONS = "Оружейная"
PR_BTN_ARMOR = "Броня"
PR_BTN_EQUIP = "Экипировка"
PR_BTN_TRAUMA = "Trauma Team"
PR_BLOCKED: dict[str, str] = {
    "jail": "Из камеры этим не заняться.",
    "eddies": "Не хватает эдди.",
    "cred": "Тебя здесь ещё не знают: мало уличной репутации.",
    "status": "Охрана качает головой: статус не тот.",
    "owned": "Это у тебя уже есть.",
    "here": "Так уже и есть.",
    "gone": "Такого здесь нет.",
    "no_ap": "ОД кончились. Нажми «Прожить месяц».",
    "unavailable": "Сейчас они не берут новых клиентов.",
}

PRH_TITLE = header("home", "Жильё: {district}")
PRH_DEPOSIT_NOTE = "Въезд стоит {ap} ОД и залог: аренда за {weeks} нед. вперёд. Залог не возвращается."
PRH_EMPTY = "В этом районе жильё не сдают. Ищи в другом."
PRH_LINE = "<b>{name}</b>{mark}\n{desc}\nАренда {rent}/нед., еда {food}/нед., залог {deposit}."
PRH_MARK_HERE = " — ты здесь"
PRH_NEEDS_STATUS = "Нужен статус {n}."
PRH_BUTTON = "{name} · залог {deposit}"
PRH_DONE = "Новое жильё: {name}. Залог {deposit} эдди уплачен."
PRH_DONE_FREE = "Новое жильё: {name}."

PRS_TITLE_W = header("weapon", "Оружейная")
PRS_TITLE_A = header("armor", "Броня")
PRS_DISCOUNT = "Скидка {pct}%."
PRS_LINE = "<b>{name}</b> · {price} эдди{extra}\n{stat}\n<i>{desc}</i>"
PRS_CRED = " · нужна уличная репутация {cred}"
PRS_OWNED = " · уже есть"
PRS_BUTTON = "{name} · {price}"
PRS_DONE = I["shop"] + " Куплено: {name} за {price} эдди."
PRS_DONE_EQUIP = I["shop"] + " Куплено: {name} за {price} эдди. Уже при тебе."
PRS_EMPTY = "Прилавок пуст."
PRS_AP = "Покупка стоит {ap} ОД."

PRE_TITLE = header("armor", "Экипировка")
PRE_EMPTY = "В сумке пусто. Оружие и броня продаются в «Имуществе» → «Оружейная» и «Броня»."
PRE_IN_HAND = "🔫 Оружие при тебе: <b>{name}</b>"
PRE_SLOTS = "<i>При себе до {n} единиц оружия: в бою лучший ствол стреляет, лучший клинок бьёт. Когда руки заняты, новое заменяет последнее взятое.</i>"
PRE_ON_BODY = "🦺 На тебе: <b>{name}</b>"
PRE_FISTS = "ничего, дерёшься кулаками"
PRE_NO_ARMOR = "без брони"
PRE_TAKE = "Взять: {name}"
PRE_WEAR = "Надеть: {name}"
PRE_PUT_AWAY = "Убрать: {name}"
PRE_PUT = "Убрано в сумку."
PRE_WEAPONS = "<b>Оружие</b>"
PRE_ARMORS = "<b>Броня</b>"
PRE_MARK = " (при тебе)"
PRE_BUTTON = "{name}"
PRE_DONE = "Теперь при тебе: {name}."

PRT_TITLE = header("trauma", "Trauma Team")
PRT_TEXT = ("Страховка на случай, если истекаешь кровью на асфальте. Платишь каждую неделю, "
            "а за вызов бригады — отдельно. Чем выше уровень, тем вернее прилетят.")
PRT_TIER = "{name}: {price}/нед., вызов {evac}, прилетают в {chance}% случаев."
PRT_BUTTON = "{name} · {price}/нед."
PRT_CANCEL = "Отписаться"
PRT_NOW = "Твоя подписка: {name}."
PRT_NONE = "Подписки нет."
PRT_DONE = "Контракт подписан: {name}. Бригада на связи."
PRT_CANCELLED = "Подписка отменена. Теперь ты сам по себе."
PRT_CLOSED = "Trauma Team сейчас не работает в городе."

MV_TITLE = header("move", "Переезд")
MV_TEXT = (
    "Твой район: {district}. В новом районе другой фиксер, другие банды и другие события. "
    "Съёмное жильё придётся бросить, залог пропадёт. Переезд стоит {ap} ОД."
)
MV_LINE = I["district"] + " <b>{name}</b> · опасность {danger}\n{desc}"
MV_CONFIRM = (header("move", "Переезд: {name}") + "\n"
              "{desc}\nОпасность {danger}. Фиксер: {fixer}.\n"
              "{home_note}\nПереезд стоит {ap} ОД.")
MV_HOME_LOST = "Жильё «{home}» придётся бросить, залог пропадёт."
MV_NO_HOME_LOST = "Жилья у тебя нет, терять нечего."
MV_GO = "Переехать"
MV_DONE = "Переезд позади. Новый район: {district}."
MV_DONE_RENTALS = "Здесь сдают жильё: загляни в «Имущество»."
MV_DONE_NO_RENTALS = "Жильё здесь не сдают, ночевать придётся на улице."
MV_EMPTY = "Переезжать сейчас некуда."

# -- daily loop (PLAN 4.7) ---------------------------------------------------------------------------------

DAILY_TITLE = header("daily", "Ежедневка")
DAILY_WAITING = I["misty"] + " Мисти раскладывает карты. Одна карта в сутки."
DAILY_STATUS = I["time"] + " Серия: {streak} дн. · заморозок: {freeze}"
DAILY_TOKENS = I["tokens"] + " Жетонов: {tokens}"
DAILY_RISK = I["danger"] + " Пропустишь день — серия сгорит."
DAILY_NO_CHAR = "<i>Живого персонажа нет. Эдди и эффект карты получит следующий, если появится сегодня.</i>"
DAILY_ALREADY = I["misty"] + " Сегодня Мисти уже гадала тебе."
DAILY_REVEAL = I["misty"] + " Мисти переворачивает карту:"
DAILY_CARD_LINE = I["misty"] + " Карта дня:"
DAILY_CARD = "{icon} <b>{name}</b>"
DAILY_EFFECT = I["time"] + " До полуночи по Киеву: {mods}."
DAILY_PAYOUT = I["eddies"] + " Эдди: {value}."
DAILY_PAYOUT_WAIT = I["eddies"] + " Эдди ({value}) дождутся нового персонажа."
DAILY_STREAK = I["time"] + " Серия продлена: {streak} дн."
DAILY_STREAK_BROKEN = I["fail"] + " Прошлая серия сгорела, отсчёт пошёл заново."
DAILY_FREEZE_USED = I["note"] + " Серию спасла заморозка. Потрачено заморозок: {n}."
DAILY_CHEST = I["reward"] + " Награда за серию, день {day}: {reward}."
BTN_DAILY_CLAIM = "Вытянуть карту"
BTN_QUESTS = "Задания"
BTN_SHOP = "«Посмертие»"
BTN_DAILY = "Ежедневка"

QUESTS_TITLE = header("quest", "Задания")
QUESTS_NOTE = "<i>Задания дня обновляются в полночь по Киеву, задания недели — в понедельник.</i>"
QUESTS_DAILY_HEAD = "<b>На сегодня</b>"
QUESTS_WEEKLY_HEAD = "<b>На неделю</b>"
QUESTS_NONE = "Заданий нет."
QUEST_LINE = "{mark} {text} — {progress}/{target}. {reward}"
QUEST_MARK_OPEN = I["todo"]
QUEST_MARK_DONE = I["reward"]
QUEST_MARK_CLAIMED = I["ok"]
QUEST_REWARD = "Награда: {parts}."
QUEST_BTN_CLAIM = "Забрать {n}"
QUEST_CLAIMED = I["reward"] + " Получено за «{text}»: {parts}."
QUEST_CLAIM_LOST = "Эдди и репутацию получает только живой персонаж. Жетоны твои."
QUEST_CLAIM_GONE = "Такого задания уже нет."
QUEST_CLAIM_NOT_DONE = "Задание ещё не выполнено."
QUEST_CLAIM_ALREADY = "Награда уже получена."

REWARD_EDDIES = "эдди +{n}"
REWARD_XP = "опыт +{n}"
REWARD_TOKENS = "жетоны +{n}"
REWARD_STREET = "уличная репутация +{n}"
REWARD_STATUS = "статус +{n}"
REWARD_FAME = "слава +{n}"

SHOP_TITLE = header("afterlife", "«Посмертие»: стойка бармена")
SHOP_NOTE = I["tokens"] + " Жетонов: {tokens}. Здесь платят только жетонами, передать их другому нельзя."
SHOP_LINE = "<b>{name}</b> · {price} жет.\n{desc}"
SHOP_BOUGHT = I["shop"] + " Куплено: {name}."
SHOP_BOUGHT_CARD = I["shop"] + " Куплено: {name}. Новая карта дня: {card}."
SHOP_BOUGHT_TITLE = I["shop"] + " Куплено: {name}. Теперь у тебя титул «{title}»."
SHOP_BLOCKED: dict[str, str] = {
    "tokens": "Не хватает жетонов.",
    "character": "Нужен живой персонаж.",
    "have_stim": "Стим уже в крови. Следующий — после конца недели.",
    "no_active": "Активных имплантов нет, заряжать нечего.",
    "freeze_full": "Больше заморозок не унести.",
    "no_card": "Мисти сегодня ещё не гадала. Сначала вытяни карту.",
    "have_title": "Этот титул у тебя уже есть.",
    "no_cat": "Кота у тебя нет. Заведи: /cat.",
    "cat_gone": "Кот ушёл к соседу. Сначала верни его.",
    "have_item": "Этот хром на коте уже стоит.",
    "unknown": "У бармена такого нет.",
}

# -- mini-games (PLAN 4.7) ----------------------------------------------------------------------------------

CALIBER_HEAD = header("caliber", "Калибр: {who}")
CALIBER_DICE = I["dice"] + " На кубике {face}: {change} мм."
CALIBER_ROLL = I["weapon"] + " Теперь <b>{mm} мм</b> ({change}), титул: {title}."
CALIBER_NEW_TITLE = I["new"] + " Новый титул: <b>{title}</b>."
CALIBER_ALREADY = I["caliber"] + " {who}: сегодня замер уже был. Калибр {mm} мм, титул: {title}."
CALIBER_TOP_TITLE = header("caliber", "Калибры города")
CALIBER_TOP_ROW = "{n} {who} — <b>{mm} мм</b>, {title}"
CALIBER_TOP_EMPTY = "Пока никто не мерился. Открой счёт: /caliber."

DIRT_GAIN = I["dirt"] + " {who}: +{gain} компромата, всего {total}. Слив стоит {cost}: /dump @игрок."
DIRT_ALREADY = I["dirt"] + " {who}: на сегодня всё накопано. Компромата: {total}, слив стоит {cost}."
DUMP_USAGE = "Формат: /dump @игрок (или ответом на сообщение игрока)."
DUMP_BLOCKED: dict[str, str] = {
    "self": "Сливать компромат на себя нельзя.",
    "no_dirt": "Мало компромата: нужно {cost}, у тебя {have}. Копать можно раз в сутки: /dirt.",
    "no_target": "У цели нет живого персонажа, сливать некуда.",
    "cooldown": "Твой слив на этого игрока ещё свеж. Повторить можно через {days} дн.",
    "no_player": "Не знаю такого игрока.",
}
DUMP_OK = I["ncpd"] + " {target}: налёт NCPD по анонимной наводке. Розыск {heat}. Наводку дал {who}."
DUMP_DEFENDED_FIXER = (
    I["dump"] + " {who} сливает компромат, цель — {target}. Но фиксер района прикрыл своего, "
    "и бумеранг вернулся: под ударом теперь {who}."
)
DUMP_DEFENDED_CHECK = (
    I["dump"] + " {who} сливает компромат, цель — {target}. Но цель отбилась, "
    "и бумеранг вернулся: под ударом теперь {who}."
)

CAR_TITLE = header("car", "{name}")
CAR_POWER = I["ap"] + " Мощность: <b>{hp}</b> л.с."
CAR_PARTS_OK = I["ok"] + " Все детали на месте."
CAR_PARTS_MISSING = I["danger"] + " Не хватает деталей: {parts}."
CAR_TUNE_READY = I["time"] + " Сегодня можно заняться тюнингом."
CAR_TUNE_DONE = I["time"] + " Сегодня в моторе уже копались."
CAR_TUNE_BTN = "Тюнинг"
CAR_REPAIR_BTN = "Поставить: {part} · {cost}"
CAR_TUNE_UP = "Тюнинг удался: {change} л.с., теперь {hp}."
CAR_TUNE_DOWN = "Тюнинг не задался: {change} л.с., теперь {hp}."
CAR_TUNE_ZERO = "Тюнинг ничего не дал: по-прежнему {hp} л.с."
CAR_PART_LOST = "По дороге отвалилось: {part}. Это ещё −{loss} л.с."
CAR_REPAIRED = "Деталь на месте: {part}. Ушло {cost} эдди."
CAR_REPAIR_BLOCKED: dict[str, str] = {
    "character": "Ремонт оплачивает живой персонаж, а его нет.",
    "eddies": "Не хватает эдди на деталь.",
    "nothing": "Эта деталь и так на месте.",
}
RACE_USAGE = "Формат: /race @игрок [ставка в жетонах] (или ответом на сообщение игрока). Ставки: {stakes}."
RACE_BLOCKED: dict[str, str] = {
    "self": "Гоняться с самим собой нельзя.",
    "stake": "Такой ставки нет. Можно: {stakes}.",
    "no_car": "У тебя нет тачки. Заведи: /car.",
    "no_car_other": "У соперника нет тачки.",
    "no_tokens": "Не хватает жетонов на такую ставку.",
    "no_tokens_other": "У соперника не хватает жетонов на такую ставку.",
    "done_today": "Сегодня вы уже гонялись.",
    "no_player": "Не знаю такого игрока.",
}
RACE_HEAD = header("race", "Гонка: {a} против {b}")
RACE_SCORES = (I["car"] + " {a}: {hp} л.с.{lost} + {roll} = <b>{score}</b>\n"
               + I["car"] + " {b}: {hp2} л.с.{lost2} + {roll2} = <b>{score2}</b>")
ROLL_DICE = "кубик {face} → {roll}"  # a race or cat fight roll made by a Telegram dice
ROLL_PLAIN = "бросок {roll}"
RACE_LOST_PARTS = " − детали ({n})"
RACE_WIN = I["legends"] + " Первым приходит <b>{who}</b>.{stake}"
RACE_DRAW = I["race"] + " Ничья: тачки пришли бок о бок."
RACE_STAKE_LINE = " Забирает {n} жет."

CAT_TITLE = header("cat", "{name}") + ", {level_name}"
CAT_LEVEL = I["xp"] + " Уровень {level} из {max}. Кормёжек: {feedings}{next}."
CAT_NEXT = ", до следующего уровня ещё {n}"
CAT_MOOD: dict[str, str] = {
    "ok": I["mood"] + " Сыт и доволен.",
    "sulk": I["mood"] + " Обижен: давно не кормили.",
    "gone": I["danger"] + " Ушёл к соседу. Вернуть можно за жетоны.",
}
CAT_CHROME = I["chrome"] + " Хром: {items}."
CAT_NO_CHROME = I["chrome"] + " Хрома нет. Его продаёт бармен в «Посмертии»."
CAT_FEED_READY = I["time"] + " Сегодня ещё не кормлен."
CAT_FED = I["time"] + " Сегодня уже накормлен."
CAT_FEED_BTN = "Покормить"
CAT_RETURN_BTN = "Вернуть кота · {cost} жет."
CAT_FED_NOW = "Кот ест. Всего кормёжек: {feedings}."
CAT_LEVEL_UP = "Кот подрос: {level_name}."
CAT_RETURN_OK = "Кот вернулся. Смотрит косо, но дома."
CAT_RETURN_NO = "Не хватает жетонов."
CAT_FIGHTS_TODAY = I["catfight"] + " Кошачий бой сегодня: {state}."
CAT_FIGHT_OPEN = "свободен, /catfight @игрок"
CAT_FIGHT_USED = "уже был"
CATFIGHT_USAGE = "Формат: /catfight @игрок [ставка в жетонах] (или ответом на сообщение игрока). Ставки: {stakes}."
CATFIGHT_BLOCKED: dict[str, str] = {
    "self": "Стравить кота с самим собой не выйдет.",
    "stake": "Такой ставки нет. Можно: {stakes}.",
    "no_cat": "У тебя нет кота. Заведи: /cat.",
    "no_cat_other": "У соперника нет кота.",
    "gone": "Твой кот ушёл к соседу.",
    "gone_other": "Кот соперника ушёл к соседу.",
    "tired": "Твой кот сегодня уже дрался.",
    "tired_other": "Кот соперника сегодня уже дрался.",
    "no_tokens": "Не хватает жетонов на такую ставку.",
    "no_tokens_other": "У соперника не хватает жетонов на такую ставку.",
    "no_player": "Не знаю такого игрока.",
}
CATFIGHT_HEAD = header("catfight", "Кошачий бой: {a} против {b}")
CATFIGHT_SCORES = (I["cat"] + " {a}{sulk}: <b>{score}</b> ({roll})\n"
                   + I["cat"] + " {b}{sulk2}: <b>{score2}</b> ({roll2})")
CATFIGHT_SULK = ", обижен"
CATFIGHT_WIN = I["legends"] + " Побеждает <b>{who}</b>.{stake}"
CATFIGHT_DRAW = I["catfight"] + " Ничья. Коты разошлись, шерсть дыбом."

COIN_TITLE = header("crypto", "Эдди-крипта")
COIN_RATE = I["eddies"] + " Курс: {rate} эдди за монету{delta}."
COIN_DELTA = " ({pct} за сутки)"
COIN_RANGE = I["date"] + " Курс меняется каждые полчаса. За последние {days} изм.: от {lo} до {hi}."
COIN_WALLET = I["crypto"] + " Кошелёк: {coins} мон., по курсу {worth} эдди. Монеты переживают смерть персонажа."
COIN_EDDIES = I["profile"] + " У персонажа: {eddies} эдди."
COIN_NO_CHAR = "Живого персонажа нет: покупать и продавать не на что. Кошелёк подождёт."
COIN_TOP_HEAD = header("legends", "Самые толстые кошельки")
COIN_TOP_ROW = "{n} {who} — {coins} мон. ({worth} эдди)"
COIN_FEE = I["note"] + " Комиссия при продаже: {pct}%."
COIN_BUY_HINT = "Купить монет на сумму (эдди):"
COIN_SELL_HALF = "Продать 50%"
COIN_SELL_ALL = "Продать всё"
COIN_BOUGHT = I["ok"] + " Куплено {coins} мон. за {eddies} эдди по курсу {rate}."
COIN_SOLD = I["money_in"] + " Продано {coins} мон. по курсу {rate}. На руки {eddies} эдди, комиссия {fee}."
COIN_BLOCKED: dict[str, str] = {
    "character": "Нужен живой персонаж: монеты покупают и продают только за его эдди.",
    "min": "Слишком мелкая сделка. Минимум {min} эдди.",
    "eddies": "Не хватает эдди.",
    "coins": "Кошелёк пуст.",
}

BTN_CAR = "Тачка"
BTN_CAT = "Кот"
BTN_COIN = "Крипта"

# -- reminders and the weekly post (PLAN 4.7) ------------------------------------------------------------------

REMIND_NOON = I["misty"] + " Ежедневка обновилась. Мисти ждёт: /daily"
REMIND_EVENING = I["danger"] + " Серия под угрозой, чумбы: {names}. Заберите ежедневку до полуночи: /daily"
WHO_TITLE = header("who", "Кто жив в Найт-Сити")
WHO_ROW = I["profile"] + " <b>{name}</b> — {roles}, {district}, {age}"
WHO_NO_ROLE = "без роли"
WHO_EMPTY = "Пока никого нет. Начать игру: /play."
ROLE_CALLED = I["roles"] + " Город начинает звать тебя {role}."
ROLE_RANK_UP = I["roles"] + " {role}: ранг {rank}."
ROLE_PERK = I["roles"] + " {role}: ранг {rank}. Новый перк «{perk}»: {mods}."
ROLE_INSTRUMENTAL: dict[str, str] = {
    "solo": "Соло", "netrunner": "Нетраннером", "techie": "Техником", "medtech": "Медтехом",
    "fixer": "Фиксером", "rockerboy": "Рокербоем", "media": "Медиа", "corpo": "Корпоратом",
    "lawman": "Копом", "nomad": "Кочевником", "monk": "Монахом", "hustler": "Аферистом",
    "trader": "Торговцем",
}

LEGENDS_SCALES = {"street": "Уличная репутация", "status": "Статус", "fame": "Слава"}
LEGENDS_TABS = {"street": "Улица", "status": "Статус", "fame": "Слава"}
LEGENDS_ALREADY = "Это и так «{scale}»."
LEGENDS_TITLE = header("legends", "Легенды: {scale}")
LEGENDS_HINT = {  # shown in italics
    "street": "Кого знают в подворотнях, у фиксеров и в бандах.",
    "status": "Кого пускают в кабинеты наверху.",
    "fame": "Чьё лицо узнают фанаты, дроны и лента.",
}
LEGENDS_ROW = "{n} {who} — <b>{v}</b>"
LEGENDS_EMPTY = "Пока ни у кого нет очков по этой шкале."
BTN_LEGENDS = "Легенды"

LEGENDS_WEEK_TITLE = header("legends", "Легенды недели")
LEGENDS_WEEK_STREET = I["street_cred"] + " Уличная репутация: {who} (+{n})."
LEGENDS_WEEK_STATUS = I["status"] + " Статус: {who} (+{n})."
LEGENDS_WEEK_FAME = I["fame"] + " Слава: {who} (+{n})."
LEGENDS_WEEK_CALIBER = I["caliber"] + " Калибр: {who}, {mm} мм ({title})."
LEGENDS_WEEK_DUMP = I["dump"] + " Громкий слив: {a} сдал NCPD компромат. Цель: {b}."
LEGENDS_WEEK_QUIET = I["time"] + " Тихая неделя: никто не вырос. Город зевает."


# owner request: many sessions of one activity at once
ACT_REPEAT_NOTE = "<i>Повтор: {n}. Нажми занятие или дело — оно пройдёт столько раз подряд, пока хватает ОД и денег.</i>"
ACT_MANY_DONE = "Сделано подряд: <b>{done}</b> из {asked}."
ACT_MANY_STOPPED = "<i>Остановилось раньше: {why}</i>"
