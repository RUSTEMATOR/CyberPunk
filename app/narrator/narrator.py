"""The narrator (PLAN 2.6 and M8): the one door between the game and Claude.

The narrator receives a structured result and returns text. It never changes numbers (architecture rule 4): the
prompt forbids it and `guard` throws away a text with a figure that is not in the payload. Whatever goes wrong (no key,
`LLM_ENABLED=false`, the budget spent, a timeout, a bad answer, a broken pause), the answer is a text from
`fallback_texts.yaml` or the authored text the caller passed, so the game never stops because of Claude.

Public calls: `narrate`, `narrate_or` (text kinds), `classify_action`, `npc_reply`, `generate_text`, `generate_json`.
Everything else (the client, the money, the prompts, the checks) lives next to this file.
"""

from __future__ import annotations

import asyncio
import json
import re
import logging
import random
import time
from functools import lru_cache
from typing import Any, Mapping

from app.config import Settings
from app.content.loader import Content, load_content
from app.content.schemas import OUTCOME_TIERS, SKILLS
from app.narrator import digest, guard, prompts
from app.narrator.budget import budget
from app.narrator.client import LlmClient, LlmError, Reply

log = logging.getLogger(__name__)

# Placeholders that are numbers: an absent value reads as 0, not as a gap in the sentence.
_NUMERIC_FIELDS = ("eddies", "hp", "heat", "street_cred", "count", "tier", "pay", "age")
_GAP = "…"

_content: Content | None = None
_rng = random.Random()  # the narrator is outside the engine, its dice may be unseeded


@lru_cache(maxsize=1)
def _load() -> Content:
    return load_content()


def _get_content() -> Content:
    return _content if _content is not None else _load()


class SafeDict(dict):  # type: ignore[type-arg]
    """format_map dictionary: an unknown placeholder never raises, it becomes a numeric 0 or a gap."""

    def __missing__(self, key: str) -> str:
        return "0" if key in _NUMERIC_FIELDS else _GAP


def flatten_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Make one flat mapping out of the contract-shaped payload of DESIGN.md section 14.

    Top-level scalars stay. A nested mapping `gig: {title: ...}` adds `title` (when the name is
    free) and `gig_title`, so `character.name` becomes `name`, `gig.title` becomes `gig_title`,
    `effects.eddies` becomes `eddies`.
    """
    flat: dict[str, Any] = {}
    for key, val in payload.items():
        if not isinstance(val, Mapping):
            flat[key] = val
    for key, val in payload.items():
        if isinstance(val, Mapping):
            for sub, subval in val.items():
                if isinstance(subval, (Mapping, list, tuple, set)):
                    continue
                flat.setdefault(sub, subval)
                flat[f"{key}_{sub}"] = subval
    return flat


def _limit(kind: str, content: Content) -> int:
    nb = content.balance.narrator
    if kind == "obituary":
        return nb.obituary_max_chars
    if kind in ("news", "news_quiet"):
        return nb.news_max_chars
    return nb.message_max_chars


def _templates(kind: str, payload: Mapping[str, Any], content: Content) -> list[str]:
    ft = content.fallback_texts
    if kind == "gig_result":
        outcome = payload.get("outcome")
        if outcome not in OUTCOME_TIERS:
            outcome = "clean"
        return list(ft.gig_result[outcome])
    texts = getattr(ft, kind, None)
    if isinstance(texts, list):
        return list(texts)
    return []


def fallback_text(kind: str, payload: Mapping[str, Any]) -> str:
    """A random ready-made text of this kind with the payload fields put in. Never raises."""
    try:
        content = _get_content()
        templates = _templates(kind, payload, content)
        if not templates:
            log.warning("narrator: no fallback texts for kind %r", kind)
            return ""
        template = _rng.choice(templates)
        values = SafeDict(flatten_payload(payload))
        try:
            text = template.format_map(values)
        except (ValueError, KeyError, IndexError, AttributeError):
            text = template.replace("{", "").replace("}", "")
        limit = _limit(kind, content)
        return text if len(text) <= limit else text[: limit - 1].rstrip() + _GAP
    except Exception:  # the narrator must never break the game
        log.exception("narrator: fallback failed for kind %r", kind)
        return ""




# ======================================================================================================================
# M8: Claude
# ======================================================================================================================

TEXT_KINDS: frozenset[str] = frozenset({"gig_brief", "gig_result", "event_text", "event_outcome", "obituary", "news"})
# Written while a unit of work holds the database write lock: a short timeout and no retry (balance.narrator).
TXN_KINDS: frozenset[str] = frozenset({"gig_result", "obituary"})
WINDOW_KINDS: frozenset[str] = frozenset({"gig_brief", "event_text", "classify_action", "unique_event", "npc_chat", "saga_step",
                                          "free_classify", "free_outcome", "event_outcome",
                                          "activity_text"})  # a player is waiting: short timeout, no retry
# Owner report 24.10 (3 players at once felt slow; Sonnet took 12-17 s for a 150-token gig text): the short texts a
# player waits for go to the fast model.
FAST_KINDS: frozenset[str] = frozenset({"classify_action", "event_text", "free_classify", "gig_brief", "gig_result",
                                        "event_outcome", "free_outcome", "activity_text"})

LIVE = False  # True while the narrator can call Claude right now; `is_live()` is the exact answer

_settings: Settings | None = None
_client: LlmClient | None = None
_year: int | None = None
_fail_streak = 0
_pause_until = 0.0
_sem: asyncio.Semaphore | None = None


def configure(
    content: Content | None = None, rng: random.Random | None = None, settings: Settings | None = None,
    client: LlmClient | None = None,
) -> None:
    """Use an already loaded Content (and, in checks, a seeded rng) instead of loading it lazily.

    With `settings` the narrator gets a Claude client when `.env` allows it (key present, `LLM_ENABLED`, a budget above
    zero). `client` replaces the real client (scratch checks inject a fake one)."""
    global _content, _rng, _settings, _client, _sem
    if content is not None:
        _content = content
    if rng is not None:
        _rng = rng
    if settings is not None:
        _settings = settings
        nb = _get_content().balance.narrator
        budget.configure(settings, nb.budget_warn_pct, nb.budget_fallback_pct)
        budget.on_mode_change = refresh_live
        _sem = None
        _client = LlmClient(settings.anthropic_api_key, timeout=nb.timeout_seconds, retries=nb.retries) if settings.llm_configured else None
    if client is not None:
        _client = client
    refresh_live()


async def start() -> None:
    """At bot start (after the database is up): this month's spending and the stored history digests."""
    await budget.load()
    await digest.load()
    refresh_live()
    if _client is None:
        log.info("narrator: Claude is off (no key, LLM_ENABLED=false or no budget); texts come from the content files")
    else:
        if budget.prices_missing():
            log.warning("narrator: PRICE_* in .env are empty, the money counter stays at zero and the budget cannot trigger")
        log.info("narrator: Claude is on (%s, %s), spent %.2f of %.2f USD this month", _settings.model_narrative if _settings else "?",
                 _settings.model_fast if _settings else "?", budget.spent, budget.limit)


async def stop() -> None:
    await digest.drain()
    await budget.drain()


def note_year(year: int) -> None:
    """A default year for calls that do not carry one (the group news). Every call that belongs to a character passes
    that character's year (`year=`): there is no shared world clock (DESIGN.md 9a)."""
    global _year
    _year = year


def is_live() -> bool:
    """The narrator would call Claude now: a client, money left, no pause after a run of errors."""
    return _client is not None and _client.enabled and budget.allowed() and time.monotonic() >= _pause_until


def pause_left() -> int:
    return max(0, int(_pause_until - time.monotonic()))


def has_client() -> bool:
    return _client is not None


def refresh_live() -> None:
    global LIVE
    LIVE = is_live()


def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_get_content().balance.narrator.concurrency)
    return _sem


def _trip(err: LlmError) -> None:
    """Count a failed call; a run of failures (or a bad key) pauses the narrator so that players are not made to wait."""
    global _fail_streak, _pause_until
    nb = _get_content().balance.narrator
    _fail_streak += 1
    if err.reason == "auth":
        _pause_until = time.monotonic() + nb.breaker_pause_seconds * 5
        log.error("narrator: the API refused the key (%s); pausing Claude for %d s", err.detail, nb.breaker_pause_seconds * 5)
    elif _fail_streak >= nb.breaker_failures:
        _pause_until = time.monotonic() + nb.breaker_pause_seconds
        _fail_streak = 0
        log.error("narrator: %d failures in a row (last: %s); fallback texts for %d s", nb.breaker_failures, err, nb.breaker_pause_seconds)
    refresh_live()


def _model(kind: str) -> str:
    assert _settings is not None
    return _settings.model_fast if kind in FAST_KINDS else _settings.model_narrative


def _year_now(year: int | None = None) -> int:
    """The year a call is about: the one the call site passed, else the noted default, else the start year."""
    if year is not None:
        return year
    if _year is not None:
        return _year
    return _get_content().balance.world.start_year


async def _ask(
    kind: str, messages: list[dict[str, Any]], *, year: int | None = None, max_tokens: int | None = None,
    model_kind: str | None = None,
) -> Reply | None:
    """One call to Claude with the cached lore block. None on any failure (logged, counted, never raised)."""
    if not is_live() or _client is None or _settings is None:
        return None
    content = _get_content()
    nb = content.balance.narrator
    mk = model_kind or kind
    model = _model(mk)
    at = _year_now(year)
    system = prompts.system_blocks(content, at, digest.text_for(at))
    timeout = retries = None
    if kind in TXN_KINDS:
        timeout, retries = nb.txn_timeout_seconds, nb.txn_retries
    elif kind in WINDOW_KINDS:
        timeout, retries = nb.window_timeout_seconds, nb.window_retries
    elif kind == "legend_scene":  # owner request 27.10: a long epic text, the player waits for it once a life
        timeout, retries = 90, 0
    elif kind == "scenarist":  # owner request 27.10: the background scenarist (services/dialogs.py)
        timeout, retries = 60, 0
    started = time.monotonic()
    try:
        async with _semaphore():
            reply = await _client.complete(
                model=model, system=system, messages=messages, max_tokens=max_tokens or nb.max_tokens[kind],
                timeout=timeout, retries=retries,
            )
    except LlmError as err:
        _trip(err)
        return None
    except Exception:  # the narrator must never break the game
        log.exception("narrator: unexpected failure in %s", kind)
        _trip(LlmError("api", "unexpected"))
        return None
    global _fail_streak
    _fail_streak = 0
    cost = budget.record(kind, model, reply.usage)
    log.info(
        "narrator: %s ok in %.1fs (%s, in %d, cache r%d w%d, out %d, %.4f USD)", kind, time.monotonic() - started, model,
        reply.usage.input_tokens, reply.usage.cache_read, reply.usage.cache_write, reply.usage.output_tokens, cost,
    )
    refresh_live()
    return reply


def _world_numbers(year: int | None = None) -> set[str]:
    """Figures of the world itself (years in the era brief and in the history) that a text may repeat."""
    content = _get_content()
    at = _year_now(year)
    era = content.era_for_year(at)
    return guard.payload_numbers(era.brief, digest.text_for(at), at)


def _limit_of(kind: str, content: Content) -> int:
    nb = content.balance.narrator
    if kind == "obituary":
        return nb.obituary_max_chars
    if kind == "news":
        return min(nb.news_paragraph_max_chars, nb.news_max_chars)
    if kind == "history_digest":
        return nb.history_digest_max_chars
    if kind == "chronicle":
        return nb.obituary_max_chars * 2
    if kind == "legend_scene":  # owner request 27.10
        return 3800
    return nb.message_max_chars


def check_text(
    raw: str, payload: Mapping[str, Any], limit: int, *, truncated: bool = False, year: int | None = None,
) -> tuple[str | None, str]:
    """Clean Claude's text and test it: fits the limit, no figure outside the payload. Returns (text, why not)."""
    nb = _get_content().balance.narrator
    text = guard.clean(raw)
    if not text:
        return None, "empty"
    if truncated:  # the answer hit max_tokens: keep whole sentences only
        cut = guard.fit(text, len(text) - 1, nb.min_keep_ratio)
        if cut is None:
            return None, "truncated"
        text = cut
    fitted = guard.fit(text, limit, nb.min_keep_ratio)
    if fitted is None:
        return None, f"too long ({len(text)} > {limit})"
    foreign = guard.foreign_numbers(fitted, guard.payload_numbers(payload) | _world_numbers(year))
    if foreign:
        return None, f"numbers not in the payload: {foreign}"
    return fitted, ""


async def generate_text(
    kind: str, payload: Mapping[str, Any], *, year: int | None = None, limit: int | None = None, **fmt: Any,
) -> str | None:
    """Claude's text for a text kind, checked; None when the narrator is off, failed or wrote something unusable."""
    if not is_live():
        return None
    content = _get_content()
    nb = content.balance.narrator
    cap = limit or _limit_of(kind, content)
    msg = prompts.user_message(kind, payload, target=int(cap * nb.target_ratio), **fmt)
    reply = await _ask(kind, [{"role": "user", "content": msg}], year=year)
    if reply is None:
        return None
    text, why = check_text(reply.text, payload, cap, truncated=reply.truncated, year=year)
    if text is None:
        log.warning("narrator: %s dropped (%s)", kind, why)
    return text


async def generate_json(
    kind: str, payload: Mapping[str, Any], *, year: int | None = None, **fmt: Any,
) -> dict[str, Any] | None:
    """Claude's answer to a JSON kind (classify_action, unique_event, future_beat) as a dict, or None when it is not
    a JSON object. The caller validates the content."""
    if not is_live():
        return None
    msg = prompts.user_message(kind, payload, **fmt)
    reply = await _ask(kind, [{"role": "user", "content": msg}], year=year)
    if reply is None:
        return None
    data = guard.extract_json(reply.text)
    if data is None:
        log.warning("narrator: %s dropped (not a JSON object): %.120r", kind, reply.text)
    return data


async def narrate(kind: str, payload: dict[str, Any], *, year: int | None = None) -> str:
    """Text for a structured result. `kind` is one of schemas.NarratorKind (or news_quiet).

    Claude's text when the narrator is live and the text passes the checks, else a ready-made text from
    `fallback_texts.yaml`. Never raises."""
    if kind in TEXT_KINDS:
        try:
            text = await generate_text(kind, payload, year=year)
        except Exception:
            log.exception("narrator: %s failed, using the fallback", kind)
            text = None
        if text:
            return text
    return fallback_text(kind, payload)


async def narrate_or(kind: str, payload: dict[str, Any], default: str, *, year: int | None = None) -> str:
    """Claude's text when the narrator is live and produced a usable one, else `default`.

    Events and outcomes already have authored texts in the YAML; the generic fallback texts would be worse than those,
    so the authored text is shown whenever Claude is off or fails. Kinds that are not text kinds of M8
    (`activity_text`) are always the authored text: a training session does not need a paid sentence."""
    if kind not in TEXT_KINDS or not is_live():
        return default
    try:
        text = await generate_text(kind, payload, year=year)
    except Exception:
        log.exception("narrator: %s failed, using the authored text", kind)
        return default
    return text or default


async def classify_action(text: str, context: dict[str, Any], *, year: int | None = None) -> dict[str, Any] | None:
    """Turn a free-text action into {approach, skill, summary} (PLAN 8.4) with the fast model.

    `context` carries `available_approaches` (the approaches of the gig) and may carry `gig`, `character`. None means
    'not understood' (off, error, invalid JSON, an approach the gig does not have): the caller then asks the player to
    pick an approach with the buttons."""
    if not is_live():
        return None
    nb = _get_content().balance.narrator
    approaches = [a for a in context.get("available_approaches", ()) if a in ("loud", "quiet", "talk", "hack")]
    if not approaches:
        return None
    payload = {
        "player_text": str(text)[: nb.npc_chat_player_max_chars], "available_approaches": approaches,
        "skills": list(SKILLS), "gig": context.get("gig"), "character": context.get("character"),
    }
    data = await generate_json("classify_action", payload, year=year, summary_max=nb.classify_summary_max_chars)
    if not data:
        return None
    approach = data.get("approach")
    if approach not in approaches:
        return None
    skill = data.get("skill")
    summary = guard.clean(str(data.get("summary") or ""))
    cut = guard.fit(summary, nb.classify_summary_max_chars, nb.min_keep_ratio) if summary else ""
    return {"approach": approach, "skill": skill if skill in SKILLS else None, "summary": cut or ""}


async def classify_free(text: str, verbs: dict[str, str], npc: dict[str, Any], *,
                        year: int | None = None) -> dict[str, Any] | None:
    """Owner request (freedom of action): what the player does to a city NPC in their own words.
    Returns {"verb": a key of `verbs` or None, "summary": str}; None when the narrator is off or failed."""
    if not is_live():
        return None
    nb = _get_content().balance.narrator
    payload = {"player_text": str(text)[: nb.npc_chat_player_max_chars], "verbs": verbs, "npc": npc}
    data = await generate_json("free_classify", payload, year=year, summary_max=nb.classify_summary_max_chars)
    if not data:
        return None
    verb = data.get("verb")
    summary = guard.clean(str(data.get("summary") or ""))
    cut = guard.fit(summary, nb.classify_summary_max_chars, nb.min_keep_ratio) if summary else ""
    return {"verb": verb if verb in verbs else None, "summary": cut or "", "refuse": bool(data.get("refuse"))}


async def find_story(payload: dict[str, Any], *, year: int | None = None) -> str | None:
    """Owner request 24.10: how a character came by a thing an admin granted. None when off or failed."""
    try:
        return await generate_text("find_story", payload, year=year)
    except Exception:
        log.exception("narrator: find_story failed")
        return None


async def free_outcome(payload: dict[str, Any], *, year: int | None = None) -> str | None:
    """The scene of a free action whose numbers the engine already decided (or a harmless one with no numbers)."""
    try:
        return await generate_text("free_outcome", payload, year=year)
    except Exception:
        log.exception("narrator: free_outcome failed")
        return None


async def npc_reply(
    card: dict[str, Any], history: list[tuple[str, str]], line: str, *, year: int | None = None,
) -> str | None:
    """One reply of an NPC (PLAN 8.6) with the narrative model. `card` is the NPC card and the character summary,
    `history` the earlier (player line, npc reply) pairs of this conversation. None on any failure."""
    text, _ = await npc_reply_act(card, history, line, year=year)
    return text


_ACT_TAG = re.compile(r"\[\[\s*([a-z_]+)\s*\]\]")


async def npc_reply_act(
    card: dict[str, Any], history: list[tuple[str, str]], line: str, *, year: int | None = None,
) -> tuple[str | None, str | None]:
    """The reply and the action tag Claude put at its end (owner request 26.10), the tag cut out of the text.
    The tag is only a wish: services/npcact.apply checks it against the menu again."""
    if not is_live():
        return None, None
    content = _get_content()
    nb = content.balance.narrator
    limit = content.balance.narrator.message_max_chars
    first = prompts.user_message("npc_chat", card, target=int(limit * nb.target_ratio))
    messages: list[dict[str, Any]] = []
    turns = list(history) + [(line, "")]
    for i, (said, answer) in enumerate(turns):
        said = said.strip()[: nb.npc_chat_player_max_chars]
        body = (first + "\n\nРЕПЛИКА ИГРОКА:\n" if i == 0 else "РЕПЛИКА ИГРОКА:\n") + json.dumps(said, ensure_ascii=False)
        messages.append({"role": "user", "content": body})
        if answer:
            messages.append({"role": "assistant", "content": answer})
    reply = await _ask("npc_chat", messages, year=year)
    if reply is None:
        return None, None
    acts = _ACT_TAG.findall(reply.text)
    npc = card.get("npc") if isinstance(card.get("npc"), dict) else {}
    allowed = {str(m.get("key")) for m in (npc.get("actions") or []) if isinstance(m, dict)}
    act = next((a for a in reversed(acts) if a in allowed), None)
    raw = _ACT_TAG.sub("", reply.text).strip()
    known = {"card": card, "earlier": [answer for _, answer in history]}  # numbers of its own earlier replies are fine
    text, why = check_text(raw, known, limit, truncated=reply.truncated, year=year)
    if text is None:
        log.warning("narrator: npc_chat dropped (%s)", why)
        return None, None
    return text, act
