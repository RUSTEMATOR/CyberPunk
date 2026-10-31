"""Builders of the M6 screens' text: the implant card in plain Russian (effects come from `effects_text`).

The card never shows a number the character cannot know: the humanity loss is a range over all tolerance types,
plus the estimate for the type the character believes in (after a scan).
"""

from __future__ import annotations

from app.bot import texts_chrome as T
from app.bot import effects_text as FX
from app.bot import views
from app.bot.ui import style
from app.content.loader import Content
from app.content.schemas import Cyberware
from app.engine import derived
from app.engine import ripper as eng
from app.engine.dice import half_up
from app.engine.state import CharacterState

SLOT_NAMES = {
    "brain": "мозг", "spine": "позвоночник", "eyes": "глаза", "skin": "кожа", "face": "лицо", "circulatory": "кровь",
    "legs": "ноги", "forearms": "предплечья", "hands": "кисти", "body": "всё тело",
}


def signed(n: int) -> str:
    return views.signed(n)


def humanity_range(content: Content, state: CharacterState, cw: Cyberware, year: int) -> tuple[int, int]:
    """What the implant can take across all tolerance types, with the era and the character's stabilizers."""
    if cw.humanity_cost <= 0:
        return 0, 0
    pct = derived.mod(state, content, "humanity_loss_pct")
    factor = derived.era_humanity_mult(content, year) * max(0.0, 1 + pct / 100)
    vals = [max(1, half_up(cw.humanity_cost * e.mult * factor)) if factor > 0 else 0 for e in content.balance.cyber.tolerance]
    return min(vals), max(vals)


def humanity_lines(content: Content, state: CharacterState, cw: Cyberware, year: int) -> list[str]:
    if cw.humanity_cost <= 0:
        return [T.RP_CARD_PCH_NONE]
    lo, hi = humanity_range(content, state, cw, year)
    if lo == hi:
        return [T.RP_CARD_PCH_FLAT.format(pch=lo)]
    lines = [T.RP_CARD_PCH.format(pch=cw.humanity_cost, lo=lo, hi=hi)]
    if state.tolerance_known:
        entry = derived.tolerance_entry(content, eng.shown_tolerance(state))
        pct = derived.mod(state, content, "humanity_loss_pct")
        factor = entry.mult * derived.era_humanity_mult(content, year) * max(0.0, 1 + pct / 100)
        mine = max(1, half_up(cw.humanity_cost * factor)) if factor > 0 else 0
        lines.append(T.RP_CARD_PCH_MINE.format(tol=views.esc(entry.name), n=mine))
    else:
        lines.append(T.RP_CARD_PCH_UNKNOWN)
    if cw.borg_risk and state.tolerance_known:  # owner request 27.10: the underground conversion
        pct = round(eng.black_death_chance(state, content, shown=True) * 100)
        lines.append(f"☠️ Тебе с типом «{views.esc(derived.tolerance_entry(content, eng.shown_tolerance(state)).name)}»: "
                     f"шанс умереть на столе около {pct}%. Деньги в любом случае не вернут.")
    return lines


def card_text(content: Content, state: CharacterState, cw: Cyberware, offer: eng.Offer, ripper_name: str, year: int) -> str:
    """The implant card: description, every effect, requirements, the replacement, price and humanity."""
    lines = [T.RP_CARD_TITLE.format(name=views.esc(cw.name))]
    lines.append(T.RP_CARD_META.format(
        tier=T.RP_CARD_TIER[cw.tier], cat=T.RP_CAT_NAMES[cw.category].lower(), slot=SLOT_NAMES.get(cw.slot, cw.slot)))
    lines.append(style.quote(f"<i>{views.esc(cw.description)}</i>"))
    passive = FX.cyberware_phrases(cw, content)
    if passive or cw.category == "booster":
        lines.append("")
        lines.append(T.RP_CARD_GIVES)
        if cw.category == "booster":
            lines.append(T.RP_CARD_BOOSTER)
        lines.extend(T.RP_CARD_BULLET.format(text=views.esc(FX.cap(p))) for p in passive)
    if cw.active is not None:
        lines.append("")
        lines.append(T.RP_CARD_ACTIVE.format(button=views.esc(cw.active.button)))
        lines.extend(views.esc(x) for x in FX.active_full(cw.active, content))
    lines.extend(build_lines(content, state, cw))  # owner request 26.10: abilities and the build line
    lines.append("")
    if cw.requires:
        names = ", ".join(views.esc(content.cyberware[r].name) for r in cw.requires if r in content.cyberware)
        lines.append(T.RP_CARD_REQUIRES.format(names=names))
        if offer.reason == eng.R_NEEDS:
            lines.append(T.RP_CARD_NEEDS_NOTE)
    if offer.replaces:
        old = content.cyberware.get(offer.replaces)
        lines.append(T.RP_CARD_REPLACE.format(
            old=views.esc(old.name if old else offer.replaces), removal=views.money(offer.removal)))
        lines.append(T.RP_CARD_PRICE_REPLACE.format(price=views.money(offer.price), total=views.money(offer.total)))
    else:
        lines.append(T.RP_CARD_PRICE.format(price=views.money(offer.price)))
    lines.extend(humanity_lines(content, state, cw, year))
    return "\n".join(lines)


def ability_icons(content: Content, cw: Cyberware) -> str:
    ab = content.balance.chrome_abilities
    return "".join(ab[k].icon for k in cw.abilities if k in ab)


def build_lines(content: Content, state: CharacterState, cw: Cyberware) -> list[str]:
    """Owner request 26.10: what the implant lets you do outside a fight and the build line it belongs to."""
    from app.engine import abilities as ab_eng

    out: list[str] = []
    ab = content.balance.chrome_abilities
    if cw.abilities:
        rank = ab_eng.line_rank(content, cw.key)  # owner request 26.10: one implant, one ability of its own
        out += ["", "⚙️ <b>Своя возможность</b> (больше ни у одного импланта)"]
        out += [f"• {ab[k].icon} {views.esc(ab[k].name)}" + (f" · ранг {rank}" if rank > 1 else "")
                + f": {views.esc(ab[k].text)}" for k in cw.abilities if k in ab]
    cs = content.balance.chrome_sets.get(cw.build or "")
    if cs is not None:
        have = ab_eng.set_counts(state, content).get(cw.build or "", 0)
        mine = any(it.key == cw.key or (content.cyberware.get(it.key) and content.cyberware[it.key].exclusive_group
                                         and content.cyberware[it.key].exclusive_group == cw.exclusive_group)
                   for it in state.cyberware)
        after = have if mine else have + 1
        out += ["", f"{cs.icon} <b>Сборка «{views.esc(cs.name)}»</b>: у тебя {have} дет., с этим будет {after}."]
        for need, b in sorted(cs.bonuses.items()):
            mark = "✅" if have >= int(need) else ("🆕" if after >= int(need) else "▫️")
            out.append(f"{mark} {need} дет.: {views.esc(b.text)}")
    return out


def price_word(mult: float) -> str:
    for limit, word in T.RP_PRICE_WORDS:
        if mult <= limit:
            return word
    return T.RP_PRICE_WORDS[-1][1]


def tolerance_label(content: Content, state: CharacterState) -> str | None:
    """The tolerance type the character believes in (a lying scan's word until a true scan), or None before a scan."""
    if not state.tolerance_known:
        return None
    return views.esc(derived.tolerance_entry(content, eng.shown_tolerance(state)).name)
