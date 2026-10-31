"""Notes on words the player may not know (owner request 24.10: «не могу понять, что такое биоимпланты»).

`notes(text)` finds terms in a text and returns short explanations to put under it (events, world news); the handbook
shows the whole list. `effects_note` turns the world effects of a news item into what changes in the game.
"""

from __future__ import annotations

import re
from typing import Any

from app.bot.texts import esc

# (stems to look for in lowercase text, the term, the note). Stems are word starts: «биоимплант» finds «биоимпланты».
GLOSSARY: list[tuple[tuple[str, ...], str, str]] = [
    (("биоимплант", "биовар", "выращенн"), "Биоимпланты",
     "импланты, выращенные из живых тканей, а не из металла. Тело принимает их легче хрома, но они дороже и могут не прижиться."),
    (("хром",), "Хром", "любые кибернетические импланты. Чем больше хрома, тем сильнее нагрузка на тело и тем быстрее уходит человечность."),
    (("человечност",), "Человечность", "насколько ты ещё человек. Её съедают импланты и жестокость. На нуле — киберпсихоз."),
    (("киберпсих",), "Киберпсихоз", "срыв, когда хрома больше, чем человека. Персонаж становится опасен для всех, за ним приходит МаксТак."),
    (("подавител", "иммунодепрессант", "иммуноподавител"), "Подавители",
     "таблетки и уколы, чтобы тело не отторгало импланты. Нужны, когда нагрузка хрома высокая. Без них — штрафы и отторжение."),
    (("нагрузк",), "Нагрузка хрома", "сумма «тяжести» всех имплантов. От неё зависит, какой курс подавителей нужен."),
    (("риппер",), "Риппер", "уличный хирург, который ставит и снимает импланты."),
    (("фиксер",), "Фиксер", "посредник: даёт заказы, свёл бы с кем угодно за процент."),
    (("нетраннер", "нетраннинг"), "Нетраннер", "хакер, который подключается к Сети через деку в голове."),
    (("лёд", "лед ", "чёрный лёд", "ice"), "Лёд", "защита сетей и серверов. Чёрный лёд бьёт по мозгу того, кто ломает."),
    (("заслон", "blackwall"), "Чёрный заслон", "стена вокруг старой Сети, за ней живут дикие ИИ. Туда не ходят."),
    (("макстак", "maxtac"), "МаксТак", "спецотряд полиции против киберпсихов. Стреляют первыми."),
    (("trauma team", "травма тим"), "Trauma Team", "частная скорая с пулемётами. Подписка спасает от смерти: платина — почти всегда."),
    (("ncpd",), "NCPD", "полиция Найт-Сити."),
    (("эдди",), "Эдди", "евродоллары, деньги этого мира."),
    (("чумба",), "Чумба", "друг, свой человек (сленг)."),
    (("корпо", "корпорат"), "Корпорат", "человек корпорации: костюм, контракт, совесть по уставу."),
    (("брейнданс",), "Брейнданс", "запись чужих ощущений, которую проживаешь как свою. Затягивает."),
    (("энграмм", "secure your soul"), "Энграмма", "цифровая копия личности. Её можно оплатить заранее, чтобы после смерти она проснулась в наследнике."),
    (("глюк",), "Глюки", "очки, которыми можно перебросить неудачную проверку. Копятся медленно, берегут жизнь."),
    (("розыск",), "Розыск", "насколько ищет полиция. Высокий розыск — облавы и МаксТак."),
    (("уличн",), "Уличная репутация", "уважение на улицах: открывает заказы, банды и фиксеров."),
    (("сандевистан",), "Сандевистан", "имплант, который замедляет мир вокруг тебя на несколько секунд."),
    (("кибердек", "деку", "дека "), "Дека", "компьютер нетраннера, вшитый в голову."),
    (("мегабашн",), "Мегабашня", "огромный жилой дом-город, где живут тысячи людей."),
    (("кочевник", "номад"), "Кочевники", "кланы, что живут за городом в дороге: семья важнее всего."),
    (("вылазк",), "Вылазка", "поход по куску района: карта, бандиты, тайники («Город» → «Вылазка»)."),
    (("инжектор",), "Инжектор", "аптечка в бою: лечит, заряды возвращаются через несколько ходов."),
]


# words every player meets in the first hour: not explained under every event (the handbook has them)
COMMON = {"Эдди", "Чумба", "Хром", "Человечность", "Риппер", "Фиксер", "Розыск", "Уличная репутация", "Корпорат", "NCPD",
          "Глюки"}


def notes(text: str, limit: int = 2, skip: set[str] | None = None) -> list[str]:
    """Short notes for the terms found in `text` (each term once), formatted as lines."""
    low = (text or "").lower()
    out: list[str] = []
    for stems, term, note in GLOSSARY:
        if skip and term in skip:
            continue
        if any(re.search(r"(?<![а-яёa-z])" + re.escape(s), low) for s in stems):
            out.append(f"📌 <b>{esc(term)}</b> — {esc(note)}")
            if len(out) >= limit:
                break
    return out


def handbook_text() -> str:
    return "\n".join(f"<b>{esc(term)}</b> — {esc(note)}" for _, term, note in sorted(GLOSSARY, key=lambda x: x[1]))


_PRICE = {"combat": "боевые импланты", "noncombat": "небоевые импланты", "netrun": "сетевые импланты и софт",
          "weapons": "оружие", "armor": "броня", "housing": "жильё", "vehicles": "транспорт", "drugs": "химия"}
_EVENTS = {"street": "уличных", "implants": "про импланты", "money": "про деньги", "relationships": "про отношения",
           "ncpd": "с полицией", "work": "на работе", "rare": "редких"}


def _pct(v: float) -> int:
    return round(abs(v - 1.0) * 100)


def effects_note(content: Any, effects: dict[str, Any]) -> str:
    """«🎮 В игре: ...» for the world effects of a news item; empty when nothing readable."""
    bits: list[str] = []
    for key, raw in (effects or {}).items():
        base, _, param = str(key).partition(".")
        try:
            if base == "price_mult" and param:
                v = float(raw)
                if abs(v - 1) > 1e-6:
                    bits.append(f"{_PRICE.get(param, param)} {'дешевле' if v < 1 else 'дороже'} на {_pct(v)}%")
            elif base == "event_weight" and param:
                v = float(raw)
                if abs(v - 1) > 1e-6:
                    bits.append(f"событий {_EVENTS.get(param, param)} {'больше' if v > 1 else 'меньше'}")
            elif base == "heat_mult":
                v = float(raw)
                bits.append(f"розыск растёт {'быстрее' if v > 1 else 'медленнее'}")
            elif base == "gig_pay_mult":
                v = float(raw)
                bits.append(f"заказы платят {'больше' if v > 1 else 'меньше'} на {_pct(v)}%")
            elif base == "district_danger" and param:
                d = content.districts.get(param)
                n = int(raw)
                bits.append(f"район {d.name if d else param} {'опаснее' if n > 0 else 'спокойнее'}")
            elif base == "faction_power" and param:
                f = content.gangs.get(param) or content.corps.get(param)
                bits.append(f"{f.name if f else param} {'сильнее' if int(raw) > 0 else 'слабее'}")
            elif base == "gang_war" and param:
                g = content.gangs.get(param)
                bits.append(f"{g.name if g else param} воюют {int(raw)} нед.")
            elif base in ("unlock", "ban") and param:
                rec = (content.cyberware.get(param) or content.weapons.get(param) or content.armor.get(param)
                       or content.quickhacks.get(param))
                bits.append(f"{'в продаже' if base == 'unlock' else 'запрещено'}: {rec.name if rec else param}")
            elif base in ("district_open", "district_close") and param:
                d = content.districts.get(param)
                bits.append(f"район {d.name if d else param} {'открыт' if base == 'district_open' else 'закрыт'}")
        except (TypeError, ValueError):
            continue
    return ("🎮 <b>В игре:</b> " + esc("; ".join(bits)) + ".") if bits else ""
