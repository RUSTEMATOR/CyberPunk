"""Player-facing strings of the group: /graveyard, the city feed, the nightly news, eras, canon events, admin replies.

Style: /tmp/claude-0/style_ru.md, terms: /tmp/claude-0/terms_ru.md (corporations in Latin, gangs in Russian).
The strings that carry user-supplied names are used with `texts.esc` by the callers. Names stay in the
nominative and the posts use no gendered verbs: the feed does not know the character's gender.
"""

from __future__ import annotations

from app.bot.ui.style import ICONS as I
from app.bot.ui.style import header

# -- /legends, /graveyard ---------------------------------------------------------------------------------------

LEGENDS_ROW_DEAD = "{n} <s>{who}</s> (на кладбище) — <b>{v}</b>"

GRAVE_TITLE = header("grave", "Кладбище")
GRAVE_ROW = I["obituary"] + " {name} — {role}, {age}. Причина смерти: {cause}. Уличная репутация: {cred}."
GRAVE_ROW_EPITAPH = I["obituary"] + " {name} — {role}, {age}. Причина смерти: {cause}. Уличная репутация: {cred}. «{epitaph}»"
GRAVE_EMPTY = "Кладбище пока пустое. Это ненадолго."

# -- the feed: instant posts ----------------------------------------------------------------------------------

# Feed posts: an iconed bold headline, a separator line of the alert's kind, then the text.
FEED_PSYCHO = header("psycho", "КИБЕРПСИХОЗ") + "\n" + I["district"] + " {name}: срыв, район {district}.\n" + I["ncpd"] + " NCPD уже едет, МаксТак поднят по тревоге."
FEED_PSYCHO_NO_DISTRICT = header("psycho", "КИБЕРПСИХОЗ") + "\n" + I["profile"] + " {name}: срыв.\n" + I["ncpd"] + " NCPD уже едет, МаксТак поднят по тревоге."
FEED_PSYCHO_CAUGHT = header("maxtac", "МаксТак") + "\n{name}: задержание после срыва. Дальше — изолятор или контракт."
FEED_PSYCHO_CAUGHT_RECRUITED = header("maxtac", "МаксТак") + "\n{name}: задержание после срыва. Вместо камеры — контракт с МаксТаком."
FEED_PSYCHO_WARD = header("ward", "Изолятор") + "\n{name}: после срыва — принудительная терапия на {weeks} нед."
FEED_MAXTAC_RECRUIT = header("maxtac", "МаксТак") + "\nВ отряде новый боец: <b>{name}</b>. Старым знакомым лучше не попадаться на глаза."
FEED_MAXTAC_HUNT = header("maxtac", "ОХОТА МАКСТАКА") + "\n" + I["enemy"] + " Цель: <b>{name}</b>.\n" + I["danger"] + " Район оцеплен, жителей просят не выходить из домов."
FEED_ARREST = header("arrest", "Арест") + "\n{name} в руках NCPD. Срок: {weeks} нед."
FEED_JAIL_ESCAPE = header("escape", "Побег") + "\n{name} на свободе раньше срока. NCPD объявила розыск."
FEED_WANTED = header("heat", "Розыск NCPD") + "\n" + I["profile"] + " <b>{name}</b>, уровень {heat}.\nКто видел — звоните. Кто не видел — не задавайте вопросов."
FEED_DEATH = header("obituary", "Некролог") + "\n<blockquote>{text}</blockquote>"
OBIT_ACHIEVEMENT = "{weeks} {weeks_word} на улицах Найт-Сити"
OBIT_ACHIEVEMENT_NONE = "жизнь короче недели"


def weeks_word(n: int) -> str:
    """'неделя', 'недели', 'недель' for n."""
    if 11 <= n % 100 <= 14:
        return "недель"
    if n % 10 == 1:
        return "неделя"
    if 2 <= n % 10 <= 4:
        return "недели"
    return "недель"


# -- the nightly news ---------------------------------------------------------------------------------------------

NEWS_TITLE = header("news", "Good Morning, Night City!")
NEWS_RECAP = "📡 <b>Кто чем жил за сутки:</b>"
NEWS_NEW_DAY = "Новый день в Найт-Сити — /play"
NEWS_TAGS = "📡 В эфире для: {names}. Новый день в Найт-Сити — /play"
NEWS_MORE_DISTRICTS = I["district"] + " Ещё в сводке: {n} {word}."
NEWS_DISTRICT_FALLBACK = "без района"
NEWS_WAR_START = I["war"] + " <b>Война банд:</b> {gang}, район {district}. Заказы против банды платят больше, но и на улицах опаснее."
NEWS_WAR_START_NO_DISTRICT = I["war"] + " <b>Война банд:</b> {gang}. Заказы против банды платят больше, но и на улицах опаснее."
NEWS_WAR_END = I["ok"] + " <b>Война банд окончена:</b> {gang}. На улицах снова обычная жизнь."

# -- eras, canon events -------------------------------------------------------------------------------------------

ERA_CHANGED = header("era", "Новая эпоха: {name}") + " ({from_year}–{to_year})\n<blockquote>{player_text}</blockquote>"
ERA_PRICES_UP = I["chrome"] + " Цены на импланты: ×{mult:g} от обычных."
ERA_PRICES_SAME = I["chrome"] + " Цены на импланты вернулись к обычным."
ERA_HUMANITY = I["humanity"] + " Потеря человечности от имплантов: ×{mult:g}."

# personal time: the date after a week, the window screen of a beat the character's date reached, the feed post
WEEK_DATE = "<i>{year} год, неделя {week}</i>"
BEAT_WINDOW_TITLE = header("canon", "{year}: {title}")
BEAT_WINDOW_NEXT = "Дальше"
FEED_CANON_REACHED = {
    "m": header("canon", "{year}: {title}") + "\n" + I["profile"] + " Свидетель: <b>{name}</b>.",
    "f": header("canon", "{year}: {title}") + "\n" + I["profile"] + " Свидетельница: <b>{name}</b>.",
}

# -- shops and rippers under the world's effects --------------------------------------------------------------------

RIPPER_BANNED_NOTE = I["lock"] + " <i>Запрещён законом. Подпольный риппер поставит, но об операции узнают: розыск +{heat}.</i>"
RIPPER_BANNED_HEAT = I["heat"] + " Имплант под запретом, и об операции узнали. Розыск: {heat}."

# -- admin replies ---------------------------------------------------------------------------------------------------

ADMIN_USAGE_TICK = "Формат: /admin_tick. Запускает суточный тик реального времени: остывание районов, войны банд, курс монеты, утренний выпуск Good Morning, Night City."
ADMIN_TICK_DONE = "Тик выполнен. Дней: {days}. Курс монеты: {rate:g}. Новостей отправлено: {news}. Войн банд началось: {wars}."
ADMIN_USAGE_YEAR = "Формат: /admin_year [@игрок] — личная дата игрока (без аргумента — твоя)."
ADMIN_YEAR_SHOW = (
    "{who}: {year} год, неделя года {woy}. Эпоха: {era}. Прожито недель: {lived}. "
    "Следующее канонное событие: {next_event}."
)
ADMIN_YEAR_START = "{who}: персонаж ещё не создан. Род начнёт с {year} года."
ADMIN_YEAR_NO_NEXT = "канон закончился"
ADMIN_YEAR_NEXT = "{title} ({year}, неделя {week})"
ADMIN_HISTORY_TITLE = "<b>История мира</b> (последние {n})"
ADMIN_HISTORY_ROW = "#{id} [{kind}{status}] {year}, нед. {week}: {title}"
ADMIN_HISTORY_EMPTY = "История пуста."
ADMIN_USAGE_VETO = "Формат: /admin_veto &lt;номер записи&gt;."
ADMIN_VETO_DONE = "Событие #{id} «{title}» отменено, эффекты сняты."
ADMIN_VETO_NOT_FOUND = "Записи #{id} нет."
ADMIN_VETO_NOT_GENERATED = "Отменить можно только сгенерированное событие. Запись #{id} — {kind}."
ADMIN_VETO_ALREADY = "Запись #{id} уже отменена."
ADMIN_VETO_LATE = "Запись #{id} старше {hours} ч. Отменять поздно."


def districts_word(n: int) -> str:
    """'район', 'района', 'районов' for n."""
    if 11 <= n % 100 <= 14:
        return "районов"
    if n % 10 == 1:
        return "район"
    if 2 <= n % 10 <= 4:
        return "района"
    return "районов"
