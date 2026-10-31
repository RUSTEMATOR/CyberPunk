"""Texts of the city raid (balance.players.raid). Everything here is seen by the player."""

from __future__ import annotations

from app.bot.ui.style import ICONS as I
from app.bot.ui.style import header

TITLE = header("war", "Рейд: {name}")
HP = I["hp"] + " {bar} {hp}/{max}"
TIME_LEFT = I["time"] + " До конца: {h} ч {m} мин."
SUPPORT = I["trauma"] + " Поддержка команды: +{bonus} к проверке у следующих атак ({n})."
TOP_HEAD = "<b>Урон</b>"
TOP_LINE = "{n}. {name} — {dmg}"
TOP_EMPTY = "Пока никто не ударил. Будь первым."
YOU_DONE = "Сегодня ты уже бил{a}: всего {dmg} урона. Следующая атака — завтра."
YOU_READY = "Твоя атака сегодня: {ap} ОД. Слабое место отмечено ★."
NONE = header("war", "Рейд") + "\nСейчас в городе тихо. Новый рейд объявят в группе: он появляется раз в несколько дней, вечером."
APPROACH = {"shoot": "Стрелять", "melee": "Рукопашная", "hack": "Взлом", "support": "Поддержка"}
APPROACH_HELP = (
    "<i>Стрелять</i> — РЕФЛ + стрельба. <i>Рукопашная</i> — ТЕЛО + ближний бой. <i>Взлом</i> — ИНТ + нетраннинг.\n"
    "<i>Поддержка</i> — ТЕХ + медицина: урона вдвое меньше, зато при успехе следующие атаки других получают +{bonus}.\n"
    "Награда делится по вкладу: урон плюс удачная поддержка. Промах тоже царапает босса, но он бьёт в ответ."
)
WEAK_MARK = " ★"
BTN_MENU = "Меню"
BTN_REFRESH = "Обновить"
BLOCKED = {
    "none": "Рейда сейчас нет.",
    "today": "Сегодня ты уже бил. Приходи завтра.",
    "jail": "Из камеры на рейд не выйти.",
}
NO_AP = "ОД кончились. Нажми «Прожить месяц»."
HIT = I["fight"] + " <b>{damage}</b> урона{weak}{support}."
HIT_WEAK = ", по слабому месту"
HIT_SUPPORT = " (с поддержкой +{n})"
HIT_SUPPORT_LEFT = I["trauma"] + " Ты прикрываешь своих: следующие атаки других получат +{bonus}."
HIT_BACK = I["danger"] + " Босс бьёт в ответ: −{hp} здоровья."
KILLED = I["ok"] + " <b>Босс повержен твоим ударом!</b> Награды придут всем, кто бил, как только откроют окно."

# the group posts
POST_SPAWN = "🚨 <b>Рейд: {name}</b>\n{text}\n\nЗдоровье: {hp}. Каждый может ударить раз в сутки, на всё 48 часов: /raid"
POST_WON = "🏆 <b>Рейд выигран: {name}.</b>\n{text}\n\n{table}"
POST_ESCAPED = "💨 <b>Рейд сорван: {name}.</b>\n{text}\n\n{table}"
POST_ROW = "{n}. {name} — {dmg} урона, {eddies} эдди"
POST_NOBODY = "Никто так и не вышел против него."

# the reward letter (menu notice)
GOT_WON = "Рейд «{name}»: {place}-е место по вкладу. +{eddies} эдди, уличная репутация +{cred}."
GOT_TOP = " Трофей лучшему: {item}."
GOT_ESCAPED = "Рейд «{name}» сорвался, босс ушёл. За участие: +{eddies} эдди."

MENU_BUTTON = "Рейд"
MENU_LINE = I["war"] + " Рейд: {name}, {pct}% здоровья. /raid"
