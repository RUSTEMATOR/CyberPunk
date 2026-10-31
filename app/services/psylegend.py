"""A fugitive cyberpsycho's way into legend (owner request 24.10): the deeds, the titles and the posts.

Every deed is a forced event `psyleg_<deed>` (content/events_psycho_legend*.yaml) that sets its flag. When the flag is set
(services/events.finalize calls `on_resolved`) the whole group gets a separate post that is never deleted
(event_log kind `legend`, engine/feed.py CLASS_PERMANENT). Some deeds take the player's own words first (the broadcast,
the cocktail, the graffiti): flag `psyleg_say` = {deed: text}; a signature for every post: flag `psyleg_sign`.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.texts import esc
from app.db import repo
from app.services.game import ActiveCharacter

FLAG_SAY = "psyleg_say"
FLAG_SIGN = "psyleg_sign"
SIGN_MAX = 120

# flag -> (button, line, AP)
DEEDS: dict[str, tuple[str, str, int]] = {
    "psyleg_studio": ("Захватить эфир", "Студия «Новостей 54»: сказать городу всё в прямом эфире. Свои слова.", 2),
    "psyleg_graffiti": ("Надпись на Мегабашне", "Тридцать этажей краски на фасаде Мегабашни H8. Свои слова.", 2),
    "psyleg_afterlife": ("Коктейль в «Афтерлайфе»", "Бар легенд наёмников. Свой коктейль в меню, своё название.", 1),
    "psyleg_vault": ("Хранилище «Милитеха»", "Наличные для чёрных операций под доками. Двадцать охранников.", 2),
    "psyleg_lab": ("Лаборатория «Биотехники»", "Люди на столах, хром на испытаниях. Освободить или забрать.", 2),
    "psyleg_precinct": ("Участок NCPD", "Взять участок или сжечь архив с делами улицы.", 2),
    "psyleg_gang": ("Логово Мальстрёма", "Вырезать фанатиков хрома или стать их богом.", 2),
    "psyleg_pacifica": ("Король Боевой зоны", "Пасифика без закона. Вудуисты, мародёры и пустой трон.", 2),
    "psyleg_bridge": ("Перекрыть мост", "Мост в Уотсон, час пик, весь город стоит и смотрит.", 2),
    "psyleg_concert": ("Сцена на концерте", "Стадион, сорок тысяч зрителей, чужой концерт.", 2),
    "psyleg_av": ("Угнать ави NCPD", "Полицейский ави с площадки участка, и круг над городом.", 2),
    "psyleg_council": ("Городской совет", "Заседание в мэрии. Камеры, депутаты, охрана.", 2),
    "psyleg_net": ("Имя в Сети", "Прорваться к узлу NetWatch и вписать своё имя в каждый экран.", 2),
    "psyleg_hunters": ("Охота на МаксТак", "Засада на отряд, который охотится на тебя. Опасность 5.", 2),
    "psyleg_exec": ("Вице-президент «Арасаки»", "Ужин на крыше Корпо-плазы. Выкуп или признание в камеру.", 2),
    "psyleg_smasher": ("Вызов Смэшеру", "Главный пёс «Арасаки». Нужны 5 дел легенды.", 3),
    "psyleg_tower_1": ("Башня: заряд", "Тактический заряд кочевников. Нужны 3 дела легенды.", 2),
    "psyleg_tower_2": ("Башня: проникновение", "Довезти заряд до опорных колонн башни «Арасаки».", 3),
    "psyleg_tower_3": ("Башня: детонация", "Крыша в Уотсоне, детонатор в руке.", 1),
}
TOWER = ("psyleg_tower_1", "psyleg_tower_2", "psyleg_tower_3")

# deeds that ask for the player's words first: deed -> (prompt, max chars)
WORDS: dict[str, tuple[str, int]] = {
    "psyleg_studio": ("Что ты скажешь городу в прямом эфире? Напиши в чат (до 300 символов). "
                      "Это увидит вся беседа, пост не удаляется.", 300),
    "psyleg_graffiti": ("Что будет написано на фасаде Мегабашни? Напиши в чат (до 80 символов).", 80),
    "psyleg_afterlife": ("Как назовут твой коктейль в «Афтерлайфе»? Напиши в чат (до 40 символов).", 40),
}

# the post of a done deed: what the city saw ({who} is the escaped name; present tense, no gender)
HEADLINES: dict[str, str] = {
    "psyleg_studio": "{who} врывается в студию «Новостей 54» и забирает прямой эфир. Весь Найт-Сити смотрит.",
    "psyleg_graffiti": "Утром Найт-Сити просыпается с новой надписью на Мегабашне H8: тридцать этажей краски. "
                       "Автор известен — {who}.",
    "psyleg_afterlife": "В «Афтерлайфе» новый коктейль в меню. Так бар отмечает только тех, о ком уже поют: {who}.",
    "psyleg_vault": "{who} вскрывает хранилище «Милитеха» под доками. Наличные чёрных операций уходят в ночь.",
    "psyleg_lab": "{who} приходит в закрытый корпус «Биотехники». Утром двери палат открыты, а город знает, что там было.",
    "psyleg_precinct": "{who} заходит в участок NCPD в Уотсоне. Дела улицы горят, копы молчат.",
    "psyleg_gang": "{who} спускается в логово Мальстрёма. Фанатики хрома больше не прежние.",
    "psyleg_pacifica": "В Боевой зоне Пасифики новый хозяин: {who}. Мародёры уступают дорогу.",
    "psyleg_bridge": "{who} перекрывает мост в Уотсон в час пик. Город стоит в пробке и смотрит.",
    "psyleg_concert": "{who} выходит на сцену чужого концерта перед сорока тысячами. Стадион не расходится.",
    "psyleg_av": "Полицейский ави кружит над Сити-центром с выключенными маяками. За штурвалом — {who}.",
    "psyleg_council": "{who} врывается на заседание городского совета. Трансляция идёт на весь город.",
    "psyleg_net": "На каждом экране Найт-Сити на минуту одно и то же имя: {who}. NetWatch не может это стереть.",
    "psyleg_hunters": "{who} устраивает засаду на отряд МаксТака. Охотники стали дичью.",
    "psyleg_exec": "{who} находит вице-президента «Арасаки» на крыше Корпо-плазы. Ужин заканчивается не по плану.",
    "psyleg_smasher": "{who} вызывает на бой главного пса «Арасаки». Об этой встрече будут спорить годами.",
    "psyleg_tower_1": "Кочевники передают кому-то свинцовый ящик со времён Четвёртой корпоративной. "
                      "На улицах шепчут имя: {who}.",
    "psyleg_tower_2": "Под башней «Арасаки» что-то стоит у опорных колонн. Охрана ищет, кто привёз. Город уже знает: {who}.",
    "psyleg_tower_3": "Рассвет над Найт-Сити. Башни «Арасаки» больше нет. Детонатор нажимает {who}.",
}
WORDS_LINE = {
    "psyleg_studio": "В эфире прозвучало:",
    "psyleg_graffiti": "На фасаде:",
    "psyleg_afterlife": "Коктейль:",
}


def done(st, key: str) -> bool:  # noqa: ANN001
    # the first detonation set only `psyleg_tower` (24.10): it counts as the last stage too
    return bool(st.flags.get(key) or (key == "psyleg_tower_3" and st.flags.get("psyleg_tower")))


def count(st) -> int:  # noqa: ANN001
    return sum(1 for k in DEEDS if done(st, k))


def title(n: int) -> str:
    if n == 0:
        return "тень в переулках"
    if n < 3:
        return "уличный ужас"
    if n < 7:
        return "кошмар Найт-Сити"
    if n < 12:
        return "легенда улиц"
    if n < 17:
        return "миф, который рассказывают детям"
    return "сам Найт-Сити"


def is_open(st, key: str) -> bool:  # noqa: ANN001
    if done(st, key):
        return False
    base = sum(1 for k in DEEDS if k not in TOWER and st.flags.get(k))
    if key == "psyleg_tower_1":
        return base >= 3
    if key == "psyleg_tower_2":
        return bool(st.flags.get("psyleg_tower_1"))
    if key == "psyleg_tower_3":
        return bool(st.flags.get("psyleg_tower_2"))
    if key == "psyleg_exec":
        return base >= 1
    if key == "psyleg_smasher":
        return base >= 5
    return True


def clean(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


async def announce(session: AsyncSession, ch: ActiveCharacter, deed: str, how: str = "", ok: bool | None = None) -> None:
    """The permanent post of a done deed (event_log kind `legend`, posted by the city feed)."""
    st = ch.state
    who = f"<b>{esc(f'{st.name} {st.surname}'.strip())}</b>"
    n = count(st)
    lines = ["🔥 <b>ЛЕГЕНДА НАЙТ-СИТИ</b>", "", HEADLINES.get(deed, "{who} делает то, о чём будут говорить.").format(who=who)]
    said = dict(st.flags.get(FLAG_SAY) or {})
    words = said.pop(deed, None)
    if said:
        st.flags[FLAG_SAY] = said
    else:
        st.flags.pop(FLAG_SAY, None)
    if words:
        lines += ["", f"{WORDS_LINE.get(deed, 'Слова:')} «{esc(str(words))}»"]
    if how:
        tail = "" if ok is None else (" Получилось." if ok else " Пошло не по плану, но город запомнил.")
        lines += ["", f"<i>Как это было: «{esc(how)}».{tail}</i>"]
    sign = st.flags.get(FLAG_SIGN)
    if sign:
        lines.append(f"На месте оставлено: «{esc(str(sign))}»")
    lines += ["", f"Дел легенды: {n}/{len(DEEDS)} · {title(n)}."]
    await repo.log_event(session, "legend", {"text": "\n".join(lines), "deed": deed, "ref": deed}, character_id=ch.id, public=True)


async def on_resolved(session: AsyncSession, ch: ActiveCharacter, event_id: str, label: str, ok: bool | None) -> None:
    """services/events.finalize: a legend event set its flag just now."""
    flag = "psyleg_tower" if event_id == "psyleg_tower_3" else event_id
    if event_id not in DEEDS or not ch.state.flags.get(flag):
        return
    if event_id == "psyleg_tower_3":
        ch.state.flags["psyleg_tower_3"] = True
    await announce(session, ch, event_id, label, ok)
