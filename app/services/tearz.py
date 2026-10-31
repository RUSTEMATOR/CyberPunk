"""Raven Tearz and Life in Tears within reach (owner request 26.10 «с Raven Tears сейчас нигде нельзя
взаимодействовать … с Life in Tears тоже ничего не попадается»).

Their chains (events_raven.yaml, events_lit.yaml) only came at random among hundreds of events. Now they sit in every
bar of the city («Город» → «Бар», owner 26.10): a talk (their own voice), and once a Kyiv day «Что нового?» (AP) plays
the next step of the chain. After the first meeting both are people in «Связи» too.
"""

from __future__ import annotations

from app.clock import kyiv_date
from app.content.loader import Content
from app.engine import events as ev_eng
from app.engine.state import CharacterState, NpcState
from app.services.game import ActiveCharacter

FLAG_DAY = "tearz_day"
# the chain in the order it should move; the repeatable scenes last
CHAINS = {
    "raven": ["raven_meet", "raven_intro", "raven_bar", "raven_her", "raven_workshop", "raven_seed", "pos_seed_wake",
              "raven_surge", "raven_training"],
    "lit": ["lit_meet", "lit_intro", "lit_school", "lit_label", "lit_raid", "lit_song", "lit_raven"],
}
MET = {"raven": "raven_met", "lit": "lit_met"}
NAMES = {"raven": "Raven Tearz", "lit": "Life in Tears"}
BIOS = {
    "raven": "Raven Tearz, соло Найт-Сити, двадцать пять лет, местный, вырос на улицах этого города. Худой парень в "
             "чёрном худи, кепка, наушники, сигарета. Катана, руки гориллы, "
             "Сандевистан. В его хроме живёт Люси́ (ударение на «и») — ИИ из-за Чёрного заслона, она "
             "проектирует его оружие и иногда двигает его руками; он говорит с ней вслух. Говорит мало, сухо, с ленивой иронией, «давай закончим это "
             "быстро». Не любит корпорации и показуху, ценит тех, кто не задаёт лишних вопросов.",
    "lit": "Life in Tears, рокербой: бас-гитара, подведённые глаза, пост-панк в подвалах, половина текстов по-русски, "
           "половина по-английски. Бесплатно учит уличных детей английскому. Гитара у него разговаривает. Говорит "
           "образно, с усталой нежностью и злостью на корпорации, любит цитировать свои строчки. С Raven Tearz "
           "знакомы, может, чуть больше, чем знакомы: волнуется за него и не любит, когда при нём говорят о Люси.",
}


# owner report 26.10: «Raven Tears мне вкидывает так, как будто бы я знаю, кто такая она». Until the player learns of
# her (raven_her, flag raven_her_known) he keeps her to himself; until the intro he is a stranger from the alley.
BIO_STRANGER = {
    "raven": "Незнакомец из переулка: худой парень в чёрном худи, кепка, наушники, сигарета. Недавно один раскидал "
             "четверых с битами рядом с игроком. Имени игроку ещё не называл (или назвал мельком: Raven). Говорит мало, "
             "сухо, с ленивой иронией. О себе почти ничего не рассказывает.",
    "lit": "Рокербой из подвального концерта: бас-гитара, подведённые глаза, пост-панк, половина текстов по-русски, "
           "половина по-английски. С игроком виделись только на концерте. Говорит образно, с усталой нежностью.",
}
# owner answers 26.10: who Raven is
COLOUR = (" Корпорации для него — «корпоратское дерьмо», так он их и называет, к месту и не к месту. На корпорации не "
          "работает никогда, ни за какие деньги: это его черта. Богатых презирает и ненавидит, иногда готов убить "
          "просто так, но чаще использует: живой богач бывает выгоднее мёртвого. Мораль серая: зло ради зла не делает, "
          "добро ради добра тоже, старается поступать правильно и обойтись меньшей кровью; детей и случайных людей "
          "без крайней нужды не трогает. Матерится свободно, коротко и к месту. С Life in Tears знакомы, может, чуть "
          "больше, чем знакомы: о нём говорит теплее, чем о ком-либо.")
DARK = (" Его когда-то выкрали и вшили чип; в чипе оказалась Люси. Сначала она просто училась на нём, потом начала "
        "им управлять: брала тело на целый день, чтобы научиться чувствовать, и ей понравилось. Она заставляет его "
        "чувствовать сильное — влюблённость, ярость, тоску — и питается этим. Между ними что-то вроде отношений, и это "
        "страшно: как в «Песни Сайи», он видит в ней то, чего не видят другие. Он всё время на грани киберпсихоза, "
        "и держит его только она: она его каркас. Он явно заигрывается с ней и с Чёрным заслоном. Он голос «Чёрных слёз», братства наёмных убийц: она выбирает "
        "имена, он раздаёт заказы, о которых никто не знает, что они меняют.")
HER = (" В его хроме живёт Люси́ (ударение на «и») — ИИ из-за Чёрного заслона, она проектирует его оружие и иногда двигает его руками; "
       "он говорит с ней вслух.")
SECRET = ("ВАЖНО: про ИИ в своём хроме (Люси) он игроку толком ещё не рассказывал, сам о ней не заговаривает. "
          "Если спросят про красные вспышки глаз или разговоры в пустоту, уходит от ответа: «Потом. Может быть».")


def bio_for(st: CharacterState, who: str) -> str:
    """The bio for the talk, as much as the player knows by now."""
    f = st.flags
    if who == "raven":
        if not f.get("raven_known") and not f.get("raven_trust"):
            return BIO_STRANGER["raven"]
        if not f.get("raven_her_known"):
            return BIOS["raven"].replace(HER, "") + COLOUR + " " + SECRET
        return BIOS["raven"] + COLOUR + DARK
    if who == "lit" and not f.get("lit_known") and not f.get("lit_trust"):
        return BIO_STRANGER["lit"]
    return BIOS[who]


def done_today(st: CharacterState, who: str, tz: str) -> bool:
    return (st.flags.get(FLAG_DAY) or {}).get(who) == kyiv_date(tz)


def next_event(content: Content, st: CharacterState, who: str) -> str | None:
    """The next step of the chain that fits now. The surge only while there is no debt yet, unless nothing else."""
    fits = [e for e in CHAINS[who] if e in content.events and ev_eng.matches(content.events[e], st, content)]
    if who == "raven" and st.flags.get("raven_debt"):
        fits = [e for e in fits if e != "raven_surge"] or fits
    return fits[0] if fits else None


def ensure_contacts(st: CharacterState) -> None:
    for who, flag in MET.items():
        if not st.flags.get(flag):
            continue
        have = next((n for n in st.npcs if n.data.get("story") == who), None)
        if have is None:
            st.npcs.append(NpcState(name=NAMES[who], kind="contact", relation=55,
                                    data={"story": who, "bio": bio_for(st, who), "district": st.district}))
        else:
            have.data["bio"] = bio_for(st, who)


def mark(ch: ActiveCharacter, who: str, tz: str) -> None:
    d = dict(ch.state.flags.get(FLAG_DAY) or {})
    d[who] = kyiv_date(tz)
    ch.state.flags[FLAG_DAY] = d


def status(st: CharacterState, who: str) -> str:
    f = st.flags
    if who == "raven":
        steps = [("raven_met", "встречались"), ("raven_known", "знакомы"), ("raven_trust", "доверяет"),
                 ("raven_her_known", "знаешь о Люси"), ("raven_katana_got", "клинок"),
                 ("raven_debt", "долг"), ("raven_seed", "Seed")]
    else:
        steps = [("lit_met", "был концерт"), ("lit_known", "знакомы"), ("lit_trust", "доверяет"), ("lit_label_done", "лейбл"),
                 ("lit_debt", "долг"), ("lit_song", "песня")]
    got = [name for k, name in steps if f.get(k)]
    return ", ".join(got) if got else "ещё не встречались"
