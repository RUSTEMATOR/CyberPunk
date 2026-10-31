"""Requests for fixes and features (owner request): players write them in the game, the owner copies the list.

/idea or «Профиль» → «Баг или идея»: pick the kind, type the text in the chat. /requests (admins): the open requests as
one block to copy, «Скопировал» closes them. world_state `requests` = [{id, kind, text, tg, name, at, done}].
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot import texts
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, make_ctx, on_screen, on_text
from app.bot.ui.kit import UiKit
from app.bot.ui.screen import Button, Screen
from app.clock import utcnow
from app.db import repo
from app.db import session as db
from app.services import game

router = Router(name="requests")
KEY = "requests"
KINDS = {"bug": "баг", "feature": "фича", "any": "заявка"}
MAX_CHARS = 1000
PER_DAY = 10
KEEP_DONE = 200       # closed requests kept for the history
COPY_CHARS = 2600     # one screen of the list stays under balance.ui.max_screen_chars with the HTML around it

INTRO = ("Нашёл баг или хочешь новую фичу — напиши. Заявки собираются в один список, владелец бота переносит их "
         "в работу. Пиши как есть: что делал, что увидел, что хотел бы.")


async def _all(s) -> list[dict]:  # noqa: ANN001
    got = await repo.ws_get(s, KEY, [])
    return [dict(x) for x in got] if isinstance(got, list) else []


def ask_screen(note: str | None = None, kind: str = "any") -> Screen:
    """The window waits for the text at once (owner report 23.10: people typed on the first screen and nothing was
    saved). The kind is optional: the buttons only mark it."""
    mark = {"bug": "Отмечено: баг.", "feature": "Отмечено: фича.", "any": "Можно отметить, баг это или фича."}[kind]
    lines = [style.header("relations", "Баг или идея"), "", INTRO, "",
             f"<b>Просто напиши сообщение в чат</b> — до {MAX_CHARS} знаков. Бот уберёт его из беседы и сохранит.", mark]
    if note:
        lines += ["", note]
    rows = [[Button("Это баг" + (" ✓" if kind == "bug" else ""), "kind", "bug"),
             Button("Это фича" + (" ✓" if kind == "feature" else ""), "kind", "feature")],
            [Button(texts.MENU, "menu")]]
    return Screen("\n".join(lines), rows, name="reqask", state={"kind": kind}, awaiting_text="req_text")


@on_screen("reqask")
async def reqask_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "kind" and arg in KINDS:
        return ask_screen(kind=arg)
    return ask_screen(kind=str(ctx.state.get("kind") or "any"))


@on_text("req_text")
async def req_text(ctx: Ctx, text: str) -> Screen | None:
    kind = str(ctx.state.get("kind") or "any")
    body = " ".join(text.split())[:MAX_CHARS]
    if len(body) < 5:
        return ask_screen("Слишком коротко. Попробуй ещё раз.", kind)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        items = await _all(s)
        today = utcnow().date().isoformat()
        mine = [x for x in items if int(x.get("tg", 0)) == ctx.user_id and str(x.get("at", "")).startswith(today)]
        if len(mine) >= PER_DAY:
            return ask_screen(f"На сегодня хватит: {PER_DAY} заявок в день.", kind)
        rid = max((int(x.get("id", 0)) for x in items), default=0) + 1
        who = ctx.full_name or (f"@{ctx.username}" if ctx.username else str(ctx.user_id))
        if ch is not None:
            who += f" ({ch.state.name} {ch.state.surname})".rstrip()
        items.append({"id": rid, "kind": kind, "text": body, "tg": ctx.user_id, "name": who,
                      "at": utcnow().isoformat(timespec="minutes"), "done": False})
        done = [x for x in items if x.get("done")]
        if len(done) > KEEP_DONE:
            drop = {x["id"] for x in done[: len(done) - KEEP_DONE]}
            items = [x for x in items if x["id"] not in drop]
        await repo.ws_set(s, KEY, items)
    return ask_screen(f"✅ Заявка #{rid} сохранена. Можно написать ещё одну.")


# -- the owner's list ---------------------------------------------------------------------------------------------------


def _line(x: dict, tz: str) -> str:
    try:
        at = datetime.fromisoformat(str(x["at"])).astimezone(ZoneInfo(tz)).strftime("%d.%m %H:%M")
    except (KeyError, ValueError):
        at = ""
    return f"#{x['id']} [{KINDS.get(x.get('kind', ''), '?')}] {x.get('name', '?')}, {at}: {x.get('text', '')}"


async def list_screen(ctx: Ctx, note: str | None = None) -> Screen:
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        items = [x for x in await _all(s) if not x.get("done")]
    head = [style.header("relations", f"Заявки: {len(items)} открытых")]
    if note:
        head += [note]
    rows: list[list[Button]] = []
    if not items:
        head += ["", "Открытых заявок нет."]
        rows.append([Button(texts.MENU, "menu")])
        return Screen("\n".join(head), rows, name="reqlist")
    block: list[str] = []
    shown: list[int] = []
    size = 0
    for x in items:
        ln = _line(x, tz)
        if size + len(ln) > COPY_CHARS and block:
            break
        block.append(ln)
        shown.append(int(x["id"]))
        size += len(ln) + 1
    more = len(items) - len(shown)
    text = "\n".join(head + ["", "Нажми на блок, чтобы скопировать:", "<pre>" + texts.esc("\n".join(block)) + "</pre>"]
                     + ([f"Ещё {more} — появятся после отметки этих."] if more else []))
    rows.append([Button(f"Скопировал ({len(shown)})", "done", f"{shown[0]}-{shown[-1]}")])
    rows.append([Button(texts.MENU, "menu")])
    return Screen(text, rows, name="reqlist")


@on_screen("reqlist")
async def reqlist_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if not ctx.ui.is_admin(ctx.user_id):
        return await menu_screen(ctx)
    if action == "done" and arg and "-" in arg:
        lo, hi = (int(x) for x in arg.split("-", 1))
        async with db.session_scope() as s:
            items = await _all(s)
            n = 0
            for x in items:
                if lo <= int(x.get("id", 0)) <= hi and not x.get("done"):
                    x["done"] = True
                    n += 1
            await repo.ws_set(s, KEY, items)
        return await list_screen(ctx, f"Отмечено: {n}.")
    return await list_screen(ctx)


@router.message(Command("idea", "bug"))
async def cmd_idea(message: Message, ui: UiKit) -> None:
    user = message.from_user
    if user is None:
        return
    await ui.windows.open(message.chat.id, user.id, ask_screen())


@router.message(Command("requests"))
async def cmd_requests(message: Message, ui: UiKit) -> None:
    user = message.from_user
    if user is None or not ui.is_admin(user.id):
        return
    ctx = await make_ctx(ui, user, message.chat.id)
    await ui.windows.open(message.chat.id, user.id, await list_screen(ctx))
