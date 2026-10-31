"""Classes of the city feed (PLAN 10.1a, 10.3, DESIGN.md 3): which public records are posted at once, which wait
for the nightly news, and how long each post stays in the chat. Pure constants; the posting is in services/feed.py."""

from __future__ import annotations

from dataclasses import dataclass

CLASS_NEWS = "news"            # the nightly digest, balance.ui.news_ttl_h
CLASS_ALERT = "alert"          # cyberpsycho, MaxTac, arrests: balance.ui.alert_ttl_h
CLASS_PERMANENT = "permanent"  # obituaries are never deleted

# event_log kinds posted the moment they are written (public=True), with the class of the post.
INSTANT_KINDS: dict[str, str] = {
    "death": CLASS_PERMANENT,
    "cyberpsychosis": CLASS_ALERT,
    "psycho_hunt": CLASS_ALERT,
    "maxtac_hunt": CLASS_ALERT,
    "psycho_caught": CLASS_ALERT,
    "psycho_ward": CLASS_ALERT,
    "maxtac_recruit": CLASS_ALERT,
    "arrest": CLASS_ALERT,
    "jail_escape": CLASS_ALERT,
    "wanted": CLASS_ALERT,
    "canon_reached": CLASS_NEWS,
    "legend": CLASS_PERMANENT,  # owner request 24.10: a cyberpsycho's legend deed stays in the chat
    "borg": CLASS_PERMANENT,  # owner request 25.10: a character became a borg (a full conversion); payload.text is HTML
    "loud": CLASS_ALERT,  # a loud deed (owner request): a jackpot, a bank job, a gang, a bounty; payload.text is HTML  # a character's own date reached a canon event or a generated beat (DESIGN.md 9a)
}

# owner request 25.10 «Сделай меньше спама объявлениями в беседе»: these instant kinds wait and go out together, one
# post per `ui.feed_batch_min`; deaths, legends and the live hunts stay instant
BATCH_KINDS: frozenset[str] = frozenset({"loud", "canon_reached", "wanted", "arrest", "jail_escape", "psycho_ward",
                                         "maxtac_recruit", "psycho_caught"})

# public kinds that are left for the nightly news (everything public that is not instant goes there too)
NEWS_KINDS: tuple[str, ...] = ("gig_result", "event_resolved", "promotion")


def is_instant(kind: str) -> bool:
    return kind in INSTANT_KINDS


def post_class(kind: str) -> str:
    return INSTANT_KINDS.get(kind, CLASS_NEWS)


# -- pictures of the feed (PLAN 11.2, DESIGN.md 15, docs/IMAGES.md) ----------------------------------------------------
# Only feed posts carry a picture; the player windows stay text. A request says which tags a picture should have:
# `want` is ordered, the first tag is the most specific (its weight is the highest), `require` tags must all be present.
# Without a matching picture the post goes out as plain text.

TAG_NEWS = "news"
TAG_DEATH = "death"
TAG_CYBERPSYCHO = "cyberpsycho"
TAG_WARD = "ward"
TAG_MAXTAC = "maxtac"
TAG_ERA = "era"
TAG_CANON = "canon"
TAG_BORG = "borg"  # owner request 27.10: a new borg in the city
ERA_PREFIX = "era_"      # era_<era key from eras.yaml>
CANON_PREFIX = "canon_"  # canon_<timeline id from timeline.yaml>
AT_PREFIX = "at_"        # at_<district key>: a cyberpsycho picture made for one district


@dataclass(frozen=True)
class ImageRequest:
    want: tuple[str, ...]
    require: tuple[str, ...] = ()
    # a picture with a tag of these prefixes that is not in `want` is made for something else and is not used
    # (a picture of one era or one district never stands in for another)
    exclude_prefixes: tuple[str, ...] = ()


def news_image() -> ImageRequest:
    return ImageRequest((TAG_NEWS,), (TAG_NEWS,))


def era_image(era_key: str) -> ImageRequest:
    """An era change: a picture of this era, else any `era` picture, else the news studio."""
    return ImageRequest((ERA_PREFIX + era_key, TAG_ERA, TAG_NEWS), exclude_prefixes=(ERA_PREFIX,))


def canon_image(event_id: str) -> ImageRequest:
    """A canon event: a picture of this event, else any `canon` picture, else the news studio."""
    return ImageRequest((CANON_PREFIX + event_id, TAG_CANON, TAG_NEWS), exclude_prefixes=(CANON_PREFIX,))


def instant_image(kind: str, district: str | None = None, ref: str | None = None) -> ImageRequest | None:
    """The picture wanted by an instant record (None = this kind is posted without a picture)."""
    if kind == "canon_reached":
        return canon_image(ref) if ref else news_image()
    if kind == "death":
        return ImageRequest((TAG_DEATH,), (TAG_DEATH,))
    if kind == "cyberpsychosis":
        want = (TAG_CYBERPSYCHO, AT_PREFIX + district) if district else (TAG_CYBERPSYCHO,)
        return ImageRequest(want, (TAG_CYBERPSYCHO,), (AT_PREFIX,))
    if kind == "legend":
        if ref == "legend_scene":  # owner request 27.10: the last deed of a legend (under its avatar, else text)
            return None
        if ref == "psyleg_studio":
            return news_image()
        if ref in ("psyleg_hunters", "psyleg_smasher"):
            return ImageRequest((TAG_MAXTAC, TAG_CYBERPSYCHO))
        return ImageRequest((TAG_CYBERPSYCHO,), (TAG_CYBERPSYCHO,), (AT_PREFIX,))
    if kind == "psycho_ward":
        return ImageRequest((TAG_WARD, TAG_CYBERPSYCHO))  # a ward picture, else the cyberpsycho one
    if kind == "borg" and ref == "borg":
        return ImageRequest((TAG_BORG,), (TAG_BORG,))
    if kind in ("psycho_hunt", "maxtac_hunt", "psycho_caught", "maxtac_recruit"):
        return ImageRequest((TAG_MAXTAC,), (TAG_MAXTAC,))
    return None  # arrests, escapes and wanted notices have no picture in v1


# every tag the code asks for (docs/IMAGES.md lists them for the owner)
PICTURE_TAGS: tuple[str, ...] = (
    TAG_NEWS, TAG_DEATH, TAG_CYBERPSYCHO, TAG_WARD, TAG_MAXTAC, TAG_ERA, TAG_CANON, TAG_BORG, ERA_PREFIX + "<era>", CANON_PREFIX + "<id>",
    AT_PREFIX + "<district>",
)
