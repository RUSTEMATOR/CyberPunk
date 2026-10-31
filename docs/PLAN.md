# Night City Life — план реализации v1

Исполнитель работает строго по порядку шагов. Перед стартом прочитать `CLAUDE.md` и `docs/DESIGN.md`.
Метка у шага показывает, какой модели его отдавать:
- **[Sonnet]** — логика, архитектура, интеграции;
- **[Haiku]** — механическая работа: заполнение YAML по образцу, тексты-заготовки, манифесты.

Если модель не переключается, [Haiku]-шаги делает Sonnet. Это просто дороже.

После каждого шага: коммит `M<этап>.<шаг>: <что сделано>` и строка в `docs/PROGRESS.md`.

---

## Стек

- Python 3.12
- aiogram 3 (FSM, inline-клавиатуры, роутеры)
- SQLAlchemy 2 (async) + aiosqlite, база — один файл SQLite в `data/`
- pydantic 2 + pydantic-settings — конфиг и валидация контента
- PyYAML — весь игровой контент и баланс
- anthropic (официальный Python SDK, `AsyncAnthropic`)
- APScheduler (AsyncIOScheduler) — глобальный тик
- Docker + docker-compose, том `./data`

Версии: последние стабильные на момент установки, зафиксировать в `requirements.txt`.

## Структура репозитория

```
nightcity-bot/
  CLAUDE.md
  README.md
  requirements.txt
  Dockerfile
  docker-compose.yml
  .env.example
  docs/  DESIGN.md PLAN.md PROGRESS.md QUESTIONS.md TEST_CASES.md IMAGES.md
  content/                # весь игровой контент и баланс, только YAML
    balance.yaml roles.yaml origins.yaml debts.yaml traits.yaml names.yaml
    districts.yaml fixers.yaml gangs.yaml corps.yaml ripperdocs.yaml
    cyberware.yaml weapons.yaml armor.yaml housing.yaml careers.yaml
    gig_templates.yaml events.yaml fallback_texts.yaml
  images/  manifest.yaml  *.png
  app/
    main.py               # запуск: бот, роутеры, планировщик
    config.py             # Settings (pydantic-settings)
    content/              # schemas.py (pydantic-модели контента), loader.py
    db/                   # session.py, models.py, repo.py
    engine/               # ЧИСТАЯ логика, без Telegram, без БД, без LLM
      dice.py checks.py character_gen.py derived.py effects.py
      week.py events.py gigs.py combat.py cyberware.py death.py
      legacy.py relations.py careers.py crime.py world.py
    services/             # склейка: БД -> engine -> БД -> narrator -> view
    narrator/             # client.py prompts.py narrator.py budget.py schemas.py
    bot/
      handlers/           # start.py menu.py profile.py gigs.py ripper.py ...
      keyboards.py texts.py middlewares.py
    scheduler/tick.py
```

## Архитектурные правила (обязательны)

1. `engine/` — чистые функции. Случайность только через переданный `rng: random.Random`. На вход — dataclass-состояние и контент, на выход — dataclass-результат. Никакого IO.
2. Все числа баланса лежат в `content/balance.yaml`. В коде нет магических чисел, кроме очевидных (d10).
3. Все изменения персонажа идут через одну функцию `engine/effects.py::apply_effects(state, effects, content) -> AppliedEffects`. Она же делает клампы: здоровье 0..max, человечность 0..max, отрицательные эдди переводятся в долг.
4. Narrator получает только структурированный результат и возвращает только текст. Числа он не меняет. При любой ошибке — текст из `fallback_texts.yaml`.
5. Каждый callback несёт `character_id`. Если нажал не владелец персонажа — отказ.
8. Игровой процесс никогда не отправляет новые сообщения напрямую: обработчик возвращает `Screen`, а `WindowManager` редактирует окно игрока. Прямая отправка разрешена только для ленты города и временных ответов, и всё это идёт через `outbox` (очередь с лимитом) и `cleanup` (отложенное удаление).
9. Игровые команды и кнопки работают только в группе из `world_state.group_chat_id`.
10. Модификаторы (DESIGN.md раздел 8 «Модификаторы и билды») — единственный способ, которым импланты, броня, оружие и черты влияют на числа. Любая формула, где есть подходящий модификатор, берёт его через `engine/derived.py::mod(state, key)`. Никаких проверок вида `if has_implant('...')` в логике.
12. Всё, что зависит от времени (импланты, районы, банды, фиксеры, роли, враги, события), берётся из контента только через `content.available(kind, year)` с текущим годом мира. Прямой перебор `content.cyberware.values()` в игровой логике запрещён.
13. Игровые действия публикуют события в шину `services/game_events.py` (`gig_done`, `crime_done`, `family_time`, `meditate`, `fight_won`, `takedown` и т.д.). Ежедневные задания, обеты и легенды недели слушают шину, а не встраиваются в обработчики.
11. Игрок и враг в бою — один тип `Combatant`. Импланты, заряд, взломы и модификаторы врагов считаются тем же кодом, что у игрока.
6. Пользовательские тексты на русском в `bot/texts.py` или в YAML. Идентификаторы в коде — на английском.
7. Ключи контента — латиница snake_case (`watson`, `tyger_claws`, `mantis_blades`), отображаемые имена — по-русски в поле `name`.

## Схема БД (SQLAlchemy-модели)

- `players`: tg_id (PK), username, created_at, lineage_id, weeks_today, weeks_today_date
- `lineages`: id, player_tg_id, family_name, lives_count, glitch_bonus
- `characters`: id, player_tg_id, lineage_id, alive, name, surname, role, origin, age_years, age_weeks, district, housing, stats (JSON: BODY REF TECH INT COOL EMP LUCK), skills (JSON: skill -> {level, xp}), hp, mood, humanity, humanity_lost, cyber_tolerance, tolerance_known, street_cred, heat, eddies, luck_pool, glitches, death_glitch_used, ap, weeks_lived, career (JSON), flags (JSON), created_at, died_at, death_cause
- `debts`: id, character_id, creditor, principal, weekly_rate, missed_weeks
- `cyberware`: id, character_id, item_key, slot, installed_week, complication, charge (0–100, по умолчанию 100)
- `inventory`: id, character_id, item_key, kind (weapon/armor/item), equipped
- `npcs`: id, character_id, kind (family/partner/friend/contact/protege/child/fixer/ripper), name, role, relation, alive, data (JSON)
- `gigs`: id, district, fixer, type, tier, pay, target, template_key, created_day, taken_by, status, brief_text
- `gig_runs`: id, gig_id, character_id, approach, checks (JSON), outcome, effects (JSON), result_text, created_at
- `event_log`: id, character_id, kind, payload (JSON), public (bool), created_at, published (bool)
- `world_state`: key (PK), value (JSON) — group_chat_id, district_heat, gang_wars, last_tick
- `graveyard`: id, character_id, lineage_id, name, born_age, died_age, role, cause, street_cred, epitaph, obituary_text, died_at
- `llm_usage`: id, ts, kind, model, input_tokens, output_tokens, cache_read, cache_write, cost_usd
- `image_cache`: file (PK), tg_file_id
- `windows`: player_tg_id (PK), chat_id, message_id, screen, screen_state (JSON), awaiting_text (str или null), last_active, collapsed (bool)
- `cleanup_queue`: id, chat_id, message_id, delete_at, kind

---

## Порядок выполнения (решение владельца)

M0–M4 готовы. Дальше: M5 → M9 → M6 → M10 → M11 → M12 → M7 → M8. Смерть и наследие (M7) и рассказчик Claude (M8) идут последними; до них смерть и нарратив работают через заглушки (выжил с 1 ХП, тексты-заготовки).

## Личное время (переделка M10, решение владельца)

Общие мировые часы и лимит недель в сутки отменены: см. DESIGN.md 9a «Личное время». Год персонажа = год рода; доступность, эпохи, цены, канонные события и мировые эффекты считаются по году персонажа; будущее после канона общее по годам (первый дошедший запускает генерацию). Доски заказов личные. Перемотка не нужна.

## M0. Скелет — [Sonnet]

**0.1** Создать структуру из раздела выше: `requirements.txt`, `Dockerfile` (python:3.12-slim, non-root user), `docker-compose.yml` (один сервис `bot`, `restart: unless-stopped`, том `./data:/app/data`, `env_file: .env`, `TZ=Europe/Kyiv`), `.env.example`:
```
BOT_TOKEN=
ANTHROPIC_API_KEY=
ADMIN_IDS=            # через запятую
GROUP_CHAT_ID=        # можно оставить пустым, потом /admin_setgroup
DB_PATH=data/game.db
TIMEZONE=Europe/Kyiv
MONTHLY_BUDGET_USD=100
MODEL_NARRATIVE=claude-sonnet-5-5
MODEL_FAST=claude-haiku-5-5
# Цены за 1M токенов — заполнить по прайсу на docs.claude.com
PRICE_NARRATIVE_IN=
PRICE_NARRATIVE_OUT=
PRICE_FAST_IN=
PRICE_FAST_OUT=
LLM_ENABLED=true
```
**0.2** `app/config.py`: `Settings` через pydantic-settings, `ADMIN_IDS` парсится в `set[int]`.
**0.3** `app/db/models.py` по схеме выше, `session.py` (async engine, `async_sessionmaker`), создание таблиц при старте (`create_all`). Миграций в v1 нет.
**0.4** `app/main.py`: Bot, Dispatcher с MemoryStorage для FSM, подключение роутеров, команда `/ping` → «pong», запуск планировщика (пока пустого), логирование в stdout и `data/bot.log`.
**0.5** `README.md`: как создать бота у @BotFather, отключить privacy mode (`/setprivacy` → Disable), добавить бота в группу администратором с правом «Удаление сообщений», заполнить `.env`, `docker compose up -d --build`.

Готово, когда: `docker compose up` поднимается без ошибок, бот отвечает на `/ping` в группе.

## M1. Контент

**1.1 [Sonnet]** `app/content/schemas.py` — pydantic-модели для каждого YAML-файла. `loader.py` загружает все файлы в один объект `Content` и валидирует перекрёстные ссылки: фиксер ссылается на существующий район, событие — на существующие навыки и так далее. При ошибке бот не стартует и пишет, какой файл и какая запись сломаны.
В каждый YAML положить **одну** образцовую запись. Форматы:

`balance.yaml` — все числа из DESIGN.md: ОД в неделю, СЛ по уровням (13/15/17/19/21 для заказов 1–5), диапазоны оплаты, проценты долга, аренда, цены активностей, таблица кибертолерантности, стоимость Trauma Team (неделя: 100/300/1 000; эвакуация: 1 000/2 000/5 000), наследование, шансы событий за неделю (1 гарантировано, 2-е 40%, 3-е 15%), шанс облавы, тюремные сроки, урон оружия.

`events.yaml` (формат события):
```yaml
- id: scav_ambush
  title: Засада мусорщиков
  weight: 3
  conditions:
    districts: [watson, pacifica]   # null = любой
    roles: null
    min_heat: null
    max_humanity: null
    requires: [has_cyberware]       # флаги-условия, список в schemas.py
  text: "Переулок, двое в масках и шокер. Им нужен твой хром."
  choices:
    - label: Драться
      check: {stat: BODY, skill: melee, dv: 15}
      success: {text: "...", effects: {street_cred: 1}}
      fail: {text: "...", effects: {start_combat: 2}}
    - label: Бежать
      check: {stat: REF, skill: stealth, dv: 13}
      success: {text: "...", effects: {}}
      fail: {text: "...", effects: {hp: -6}}
    - label: Отдать эдди
      effects: {eddies: -200}
      text: "..."
```

Допустимые ключи `effects` (зафиксировать в schemas.py): `eddies, hp, mood, humanity, street_cred, heat, debt, luck_pool, glitches, skill_xp:<skill>, rel_npc:<tag>, rel_fixer:<key>, add_npc:<template>, remove_random_cyberware, infection, start_combat:<danger 1-5>, jail_weeks, flag_set:<name>, flag_clear:<name>, death:<cause>`.

`gig_templates.yaml`:
```yaml
- key: data_theft
  type: hack          # delivery | theft | elimination | hack | escort | cyberpsycho | missing
  titles: ["Данные {target}", "Осколок из офиса {target}"]
  targets: [corp, gang]
  approaches:
    loud:  [{stat: REF, skill: firearms}, {stat: BODY, skill: melee}]
    quiet: [{stat: REF, skill: stealth}, {stat: INT, skill: netrunning}]
    talk:  [{stat: COOL, skill: persuasion}, {stat: COOL, skill: persuasion}]
    hack:  [{stat: INT, skill: netrunning}, {stat: TECH, skill: tech}]
```

Остальные файлы — по таблицам DESIGN.md: роли (шаблон статов, стартовые навыки, стартовое снаряжение), районы, фиксеры, банды, корпорации, рипперы (модификатор цены, шанс осложнения), импланты, оружие, броня, жильё, карьеры (5 ступеней: название, зарплата, СЛ повышения, минимальный стаж в неделях).

**1.2 [Haiku]** Заполнить все YAML по образцу и DESIGN.md:
- `roles.yaml` — 10 ролей;
- `origins.yaml` — 10, `debts.yaml` — 10;
- `traits.yaml` — 20 черт, 20 примет, 20 одержимостей;
- `names.yaml` — 60 имён и 60 фамилий, смесь происхождений, как в Найт-Сити;
- `districts.yaml`, `fixers.yaml`, `gangs.yaml`, `corps.yaml`, `ripperdocs.yaml` — по разделу 9;
- `cyberware.yaml` — по разделу 8, `weapons.yaml` — 8 позиций, `armor.yaml` — 4, `housing.yaml` — по разделу 11;
- `careers.yaml` — 6 карьер;
- `gig_templates.yaml` — по 2 шаблона на каждый из 7 типов;
- `events.yaml` — 40 событий: 10 уличных, 6 про импланты и человечность, 6 про долг и деньги, 6 про отношения, 4 про NCPD, 4 про работу, 4 редких;
- `fallback_texts.yaml` — по 3 варианта текста на каждый тип narrator (раздел M8) и на каждый исход заказа.

Тексты короткие, жёсткие, сленг из DESIGN.md, без эмодзи.

**1.3 [Haiku]** Запустить загрузчик (`python -m app.content.loader`), исправить все ошибки валидации.

Готово, когда: загрузчик проходит без ошибок и печатает количество записей по каждому файлу.

**1.4 [Sonnet]** Модификаторы, противники, быстрые взломы (DESIGN.md раздел 6 «Противники», раздел 8 «Модификаторы и билды», «Кибердека и быстрые взломы»):
- `schemas.py`: тип `Mods = dict[str, int]` с валидатором грамматики ключей из таблицы модификаторов DESIGN.md (`stat.REF`, `skill.tech`, `xp.all`, `check.gig_talk`, `salary_pct.corpo`, `active.sandevistan.recharge_per_tick` и т.д.; стат, навык, контекст, карьера, группа и поле проверяются по спискам). Поле `mods: Mods = {}` у `Cyberware`, `Weapon`, `Armor` и у черт/примет/одержимостей в `traits.yaml`.
- Перенести старые поля имплантов в `mods` и удалить их из схемы: `skill_bonus` → `skill.*`, `strength_bonus` → `strength`, `flee_bonus` → `flee`, `bleed_reduction` → `bleed`, `armor` → `armor`, `combat_stat_bonus` → `check.combat` (сумма). Остаются: `weapon_damage`, `weapon_targets`, `resurrection`, `smasher_only`, `active`, `requires`, `exclusive_group`. Поле `tier: Literal["street", "mid", "military"]` (для подбора врагам и описаний). Поле `category: Literal["combat", "noncombat", "booster", "netrun", "defense"]`.
- Кибердека: блок `deck: {ram_max, ram_recharge, hack_bonus}` у имплантов с `exclusive_group: cyberdeck`.
- `content/quickhacks.yaml` + схема: `key, name, description, price, ram_cost, dv_base, dv_per_danger, tier, effects: {damage, stun_ticks, attack_penalty, penalty_ticks, drain_charge, disable_ticks, spread}`.
- `content/enemies.yaml` + схема: `key, name, factions (ключи банд/корпораций, плюс cyberpsycho), danger 1–5, hp (необязательно, иначе формула), attack, defense, weapon (ключ), armor (ключ или null), cyberware (ключи), quickhacks (ключи), ai: brute|tactical|netrunner|coward, leader: bool`. Загрузчик проверяет ссылки, `requires` и `exclusive_group` у врагов так же, как у ролей; у каждой фракции из `gangs.yaml` и у arasaka, militech, kang_tao, ncpd должен быть хотя бы один архетип на каждый уровень опасности, где фракция встречается в заказах (минимум: 1–3 для банд, 2–5 для корпораций и ncpd).
- `balance.yaml`: раздел `mods_caps` (`ap_week: 1`, `shop_discount_pct: 50`, `ripper_discount_pct: 50`, `humanity_loss_pct: -50`, остальное без потолка), раздел `enemy_ai` (пороги поведения из DESIGN.md), раздел `netrun` (базовый `ice`, СЛ взломов по опасности, подзарядка RAM).
- Загрузчик читает все файлы по маске: `cyberware*.yaml`, `enemies*.yaml`, `events*.yaml` объединяются, дубли ключей — ошибка. Так контент можно раскладывать по файлам категорий.
- Обновить `docs/CONTENT_GUIDE.md` и `docs/CONTENT_KEYS.md` (новые разделы: quickhacks, enemies, мод-ключи), по одной образцовой записи в `quickhacks.yaml` и `enemies.yaml`.
- Проверка: загрузчик проходит, намеренная ошибка в мод-ключе ловится с понятным сообщением.

**1.5 [Haiku, параллельно]** Наполнение каталога по CONTENT_GUIDE (каждый агент пишет в свой файл, загрузчик объединяет):
- `cyberware_combat.yaml`: ещё 15 боевых и защитных имплантов (оптика со сканером, умные линки, броня, рефлексы, ещё модели существующих типов) и 10 усилителей (`category: booster`), разные уровни и цены, принцип «дорогое — гибкое»;
- `cyberware_noncombat.yaml`: 25 небоевых имплантов: минимум по 2 на каждую роль и карьеру, плюс общие (сон, память, стабилизатор, тайник, эндокринный регулятор);
- `cyberware_netrun.yaml` и `quickhacks.yaml`: ещё 2 кибердеки (уличная и военная), 3 защитных импланта (`ice`), 12 быстрых взломов;
- `enemies.yaml`: архетипы по правилу покрытия из 1.4 (ориентир 35–45), почерк фракций из DESIGN.md.

**1.6 [Sonnet]** Пути, без хрома, эпохи, ежедневки — схема и загрузчик (DESIGN.md разделы 1, 5, 5a, 7a, 9a, 12a):
- Доступность по времени: поля `available_from: int | None`, `available_until: int | None` (годы) у cyberware, quickhacks, weapons, armor, districts, gangs, corps, fixers, ripperdocs, roles, origins, enemies, events, gig_templates, careers. Метод `Content.available(kind, year)`. Загрузчик: from ≤ until; ссылки между записями не должны указывать на запись, которой нет в нужные годы (фиксер района, банды района, снаряжение роли, импланты врага) — это предупреждение, в `--strict` ошибка.
- `eras.yaml`: key, name, from_year, to_year, brief (для Claude, до 600 знаков), player_text, chrome_price_mult, chrome_humanity_mult, news_tone. Эпохи без дыр и наложений.
- `timeline.yaml`: id, year, week (1–52), title, description (контекст для Claude), announcement (текст поста), effects (меню мировых эффектов), canon: true, source_note (откуда дата). Меню эффектов (`WorldEffect`): `price_mult.<категория импланта|weapons|housing>`, `district_danger.<район>`, `gang_war.<банда>` (недели), `faction_power.<фракция>`, `unlock.<ключ предмета>`, `ban.<ключ предмета>`, `heat_mult`, `gig_pay_mult`, `event_weight.<категория>`, `district_open.<район>`, `district_close.<район>`, `era.<ключ эпохи>`. Коридоры в `balance.world_effects`.
- `balance.yaml`: раздел `world` (start_year 2013, weeks_per_day 7, catchup_weeks_per_day 21, future_beat_weeks 26, history_veto_hours 24); `chrome_free` (xp_pct, check бонусы social/heal/meditation, quickhack_penalty); `daily` (база эдди, таблица наград за серию, жетоны за задания, число заданий 3+1); `caliber` (кубик изменения, например `1d10-4`, титулы по порогам мм); `dirt` (накопление `1d3`, цена слива 5, кулдаун цели 3 дня, СЛ защиты); `reputation` (потолки 50 для street_cred, status, fame).
- Роль `civilian` (Обычный житель) в `roles.yaml`, всегда предлагается четвёртой. Происхождения `monastery_novice` и `preacher_commune` с флагом `chrome_free_start: true` и стартовым обетом.
- Эффекты событий: добавить `status`, `fame`, `vow_break`, `dirt`, `tokens`, `buff:<tarot|quest ключ>`. Выбор события получает флаг `violent: bool`; загрузчик: если в событии есть выбор с `start_combat` или `violent`, обязан быть выбор без них (небоевой выход). Нарушения на текущих 40 событиях — список предупреждений, исправит Haiku в 1.7.
- `quickhacks.yaml`: поле `environmental: bool` (работает по чистым).
- `vows.yaml`: key, name, description, forbids (`cyberware`, `violence`, `braindance`, `crime`, `lies`), streak_bonuses (список `{weeks, mods}`), break_effects.
- `tarot.yaml`: 22 Старших аркана: key, name, text, mods (временные на сутки, могут быть отрицательными), eddies, weight.
- `quests.yaml`: key, text, period (`daily`|`weekly`), paths (`combat`, `noncombat`, `chrome_free`, `any`), goal (тип из enum событий шины + параметры, например `{event: gig_done, approach: quiet}`), target, rewards `{eddies, xp, tokens, street_cred, status, fame}`.
- `afterlife_shop.yaml`: key, name, description, price_tokens, effect (`stim`, `recharge_all`, `streak_freeze`, `tarot_reroll`, `title:<текст>`).
- БД (`app/db/models.py`): у `Player` — streak, last_daily, tokens, caliber_mm, dirt, dirt_targets (JSON: tg_id → дата), titles (JSON), freeze_count; у `Character` — status, fame, chrome_free, vows (JSON: key → серия недель), world_week_offset не нужен (время персонажа = `weeks_lived` + год рождения); новые таблицы `buffs` (id, player_tg_id, character_id null, source, mods JSON, expires_at), `quest_progress` (id, player_tg_id, quest_key, period, period_start, progress, target, done, claimed), `world_history` (id, world_week, year, kind `canon|local|generated`, ref_id, title, text, effects JSON, status `active|vetoed`, created_at).
- Обновить CONTENT_GUIDE.md и CONTENT_KEYS.md, по одной образцовой записи в каждом новом файле.

**1.8 [Sonnet]** Пресеты, роли из стиля игры, нрав фракций (DESIGN.md разделы 5, 5b, 6 «Выходы без боя»):
- Разделить `roles.yaml`: стартовые наборы переезжают в `presets.yaml` (key, name, description, stat_template, skills, gear, starting_career, origin_hint, chrome_free_start, available_from/until). Текущие 10 ролей + `civilian` превращаются в пресеты с нейтральными названиями (Уличный стрелок, Ребёнок Сети, Гаражный мастер, Санитар, Делец, Уличный музыкант, Медиа-стрингер, Корпоративный стажёр, Патрульный-новичок, Кочевник, Обычный житель); послушник — пресет с chrome_free_start.
- Новый `roles.yaml` — эмерджентные роли: key, name, description, affinity (словарь `событие шины -> вес`, с параметрами, например `gig_done:loud: 3`), decay_pct (в неделю), rank_thresholds, perks (список `{rank, name, description, mods}`). 13 ролей: 10 RED + monk, hustler, trader. Ссылки на роли в событиях (`conditions.roles`) означают основную роль; добавить `conditions.min_role_rank: {role: N}`.
- Нрав фракций: блок `disposition` в `gangs.yaml` и `corps.yaml`: `bribe_dv`, `talk_dv`, `bribe_cost_mult`, `flee_mod`, `surrender` (веса исходов: `released`, `robbed`, `beaten`, `chrome_stripped`, `arrested`, `captured`, `executed`, `vanished`), `talk_bonus_if` (условия: район, происхождение, роль, хром). Особые нравы `maxtac` и `cyberpsycho` в `balance.yaml`. Раздел `exits` в балансе: модификаторы ситуации и их коридоры, шаг взятки.
- Мод-ключи: добавить контексты `check.bribe`, `check.talk_exit`.
- БД: `characters.role_affinity` (JSON), `characters.faction_rep` (JSON), события шины для сродства перечислить в `services/game_events.py` позже (пока enum в schemas.py).
- Мини-игры (DESIGN.md 12a): разделы баланса `caliber` (уже есть), `car` (кубик тюнинга, шанс поломки, формула гонки, ставки), `cat` (уровни, пороги обиды и ухода, бой), `coin` (стартовый курс, волатильность, комиссия, реакция на мировые эффекты: добавить мировой эффект `coin_shock` в меню); `cat_shop` в `afterlife_shop.yaml` (эффект `cat_item:<ключ>`). БД: у `Player` — car_hp, car_name, car_parts (JSON), cat (JSON: имя, уровень, сытость, последний корм, хром, ушёл), coins; курс монеты и история в `world_state`.
- Обновить CONTENT_GUIDE и CONTENT_KEYS; заполнить реальные `disposition` для всех банд и корпораций по таблице DESIGN.md (это данные из дизайна, не Haiku); роли — по одной образцовой (solo), остальные заполнит Haiku в 1.7c.

**1.7 [Haiku, частично параллельно]** (после 1.8) Контент путей, эпох и ежедневок:
- **1.7a** (с веб-поиском) `eras.yaml` и `timeline.yaml`: канонная история Найт-Сити и мира Cyberpunk от основания города до 2077, 25–35 событий с эффектами, у каждого `source_note`. Даты сверять по вики (cyberpunk.fandom.com) и книгам RED.
- **1.7b** (после 1.7a) проставить `available_from/until` всему контенту по эпохам; добавить ранние импланты и оружие (грубые, дорогие, тяжёлые для человечности) так, чтобы в стартовую эпоху было из чего выбирать (минимум 10 имплантов); ранних фиксеров и рипперов для эпох до 2077.
- **1.7f** MaxTac: карьера `maxtac` (5 рангов, требования, зарплата, срок службы), имплант `maxtac_inhibitor` (категория defense, мод нижнего порога человечности через баланс, недоступен в продаже), раздел `balance.cyberpsychosis` (hunt_weeks, duty_weeks, recruit: база и бонусы, isolation_weeks, chrome_stripped_pct, humanity_floor), 4 события службы и 3 события беглого киберпсиха, архетипы `cyberpsycho_*` (не меньше 4, опасность 3–5) и MaxTac (`ncpd_maxtac_*`, опасность 4–5) в 1.7e. Схемные поля (флаг `not_for_sale` у импланта, `humanity_floor` мод) добавляет Sonnet в начале 1.7f.
- **1.7c** `tarot.yaml` (22 карты), `quests.yaml` (25 ежедневных и 8 недельных, поровну по путям), `afterlife_shop.yaml` (8 позиций), `vows.yaml` (4 обета), ещё 4 пресета (например, Беженец, Дитя мегабашни, Мелкий торговец, Ученик риппера), 12 эмерджентных ролей с перками по образцу solo.
- **1.7d** события: исправить предупреждения про небоевой выход; добавить 15 событий: 6 для небоевого пути (карьера, бизнес, сцена, медиа), 5 для пути без хрома (монастырь, община, презрение и вербовка), 4 эпохальных (ранний хром, корпоративная война).
- **1.7e** (после 1.7b) `enemies.yaml`: архетипы по правилу покрытия с учётом эпох (ориентир 40–55).

## M2. Ядро движка — [Sonnet]

**2.1** `engine/dice.py`: `d(sides, rng)`, `roll(expr, rng)` для выражений вида `2d6+1`, `d10_check(rng) -> (value, crit)` с правилами RED: 10 добавляет d10, 1 вычитает d10.
**2.2** `engine/checks.py`: `check(stat_val, skill_val, mods, dv, rng) -> CheckResult(total, base_roll, crit, ok, margin)`. `outcome_tier(results, key_index) -> Outcome` (`clean | complication | fail | disaster`) строго по DESIGN.md раздел 6.
**2.3** `engine/character_gen.py`:
- `offer_roles(rng, content) -> list[str]` — 3 разные роли;
- `generate(rng, content, role_key, lineage) -> CharacterDraft`: статы по шаблону роли ±1 (кламп 2–8), навыки, происхождение, долг, черты, возраст 18–30, имя, семья (родители и 0–3 брата или сестры как NPC-черновики), бросок кибертолерантности (скрыт), здоровье, человечность = ЭМП×10, пул удачи = УДАЧА, глюки = d4−1 плюс бонус рода.
**2.4** `engine/derived.py`: `collect_mods(state, content) -> dict[str, int]` (сумма `mods` всех имплантов, экипированного оружия и брони, черт; с потолками `mods_caps`; кэш на время одного действия), `mod(state, key, default=0)`, `active_spec(state, cyberware_key)` — `ActiveSpec` с применёнными `active.<группа|all>.*` (кламп: min_charge 1..100, duration_ticks ≥ 1, cost ≥ 1), `max_hp` (+`hp_max`), `humanity_max`, `effective_emp`, `stat_total` (+`stat.*`), `skill_total` (+`skill.*`), `check_bonus(state, context)` (+`check.*`), `armor_value` (+`armor`), `heavy_wound`, `humanity_loss(base, state)` (множитель толерантности и `humanity_loss_pct`).
**2.5** `engine/effects.py::apply_effects` — все ключи из M1.1 и клампы. Добавить ключ `add_cyberware:<key>` (в schemas.py и в apply_effects, с проверкой слота и потерей человечности по правилам M6) и использовать его в событиях про дешёвый хром у подпольного риппера. Эффекты, которые требуют дальнейших действий (`start_combat`, `death`, `jail_weeks`), возвращаются в `AppliedEffects.followups`.
**2.6** `narrator/narrator.py`: интерфейс `async def narrate(kind: str, payload: dict) -> str` и `async def classify_action(text, context) -> dict | None`. Пока это заглушка: берёт случайный текст из `fallback_texts.yaml` и подставляет поля payload через `str.format_map` с безопасным словарём.

Готово, когда: `python -c` с фиксированным seed генерирует персонажа и печатает лист, а проверки дают ожидаемые исходы.

## M3. Окно, онбординг, меню, админка — [Sonnet]

Вся игра идёт в группе, у каждого игрока одно окно-сообщение, которое редактируется (DESIGN.md раздел 3). Сначала каркас интерфейса, потом экраны.

**3.0** Каркас интерфейса `app/bot/ui/`:
- `screen.py`: `Screen(text: str, keyboard: list[list[Button]], awaiting_text: str | None = None)`. Любой игровой обработчик возвращает `Screen`. Обрезка текста до 3 500 символов (обрезается начало, а не конец: последние действия важнее).
- `outbox.py`: очередь на чат. Не больше 15 операций в минуту на чат (настройка `ui.max_ops_per_minute` в balance.yaml). Правки одного сообщения склеиваются: в очереди хранится только последнее состояние. Ошибку `message is not modified` игнорировать. `RetryAfter` — подождать указанное время и повторить.
- `window.py`, `WindowManager`:
  - `open(chat_id, user)` — удалить старое окно (ошибки удаления игнорировать), отправить новое, сохранить в `windows`;
  - `show(user, screen)` — отредактировать окно; если сообщение не найдено — отправить новое и сохранить id;
  - `collapse(user)` — заменить текст строкой «<Имя> — окно закрыто. /play», убрать кнопки;
  - фоновая задача раз в минуту сворачивает окна, неактивные дольше `ui.window_idle_minutes` (10).
- `cleanup.py`: `schedule_delete(chat_id, message_id, delay_s, kind)` пишет в `cleanup_queue`. Задача каждые 10 секунд удаляет просроченные. При старте бота подхватывает оставшиеся записи.
- `callbacks.py`: формат callback data `<screen>:<action>[:<arg>]`, не длиннее 64 байт. Владельца окна определяет `windows` по `message_id` нажатого сообщения: если нажал не владелец — `answer_callback_query("Это не твоё окно, чумба", show_alert=False)`. Если `message_id` не совпадает с текущим окном игрока — свернуть это сообщение и ответить «Окно устарело, /play».
- Ввод текста: если у окна `awaiting_text` не пустой, следующее текстовое сообщение этого игрока в группе передаётся обработчику ожидания, удаляется, окно обновляется. Кнопка «Отмена» сбрасывает ожидание.
- `middlewares.py`: только группа из `world_state` (иначе ответ «Игра идёт в группе» и удаление через 60 с); удаление команд игрока сразу после обработки; замок на пользователя на время обработки callback; перехват исключений с экраном «Сбой в сети, чумба. Попробуй ещё раз» в окне и записью в лог.
- Добавить в `balance.yaml` раздел `ui`: `window_idle_minutes: 10`, `temp_message_ttl_s: 60`, `news_ttl_h: 48`, `alert_ttl_h: 24`, `max_ops_per_minute: 15`, `max_screen_chars: 3500`, `combat_log_rounds: 3`.

**3.1** `/play` (синоним `/menu`) в группе. Нет живого персонажа → создание персонажа в окне:
1. 3 роли кнопками, у каждой одна строка описания.
2. Бросок, показ листа персонажа. Кнопки: «Принять» и «Перебросить» (один переброс).
3. Имя: «Случайное имя» или «Ввести» (окно ждёт текст: 2–24 символа, без ссылок).
4. Сохранение. Окно показывает приветствие фиксера района и главное меню.

Стартовый район — по происхождению (таблица в `origins.yaml`). `/start` в группе работает как `/play`. В личке любая команда отвечает «Игра идёт в группе».
**3.2** Главное меню в окне из DESIGN.md раздел 4. Разделы, которые ещё не сделаны, показывают «Скоро» и кнопку «Назад». Экран профиля: статы, навыки, шкалы, деньги, долг, импланты (тип толерантности показывается, только если известен). Во всех экранах есть «Назад» или «Меню».
**3.3** Кнопка «Закрыть» в главном меню сворачивает окно сразу.
**3.4** Админ-команды из DESIGN.md раздел 16, плюс `/admin_setgroup` (выполняется в группе, сохраняет `group_chat_id` в `world_state`, проверяет, есть ли у бота право удалять сообщения, и предупреждает, если нет). Если в `.env` задан `GROUP_CHAT_ID`, он используется, пока не выполнен `/admin_setgroup`. Ответы админ-команд — временные сообщения. Команды, которые зависят от следующих этапов, пока отвечают «не реализовано».

## M4. Неделя, экономика, события, активности — [Sonnet]

**4.1** ОД: каждое действие списывает ОД через сервис. За каждое потраченное ОД активные импланты получают `cyber.active.recharge_per_ap` заряда. При 0 ОД доступны только профиль и «Прожить неделю».
**4.2** `engine/week.py::advance_week(state, rng, content) -> WeekReport`:
- зарплата (если есть работа), аренда, еда;
- проценты по долгу; если не хватает денег на минимальный платёж — `missed_weeks += 1`; при 2 и больше — событие коллектора в очередь;
- регенерация здоровья (+ТЕЛО, ×2 в жилье уровня «апартаменты» и выше);
- настроение сдвигается на 5 к 50;
- розыск −1, если за неделю не было криминала;
- пул удачи восполняется, глюки + (d4−1), не выше 3;
- `age_weeks += 1`, каждые 52 недели +1 год; после 70 лет — бросок смерти от старости (шанс в `balance.yaml`);
- ОД = 3 + `mod(ap_week)`;
- зарплата × (1 + `salary_pct.<карьера>` + `salary_pct.all`), здоровье + `hp_regen`, настроение + `mood_weekly`, розыск − `heat_decay`;
- заряд всех активных имплантов = 100, если `cyber.active.full_recharge_on_week`.
**4.3** Лимита недель в сутки нет (личное время, DESIGN.md 9a): `players.weeks_today` больше не читается.
**4.4** `engine/events.py`: отбор событий по условиям и весам, количество по шансам из баланса. События показываются по одному: текст, кнопки выбора → проверка → эффекты → следующий. Followups обрабатываются сервисом: бой (M5), смерть (M7), тюрьма (M9). Пока этапы не готовы — безопасные заглушки: бой = −d6 здоровья, тюрьма = только запись флага.
**4.5** Активности: тренировка (выбор навыка, +опыт, настроение −5), учёба, бар (+настроение, шанс барного события), брейнданс (+настроение, счётчик зависимости, при 5 и больше — событие зависимости), терапия (+2d6 человечности по правилам раздела 7), отдых, азарт (ставки 100/500/1 000, проверка ХЛАД с удачей). Всё по ценам из баланса.
**4.6** Имущество: жильё текущего района и переезд (залог — недельная аренда, смена района), магазин оружия и брони, экипировка, подписка Trauma Team (смена уровня, отмена).

**4.7** Ежедневки (DESIGN.md раздел 12a): `/daily` и кнопка в меню — карта Мисти (баф в `buffs` до полуночи по Киеву, моды попадают в `collect_mods`), эдди, серия, награды за серию, заморозка; задания дня и недели — выдача под путь персонажа (`paths` по статистике: бои, небоевые действия, флаг chrome_free), прогресс через шину событий, кнопка «Забрать»; магазин жетонов Afterlife; `/caliber` и `/caliber_top`; `/car`, `/race @игрок`; `/cat`, `/catfight @игрок`; `/coin` (курс обновляется в глобальном тике, покупка и продажа в окне); `/dirt` и `/dump @игрок` (проверка защиты, кулдаун, публичный пост в ленту на 24 часа); задания планировщика 12:00 и 21:00 (напоминания, упоминания тех, у кого сгорает серия), еженедельный пост «Легенды недели» в воскресенье 20:00. Ежедневка работает и без живого персонажа (жетоны, серия, калибр).
**4.8** Без хрома и обеты (DESIGN.md раздел 7a): флаг `chrome_free` (снимается при первой установке импланта навсегда для жизни), бонусы из `balance.chrome_free` через `collect_mods`, быстрые взломы по чистым — только `environmental` и со штрафом; обеты: принять, серия в неделях, бонусы по таблице, нарушение через шину событий (`violence`, `cyberware`, `braindance`, `crime`, `lies`) → эффекты нарушения; активности «Медитация», «Боевые искусства», «Служение общине».
**4.10** Роли из стиля игры: сродство растёт от событий шины по весам `roles.yaml`, остывает в конце недели на `decay_pct`, ранги и перки пересчитываются, перки идут в `collect_mods`; профиль показывает основную и вторую роль с рангами; уведомление в окне «Город начинает звать тебя Нетраннером» при смене основной роли.
**4.9** Три репутации: `status` и `fame` — эффекты событий, карьера (повышения дают статус), сцена и медиа дают славу; потолки из баланса; `/legends` с переключателем «Улица / Статус / Слава» (временное сообщение) и кнопкой в профиле.

## M5. Заказы и бой — [Sonnet]

**5.1** `engine/gigs.py::generate_board(district, day, rng, content) -> list[Gig]`: 3–5 заказов, уровни взвешены по средней репутации игроков района (если игроков нет — уровни 1–2). Цель — банда или корпорация из района, оплата по диапазону уровня. Доска создаётся в тике и при первом заходе, если её нет.
**5.2** Экран фиксера: доступные заказы плюс один закрытый (виден уровень и «Нужно больше репутации»). Брифинг — через narrator (`gig_brief`), кэшируется в `gigs.brief_text`.
**5.3** Подход: 4 кнопки (громко, тихо, договориться, взлом). Кнопка «Своё действие» появляется только при `LLM_ENABLED` и после M8 и переводит окно в ожидание текста.
**5.4** Разрешение заказа: проверки по шаблону, СЛ по уровню из баланса. После проваленной проверки игроку предлагается «Удача +N» (только если не хватило не больше пула) или «Глюк: переброс». Затем `outcome_tier`.
**5.5** Эффекты по исходу из `balance.yaml`: оплата 100/60/0/0%, урон, розыск, репутация, отношение фиксера. Отношение фиксера меняет оплату на ±10%. Если подход «громко» и исход хуже «чисто» — запускается бой с опасностью = уровень заказа.
**5.5a** `engine/combat.py::build_pack(faction, danger, rng, content)` — пачка из `enemies.yaml` по DESIGN.md «Противники»: лидер на уровне опасности, остальные ниже, количество по `combat.enemy_count_by_danger`. `Combatant` (dataclass) для игрока и врагов: hp, armor, атака/защита, оружие, импланты с зарядом, RAM, взломы, моды, временные эффекты (оглушение, штраф, отключённые импланты), флаги ai.
**5.5b** `engine/enemy_ai.py` — выбор действия врага по профилю `ai` и порогам `balance.enemy_ai`: включение/выключение активных имплантов по тем же правилам, атака, взлом, бегство. Чистая функция `(combat_state, enemy_id, rng) -> Action`.
**5.6** `engine/combat.py` — бой тиками по DESIGN.md раздел 6 «Бой (тиками)» и раздел 8 «Активные импланты». Весь бой идёт в окне: каждый тик редактирует его, видны последние 3 тика, более ранние сжаты в строку «Тики 1–N: нанёс X, получил Y».
- состояние боя (`CombatState`, dataclass): враги поштучно (hp, danger, alive, fled), тик, активные импланты (key → осталось тиков), заряды, бонус следующей атаки, счётчики убийств и обезвреживаний; хранится в `windows.screen_state`;
- пачка: количество по `combat.enemy_count_by_danger`, здоровье каждого = `enemy_hp_base + enemy_hp_per_danger × опасность`; СЛ попадания = 11 + 2 × опасность; атака врага 8 + 2 × опасность + d10 против защиты игрока 10 + РЕФЛ (+2 при «Укрыться»);
- тик игрока: кнопки «Стрелять», «Ближний бой», «Нелетально» (переключатель), «Укрыться», «Бежать» (РЕФЛ + Скрытность против 13 + опасность), «Сдаться»; выбор цели, если врагов больше одного; бесплатные кнопки активных имплантов (`active.button`), доступные при заряде ≥ `min_charge`; несколько имплантов можно включить в одном тике;
- длительный режим: включение доступно при заряде ≥ `min_charge`; пока работает, каждый тик списывает `ceil(100 / duration_ticks)` заряда (эффект действует в тике, где списан заряд); при нуле выключается; если `can_cancel`, в окне кнопка «Выкл <button>», остаток заряда сохраняется; `extra_actions` дают дополнительные атаки в тике; подзарядка за тик не идёт, пока работает;
- реактивный режим: когда атака врага попала, заряд ≥ `min_charge` и авто включено (при `trigger_toggle` переключатель в окне, по умолчанию вкл) — атака становится промахом, списывается `cost`, бонус `next_attack_bonus` к следующей атаке игрока; в окне строка «Kerenzikov: уклонение»;
- выстрел: игрок выбирает число ракет (1..`targets`), нужно `cost` × число ≤ заряд и заряд ≥ `min_charge`; урон `damage` каждой цели без проверки попадания;
- конец тика: подзарядка `recharge_per_tick` всем неработающим имплантам (кламп 100); за каждое убийство `recharge_on_kill`, за обезвреживание `recharge_on_takedown` — всем имплантам с этими полями, включая работающие (так работа продлевается); проверка морали (`morale_break_fraction`, `morale_flee_chance`);
- перегрузка: каждое включение при человечности ≤ `cyber.active.overload_humanity_threshold` списывает `overload_humanity_cost` через `apply_effects`;
- выходы без боя (DESIGN.md раздел 6 «Выходы без боя»), до боя и в любой тик: «Заплатить» (выбор суммы: базовая × `bribe_cost_mult`, ×1.5, ×2), «Договориться», «Бежать», «Сдаться» — проверки против СЛ из `disposition` фракции с модификаторами ситуации из `balance.exits`; провалы по DESIGN.md; сдача — бросок по весам `surrender` фракции, исходы через `apply_effects`; `faction_rep` меняется от исхода; урон уменьшается на броню и `damage_reduction`; не больше `combat.max_rounds` тиков, затем подкрепление: остаются только «Бежать» и «Сдаться»;
- здоровье 0 → `death.resolve_zero_hp` (до M7 заглушка: выжил с 1 ХП);
- быстрые взломы: кнопка «Взлом» при наличии деки, выбор программы и цели, проверка ИНТ + Нетраннинг + `hack_bonus` против `dv_base + dv_per_danger × опасность` (+ `ice` цели), RAM списывается всегда; эффекты по DESIGN.md; RAM восстанавливается `ram_recharge` за тик (+`ram_recharge` мод);
- враги действуют через `enemy_ai`, их импланты и взломы идут тем же кодом; реактивный Kerenzikov врага уводит от атак игрока;
- `enemy_scan`: в окне видны импланты врагов и заряд; без него — «хром не опознан» и только состояние «работает Сандевистан» по факту включения;
- итог боя: убийства, обезвреживания, заряды записываются в `cyberware.charge`.
**5.7** Запись в `gig_runs` и `event_log` (публичная, если уровень 3+ или исход «катастрофа»), текст исхода — narrator `gig_result`.

## M6. Импланты и человечность — [Sonnet]

**6.1** Меню риппера: рипперы района плюс Виктор, если игрок в Уотсоне. Каталог фильтруется по деньгам, цена с модификатором риппера и `ripper_discount_pct`. Карточка импланта в окне показывает моды человеческим языком (таблица подписей мод-ключей в `bot/texts.py`) и для активных — режим, порог, длительность, подзарядку. Ограничений по слотам нет (DESIGN.md раздел 8): скрыть уже установленные ключи, проверить `requires` (дека требует нейропорт). Вещь из той же `exclusive_group` (оптика, кибердека) показывается как «Замена»: старая снимается по правилам 6.3, новая ставится в одной операции. Активные импланты работают на заряде (DESIGN.md раздел 8), новый имплант ставится с зарядом 100.
**6.1a** Магазин «Софт» (у фиксера-нетраннера Voodoo Boys в Пасифике и у рипперов с флагом `sells_software`): покупка быстрых взломов в инвентарь (`kind: quickhack`). Взломы видны в профиле и в бою.
**6.2** Установка: подтверждение → оплата → бросок осложнения риппера → потеря человечности = ПЧ × множитель толерантности × (1.5 при осложнении), с округлением. ЭМП пересчитывается через `derived`. Подпольный риппер дополнительно даёт шанс флага `infection`.
**6.3** Снятие: стоит 20% цены, возвращает половину ПЧ, деньги за имплант не возвращаются.
**6.4** Скан толерантности: 500 эдди. Фингерс в 30% случаев показывает соседний тип. После скана у Виктора `tolerance_known = true`.
**6.5** Полная конверсия доступна, только если толерантность «Смэшер»: эффекты из раздела 7, все отношения с семьёй падают до 0.
**6.6** Человечность 20 и ниже, 10 и ниже — предупреждающие события. Человечность 0 → эпизод киберпсихоза (DESIGN.md раздел 7b): публичный пост, бой с NCPD и подлётом MaxTac через 2 тика, выходы (прорыв и побег, сдача по нраву MaxTac, смерть с причиной `cyberpsychosis`).
**6.6a** Исходы эпизода: флаг `fugitive_psycho` (розыск 5 без спада, охота MaxTac раз в `balance.cyberpsychosis.hunt_weeks`, закрыты карьера, жильё, рипперы кроме подпольных; кнопка «Сдаться MaxTac»); при поимке — бросок вербовки по `balance.cyberpsychosis.recruit` (база + тип толерантности + ранги Соло/Копа + уличная репутация − убийства гражданских); экран выбора: «Контракт MaxTac» (имплант `maxtac_inhibitor` бесплатно, карьера `maxtac` ранг 1, срок службы в неделях, человечность поднимается до порога), «Изолятор» (тюремный режим M9.5 с терапией каждую неделю и снятием части хрома), либо смерть, если вербовки нет и бросок исхода плохой.
**6.6b** Служба MaxTac: обязательные вызовы раз в `balance.cyberpsychosis.duty_weeks` недель — бой с киберпсихом (архетипы `cyberpsycho_*`, иногда беглый персонаж другого игрока как противник с его статами и хромом); отказ — разряд ингибитора (урон, человечность не ниже порога); дезертирство до конца срока — снова `fugitive_psycho`.
**6.7** `infection`: каждую неделю −d6 здоровья, пока не вылечишь (проверка Медицины или клиника за 300).

## M7. Смерть и наследие — [Sonnet]

**7.1** `engine/death.py::resolve_zero_hp(state, rng, content, encounter_penalty)` — порядок: Trauma Team (шанс по уровню, плата за эвакуацию, если не хватает — уходит в долг) → Второе сердце → спасбросок `d10 < ТЕЛО − штраф` → предложение глюка, если он ещё не тратился на смерть в этой жизни → смерть.
**7.2** `services/death.py::kill(character, cause)`: персонаж мёртв, запись в кладбище, `lineages.lives_count += 1`, публичная запись, некролог через narrator (`obituary`) в ленту группы. В окне игрока — экран наследия.
**7.3** `engine/legacy.py` — варианты наследника по таблице DESIGN.md раздел 13:
- ребёнок или родственник: возраст = max(16, текущий), генерация через `character_gen` с фиксированной фамилией и унаследованными отношениями;
- протеже;
- энграмма (флаг `secure_your_soul`, услуга Arasaka в разделе «Имущество»);
- с нуля с бонусом глюков рода.

Перенос денег, жилья, долга, репутации и вражды с бандами — по процентам из баланса.
**7.4** `/lineage` и `/graveyard` в группе, ответ временным сообщением. Род также виден в окне: Профиль → «Род».
**7.5** Беглый киберпсих другого игрока может стать целью вызова MaxTac: бой против его `Combatant` (статы и хром из БД, поведение `tactical`); если его убили на этом вызове — смерть с причиной `maxtac`, некролог упоминает охотника.
**7.6** Все причины смерти — enum: combat, cyberpsychosis, old_age, collector, prison, overdose, implant_failure, maxtac. Каждая подключается в соответствующем месте.

## M8. Claude — [Sonnet]

**8.1** `narrator/client.py`: `AsyncAnthropic`, модели из конфига, тайм-аут 30 с, один повтор, `max_tokens` по типу запроса.
**8.2** `narrator/prompts.py`:
- системный промпт = лор-блок (районы, банды, фиксеры, корпорации, сленг — собирается из контента) + правила стиля из DESIGN.md раздел 14. Лор-блок помечается `cache_control: {"type": "ephemeral"}`;
- сообщение пользователя = инструкция типа + JSON payload;
- типы: `gig_brief`, `gig_result`, `event_text`, `event_outcome`, `obituary`, `news`, `npc_chat`, `classify_action`, `unique_event`.

В инструкции каждого типа прямо сказано: описывай только то, что в payload, не придумывай чисел и последствий, лимит длины.
**8.3** `narrator/budget.py`: запись каждого вызова в `llm_usage` (включая токены кэша), стоимость по ценам из `.env`. Месячная сумма. При 90% и выше — режим заготовок и одно сообщение админам. `/admin_budget` показывает расход за месяц по типам.
**8.4** `classify_action` (модель FAST): выход строго JSON `{"approach": "loud|quiet|talk|hack", "skill": "<skill_key>", "summary": "<до 80 символов>"}`. Если невалидно — просим игрока выбрать подход кнопкой. Свободное действие заменяет навык первой проверки заказа на выбранный.
**8.5** `unique_event` (модель NARRATIVE): при «Прожить неделю» шанс 30% на игрока, не чаще раза в неделю. JSON по схеме формата `events.yaml`, эффекты проверяются коридорами из баланса (деньги ±20% текущих, здоровье ±15, человечность −5..+5, без `death`). Если невалидно — обычное событие из YAML.
**8.6** `npc_chat`: Отношения → NPC → «Поговорить». Окно ждёт текст, реплика игрока удаляется из чата, в окне видны последние 2 обмена репликами. До 6 сообщений игрока, контекст: карточка NPC, сводка персонажа, последние реплики. В v1 разговор не меняет числа.
**8.7** Сводка рода для контекста (`legacy_note`): 1–2 строки о прошлых жизнях. Передаётся в `gig_brief`, `npc_chat`, `obituary`.
**8.8** Включить кнопку «Своё действие» (M5.3).

**8.9** История мира: `news` получает в контекст эпоху (`eras.brief`), недавние записи `world_history` и `history_digest`; `history_digest` — сжатая история мира до 1 500 знаков, пересобирается Claude раз в 52 недели мира и после каждого канонного события; тип `future_beat` (модель NARRATIVE): когда канонные события закончились, раз в `world.future_beat_weeks` недель Claude генерирует событие по JSON-схеме `timeline.yaml` (эффекты только из меню, коридоры), код валидирует; невалидно — повтор через сутки; запись `kind: generated`, пост в ленту; админ может отменить командой `/admin_veto`. Легенды игроков (громкие смерти, династии, заказы 5-го уровня) попадают в `world_history` как `local` и в digest.

## M9. Отношения, карьера, криминал, тюрьма — [Sonnet]

**9.0** `effects.add_npc` перевести на `roll_first_name`/`surname_for` из character_gen (род, согласование фамилии, уникальность имён в семье), как сделано в M2.7.
**9.1** Меню отношений: список NPC по группам, действия из DESIGN.md раздел 10 (по 1 ОД). Эффекты на отношение и настроение — из баланса.
**9.2** Партнёр: знакомство через бар или события, совместная жизнь (аренда делится). Ребёнок: при совместной жизни 2% в неделю на событие с выбором. Протеже: в Активностях, нужна репутация 10+.
**9.3** Карьеры: устроиться (требования к статам из `careers.yaml`), зарплата в неделю, «Работать усерднее» (+производительность), повышение после минимального стажа через проверку, увольнение через события.
**9.4** Криминал: 4 действия, проверки, деньги, розыск. При «Прожить неделю» с розыском 3 и выше — шанс облавы = 20% × (розыск − 2). Розыск 5 → охота MaxTac: бой с опасностью 5 или сдаться (тюрьма 26–52 недели).
**9.5** Тюрьма: урезанное меню (тренировка, драка — бой с опасностью 2, связи, побег — РЕФЛ + Скрытность против 21; при провале +12 недель и урон). Одно нажатие «Прожить неделю» = 4 недели. Лимит недель в сутки считается по нажатиям.

## M10. Общая беседа и глобальный тик — [Sonnet]

**10.1** Команды в группе: `/who` (живые персонажи: имя, роль, район, возраст), `/legends` (топ-5 по репутации, включая мёртвых), `/graveyard` (последние 10). Ответы — временные сообщения (удаляются через `ui.temp_message_ttl_s`).
**10.1a** Все публикации ленты ставятся в `cleanup_queue`: новости через `ui.news_ttl_h`, киберпсих, MaxTac и аресты через `ui.alert_ttl_h`. Некрологи не удаляются.
**10.2** `scheduler/tick.py`, каждый день в 03:00 по Киеву:
- собрать публичные записи `event_log` с прошлого тика;
- сгенерировать новости (narrator `news`, до 1 500 символов) и отправить в группу, отметить записи как опубликованные;
- жара районов: −1 за сутки, + по записям заказов;
- война банд: 10% в сутки на случайный район, длится 3–7 дней;
- (доски заказов личные и тиком не делаются: см. «Личное время»).

Если событий за сутки не было — короткая сводка «тихая ночь» из заготовок.
**10.3** Мгновенные публикации: некролог, киберпсих, охота MaxTac, арест.
**10.4** Ничего из личного (деньги, импланты, долг) не публикуется, кроме того, что прямо попало в публичную запись.

**10.5** Личное время и эпохи (DESIGN.md раздел 9a, переделка T.1-T.6): общих часов мира нет; дата рода — календарь последнего персонажа игрока (`birth_year + age_years` — год, `age_weeks` — неделя года), у нового игрока это старт `world.start_year`, наследник продолжает с даты умершего; `world_state.world_week` остался только записью для разовой миграции `services/world.py::migrate_personal_time`; при смене эпохи экран в окне и обновление цен; фильтр доступности везде через `content.available` с годом персонажа.
**10.6** Канонные события срабатывают лично, когда дата персонажа доходит до `year/week` события из `timeline.yaml`: экран в окне перед отчётом недели, короткий пост в ленту с именем персонажа (`canon_reached`), первый дошедший делает событие общим (запись в `world_history`, удар `coin_shock` один раз). Эффекты мира персонажа — свёртка всех событий (канон и сгенерированные) с датой не позже его даты в коридорах `balance.world_effects` (`services/worldfx.py::fx_at`); они учитываются в магазинах, рипперах, заказах и событиях. Будущее после канона общее по датам: первый дошедший запускает `futurebeats.ensure_future_beats`, остальные читают те же записи.
**10.7** `/admin_year [@игрок]` (личная дата игрока), `/admin_history` (последние 10 записей истории), `/admin_veto <id>` (отменить сгенерированное событие в течение `history_veto_hours`, эффекты откатываются).

## M11. Картинки

**11.1 [Haiku]** `images/manifest.yaml` по `docs/IMAGES.md`: `file`, `tags`.
**11.2 [Sonnet]** `services/images.py::pick(tags) -> file | None`: картинка с наибольшим пересечением тегов, случайная среди равных. Кэш `file_id` в `image_cache`. Картинки используются только в ленте города: новости, некрологи, киберпсих, MaxTac. Пост с картинкой — фото с подписью, если текст ≤ 1 024 символов, иначе фото без подписи и текст отдельным сообщением (оба удаляются по одному сроку). Окна игроков в v1 остаются текстовыми. Если файлов нет — всё работает без картинок.

## M12. Полировка — [Sonnet]

- `/help` с кратким описанием разделов.
- Ежедневный бэкап базы в `data/backups/` (хранить 7 последних).
- Вычитка всех пользовательских текстов на единый тон.
- `README.md`: запуск, админ-команды, как добавлять события и картинки.
- Финальная запись в `PROGRESS.md`: что сделано, что отложено, известные проблемы.
