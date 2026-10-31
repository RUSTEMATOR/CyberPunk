"""Player-facing lines of event payoffs: delayed outcomes, granted items and implants, the money block of the week
report, and the admin compensation commands. Russian, short. Telegram HTML: names from content go through `esc`."""

from __future__ import annotations

# -- outcome lines (views.effects_lines) ------------------------------------------------------------------------

FX_IMPLANT = "+1 имплант: {name}"
FX_IMPLANT_LOST = "−1 имплант: {name}"
FX_WEAPON = "+1 оружие: {name}"
FX_ARMOR = "+1 броня: {name}"
FX_VEHICLE = "Транспорт: {name}"
FX_VEHICLE_SOLD = "Старый транспорт ({name}) ушёл дилеру за {n}"
FX_LATER = "через {weeks}: {hint}"
FX_LATER_HINT = "станет ясно, чем это кончится"
FX_OVERDRAFT = "эдди не хватило: {n} ушло в долг"
FX_TOLERANCE = "кибертолерантность: {name}"
FX_DEBT_GRACE = "кредиторы ждут: пропущенные недели списаны"
FX_CAREER_NEW = "новая работа: {name}"
FX_CAREER_GONE = "работы больше нет"
FX_EMPLOYER = "работодатель: {name}"
WEEKS = ("неделю", "недели", "недель")

# -- the week report ----------------------------------------------------------------------------------------------

WEEK_EVENTS_MONEY = "События с прошлого отчёта: {eddies} эдди."
WEEK_EVENTS_MONEY_DEBT = "События с прошлого отчёта: {eddies} эдди, долг {debt}."
WEEK_EDDIES_TOTAL = "Итого за ход: {delta} эдди, на счету {eddies}."
WEEK_LATER_TITLE = "<b>Отложенные итоги</b>"

# -- admin --------------------------------------------------------------------------------------------------------

ADMIN_USAGE_GRANT_CYBER = "Формат: /admin_grant_cyber @игрок ключ_импланта (или ответом на сообщение игрока)."
ADMIN_USAGE_GRANT_ITEM = "Формат: /admin_grant_item @игрок ключ_оружия_или_брони (или ответом на сообщение игрока)."
ADMIN_GRANT_UNKNOWN = "Ключа {key} нет в каталоге."
ADMIN_GRANT_NOT_IN_YEAR = "{key} не существует в {year} году персонажа. Всё равно выдано."
ADMIN_GRANT_DONE = "{name}: выдано {what}."
ADMIN_GRANT_NOTHING = "{name}: ничего не изменилось ({why})."
