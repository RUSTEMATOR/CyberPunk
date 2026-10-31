"""Levels, attributes and perk trees in the window (owner request 24.10): profile -> «Прокачка»."""

from __future__ import annotations

from app.bot import texts
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen, pack
from app.content.loader import Content
from app.db import session as db
from app.engine import bwtree
from app.engine import perks as eng
from app.services import game
from app.services.game import ActiveCharacter

STAT_NAMES = {"BODY": "ТЕЛО", "REF": "РЕФЛ", "TECH": "ТЕХ", "INT": "ИНТ", "COOL": "ХЛАД", "EMP": "ЭМП", "LUCK": "УДАЧА"}
BLOCKED = {"none": "Такого нет.", "max": "Уже максимум.", "points": "Нет свободных очков.",
           "cap": "Выше уже не поднять: это предел легенды.",
           "cost": "Не хватает очков: выше 10 каждое очко атрибута стоит дороже.",
           "notyet": "Обмен откроется, когда все атрибуты дойдут до предела легенды."}


def _why(why: str) -> str:
    if why.startswith("need:"):
        _, tree, n = why.split(":")
        return f"Нужно {STAT_NAMES.get(tree, tree)} {n}."
    return BLOCKED.get(why, why)


SKILLS_TAB = "SKILLS"
BW_TAB = "BW"  # owner request 25.10: the Blackwall branch of the character


def _bw_why(why: str) -> str:
    if why.startswith("tier:"):
        return f"Нужна порча уровня {why.split(':')[1]} и выше (вкладка «Заслон» в «Сети»)."
    if why.startswith("step:"):
        return f"Нужно пройти {why.split(':')[1]} шага цепочки Аватара."
    return {"avatar": "Только для Аватара.", "side": "Ветка открыта тем, кто встал на сторону Заслона.",
            "points": "Нет очков Заслона."}.get(why) or _why(why)


def bw_lines(content: Content, ch: ActiveCharacter) -> tuple[list[str], list[Button]]:
    st = ch.state
    c = bwtree.cfg(content)
    have = bwtree.ranks(st)
    pts = bwtree.points(st, content)
    lines = ["", f"<b>{texts.esc(bwtree.tree().get('title', 'ЗАСЛОН'))}</b>",
             f"Очки Заслона: <b>{pts}</b> (всего {bwtree.earned(st, content)}).",
             "<i>Заслон сплавляется с тобой: таланты ветки усиливают твои собственные атрибуты, а «Эхо себя» — самый "
             f"сильный из них ({STAT_NAMES[bwtree.top_stat(st)]}). Очки Заслона не тратят обычные очки талантов: "
             f"{c.base} при вступлении, по {c.per_step} за шаг цепочки Аватара, ранг культа и уровень порчи, "
             f"{c.avatar} за Аватара и по одному за каждые {c.favor_per} благосклонности культа.</i>"]
    buys: list[Button] = []
    for key, p in bwtree.perks().items():
        rk = have.get(key, 0)
        ranks = int(p.get("ranks", 1))
        req = []
        for stat, need in (p.get("stat") or {}).items():
            req.append(f"{STAT_NAMES.get(stat, stat)} {int(need) + rk}")
        if p.get("tier"):
            names = content.balance.blackwall.tier_names
            t = int(p["tier"])
            req.append(f"порча «{names[t]}»" if t < len(names) else f"порча {t}+")
        if p.get("step"):
            req.append(f"шаг Аватара {p['step']}+")
        if p.get("avatar"):
            req.append("Аватар")
        mark = "✅" if rk >= ranks else ("▶" if rk else "▫️")
        tail = "" if rk >= ranks or not req else " · нужно " + ", ".join(req)
        lines.append(f"{mark} <b>{texts.esc(p['name'])}</b> {rk}/{ranks}{tail}\n    <i>{texts.esc(p['text'])}</i>")
        if bwtree.block(st, content, key) is None:
            lab = f"{p['name']} {rk + 1}/{ranks}"
            buys.append(Button(lab if len(lab) <= 24 else p["name"][:24], "bwbuy", key))
    return lines, buys


def perks_screen(content: Content, ch: ActiveCharacter, tree: str = "BODY", note: str | None = None) -> Screen:
    st = ch.state
    c = eng.cfg(content)
    r = eng.rec(st, content)
    lv = int(r["level"])
    nxt = eng.next_xp(content, lv)
    xp = int(r["xp"])
    lines = [style.header("xp", "Прокачка"),
             f"⭐ Уровень <b>{lv}</b> · опыт {xp}" + (f"/{nxt}" if nxt else " (макс.)"),
             f"Очки атрибутов: <b>{r['attr']}</b> · талантов (перков): <b>{r['perk']}</b>",
             "<i>Опыт идёт со всего: каждое потраченное ОД, заказы, бои, события, прожитые недели, работа, покупки, "
             f"встречи с игроками и опыт навыков. Уровень: очко атрибута и очко таланта. Атрибут до {c.stat_cap} стоит "
             f"1 очко, выше — предел легенды до {c.stat_legend_cap}: {c.stat_cap + 1}-й стоит {c.over_cost_start} очка, "
             f"каждый следующий на одно дороже. Когда все атрибуты на {c.stat_legend_cap}, очко атрибута можно "
             "обменять на очко таланта. "
             f"Навыки растут только тренировками и делом. Каждый новый уровень навыка — ещё +{c.skill_level_xp} опыта, "
             f"каждые {c.skill_levels_per_talent} уровня навыков — очко таланта. ТЕЛО и ТЕХ помогают телу держать хром.</i>",
             "",
             " · ".join(f"{STAT_NAMES[k]} {st.stats.get(k, 0)}" for k in ("BODY", "REF", "TECH", "INT", "COOL", "EMP", "LUCK"))]
    tabs = [Button(("• " if k == tree else "") + STAT_NAMES[k], "tree", k) for k in eng.TREES]
    tabs.append(Button(("• " if tree == SKILLS_TAB else "") + "Навыки", "tree", SKILLS_TAB))
    if bwtree.on_side(st):  # owner request 25.10: the Blackwall branch for those on its side
        tabs.append(Button(("• " if tree == BW_TAB else "") + "ЗАСЛОН", "tree", BW_TAB))
    rows: list[list[Button]] = pack(tabs, content.balance.ui.button_chars, max_per_row=4)
    if tree == BW_TAB and bwtree.on_side(st):
        bl, buys = bw_lines(content, ch)
        lines += bl
        rows += pack(buys, content.balance.ui.button_chars, max_per_row=2)
    elif tree == SKILLS_TAB:
        cap = content.balance.character.skill_cap
        lines += ["", f"<b>Навыки</b> (до {cap}) растут только тренировкой, учёбой и делом. Очков навыков нет."]
        for k, label in texts.SKILL_LABELS.items():
            sk = st.skills.get(k)
            lines.append(f"• {label}: {sk.level if sk else 0}/{cap}")
    else:
        t = eng.trees().get(tree) or {}
        lines += ["", f"<b>{texts.esc(t.get('title', tree))}</b>"]
        buys = []
        for key, p in (t.get("perks") or {}).items():
            have = eng.rank(st, key)
            ranks = int(p.get("ranks", 1))
            need = int(p.get("need", 0)) + have
            mark = "✅" if have >= ranks else ("▶" if have else "▫️")
            req = "" if have >= ranks else f" · нужно {STAT_NAMES[tree]} {need}"
            lines.append(f"{mark} <b>{texts.esc(p['name'])}</b> {have}/{ranks}{req}\n    <i>{texts.esc(p['text'])}</i>")
            if have < ranks and int(r["perk"]) > 0 and int(st.stats.get(tree, 0)) >= need:
                lab = f"{p['name']} {have + 1}/{ranks}"
                buys.append(Button(lab if len(lab) <= 24 else p["name"][:24], "buy", f"{tree}:{key}"))
        rows += pack(buys, content.balance.ui.button_chars, max_per_row=2)
    if note:
        lines += ["", note]
    if int(r["attr"]) > 0:
        ups = []
        for k in STAT_NAMES:
            cur = int(st.stats.get(k, 0))
            if cur >= c.stat_legend_cap:
                continue
            cost = eng.stat_cost(content, cur)
            ups.append(Button(f"{STAT_NAMES[k]} +1" + (f" · {cost}" if cost > 1 else ""), "attr", f"{tree}:{k}"))
        rows += pack(ups, content.balance.ui.button_chars, max_per_row=4 if all(b.text.endswith("+1") for b in ups) else 3)
        if eng.all_legend(st, content):
            rows.append([Button("Очко атрибута → талант", "a2p", tree)])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="perks")


@on_screen("perks")
async def perks_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "profile")
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        tree = "BODY"
        note = None
        if action == "tree" and (arg in eng.TREES or arg in (SKILLS_TAB, BW_TAB)):
            tree = arg
        elif action == "bwbuy" and arg:  # owner request 25.10: the Blackwall branch
            tree = BW_TAB
            why = bwtree.buy(ch.state, c, arg)
            if why:
                note = _bw_why(why)
            else:
                await game.save_active(s, ch, ctx.ui.settings.timezone)
                note = f"Заслон отвечает: <b>{texts.esc(bwtree.perks()[arg]['name'])}</b>."
        elif action == "skill" and arg:
            tree = SKILLS_TAB
            why = eng.raise_skill(ch.state, c, arg)
            if why:
                note = _why(why)
            else:
                await game.save_active(s, ch, ctx.ui.settings.timezone)
                note = f"{texts.SKILL_LABELS.get(arg, arg)} +1."
        elif action in ("buy", "attr") and arg and ":" in arg:
            tree, _, key = arg.partition(":")
            why = eng.buy(ch.state, c, key) if action == "buy" else eng.raise_stat(ch.state, c, key)
            if why:
                note = _why(why)
            else:
                await game.save_active(s, ch, ctx.ui.settings.timezone)
                if action == "buy":
                    found = eng.perk(key)
                    note = f"Перк взят: <b>{texts.esc(found[1]['name'])}</b>." if found else None
                else:
                    note = f"{STAT_NAMES.get(key, key)} +1."
        elif action == "a2p":
            tree = arg or "BODY"
            why = eng.attr_to_perk(ch.state, c)
            if why:
                note = _why(why)
            else:
                await game.save_active(s, ch, ctx.ui.settings.timezone)
                note = "Очко атрибута стало очком таланта."
        elif action == "open":
            r = eng.rec(ch.state, c)
            if not isinstance(ch.state.flags.get(eng.FLAG), dict):  # the start level of an existing character
                eng.save(ch.state, r)
                await game.save_active(s, ch, ctx.ui.settings.timezone)
    return perks_screen(c, ch, tree if (tree in eng.TREES or tree in (SKILLS_TAB, BW_TAB)) else "BODY", note)
