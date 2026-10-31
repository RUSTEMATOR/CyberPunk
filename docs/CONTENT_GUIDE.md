# Руководство по контенту

Для шагов M1.2 и M1.5: как заполнять YAML в `content/`. Схемы — `app/content/schemas.py`, проверка — `python -m app.content.loader`.
Любая опечатка в имени поля, ссылка на несуществующий ключ или неверное значение роняют загрузчик и показывают файл и запись.

## Общие правила

Ключи
- Ключ записи (`key`, у событий `id`) — латиница, snake_case, уникален в своём файле: `watson`, `tyger_claws`, `mantis_blades`.
- Русское отображаемое имя — в поле `name`. Ссылки между файлами идут только по ключам.
- Статы: `BODY REF TECH INT COOL EMP LUCK`. Навыки: `firearms melee stealth netrunning tech medicine persuasion driving`.
- Подходы: `loud quiet talk hack`. Исходы: `clean complication fail disaster`.
- Типы кибертолерантности: `fragile normal resistant ironclad smasher`.
- Типы заказов: `delivery theft elimination hack escort cyberpsycho missing`.
- Причины смерти: `combat cyberpsychosis old_age collector prison overdose implant_failure maxtac`.
- Слоты имплантов: `brain spine eyes skin face circulatory legs forearms hands body` — только место на теле, ограничений нет. Конфликт только внутри `exclusive_group` (список в `CONTENT_KEYS.md`).
- Уровни имплантов и взломов: `street mid military`. Категории имплантов: `combat noncombat booster netrun defense`. Поведение врагов: `brute tactical netrunner coward`.
- Все числовые бонусы имплантов, оружия, брони и черт пишутся в `mods` (раздел «cyberware.yaml»). Каталоги, которые читаются по маске: `cyberware*.yaml`, `enemies*.yaml`, `events*.yaml`.
- Пресеты и роли (1.8): `presets.yaml` (стартовые наборы) и `roles.yaml` (роли, которые растут из игры). **Пресет не роль**: в названии пресета нет слов «Соло», «Нетраннер» и других ролей. Подробно в разделах `presets.yaml` и `roles.yaml`.
- Новые файлы M1.6: `eras.yaml`, `timeline.yaml`, `vows.yaml`, `tarot.yaml`, `quests.yaml`, `afterlife_shop.yaml` (разделы в конце руководства). Каждый содержит по одной образцовой записи; остальное наполняет шаг 1.7.
- Любая запись каталога может иметь окно доступности по годам (следующий раздел). Если поля не указаны, запись доступна всегда.
- Числа баланса — только в `balance.yaml`. В записях контента остаются числа самой записи: цена, ПЧ, зарплата, СЛ события.
- В YAML строки с двоеточием, кавычками или `{}` заключать в двойные кавычки.

Стиль текстов (всё, что видит игрок)
- Русский язык. Коротко и жёстко: обычное сообщение до 600 знаков, текст события 1–3 предложения.
- Нуар и уличный сленг Найт-Сити из русской локализации 2077: чумба, эдди, нова, дельта, риппер, нетраннер, корпо, хром, брейнданс, шарды.
- Деньги — «эдди» (не «евродоллары», не «деньги» в каждом предложении). Хорошее — «нова», плохое — «дельта».
- Чёрный юмор допустим, морализаторство нет. Без эмодзи.
- Никакого «ИИ-языка»: без «в мире, где», «словно», «не просто X, а Y», без вступлений и подытоживания, без восклицательных знаков подряд, без обращений вроде «дорогой игрок».
- Текст описывает ситуацию и последствие, а не механику: никаких «вы потеряли 6 здоровья» внутри текста. Числа показывает интерфейс.
- Тексты исходов не противоречат эффектам: если `effects` содержит `hp: -6`, в тексте человек получает ранение.
- Имена и места канонные (Регина Джонс, «Посмертие», Джапантаун). Не выдумывать канонных персонажей, которых нет в DESIGN.md.
- Единые термины. Корпорации и бренды только латиницей и без падежных окончаний: Arasaka, Militech, Kang Tao, Biotechnica, Petrochem, Zetatech, Trauma Team, NetWatch, Kiroshi, Dynalar; фразу строят так, чтобы окончание не понадобилось («охрана Arasaka», «контракт с Trauma Team»). Банды, районы и организации по-русски и склоняются: Мальстрём, Тигриные когти, Валентинос, Шестая улица, Вудуисты, Шельмы, Животные, Баргест, Рейфы, Альдекальдо, Мусорщики; Уотсон, Кабуки, Вестбрук, Джапантаун, Сити-центр, Хейвуд, Санто-Доминго, Пасифика, Догтаун, Бэдлендс (в Бэдлендсе), НСША, МаксТак, «Посмертие»; NCPD латиницей. Районы с предлогом «в»: «в Уотсоне», «в Кабуки». Одно слово на понятие: эдди, уличная репутация, статус, слава, человечность, розыск, очки действий (ОД), заряд, имплант (хром — разговорно), риппер, фиксер, заказ.

## Доступность по времени (`available_from` / `available_until`)

Мир идёт сквозь эпохи (DESIGN.md раздел 9a): в 2013 нет «Апогея» и нет Догтауна, в 2077 уже нет ранних грубых имплантов. Поэтому у записей есть окно доступности по годам.

Поля (оба необязательны, оба целые годы 1900..2200):
- `available_from: <год>` — запись существует с этого года включительно;
- `available_until: <год>` — запись существует по этот год включительно;
- нет поля или `null` — предел с этой стороны отсутствует; нет обоих полей — запись доступна всегда (так сейчас устроен весь старый контент).

Где есть: `cyberware*`, `quickhacks`, `weapons`, `armor`, `districts`, `gangs`, `corps`, `fixers`, `ripperdocs`, `roles`, `origins`, `enemies*`, `events*`, `gig_templates`, `careers`. Нет в `housing`, `traits`: они живут всегда. У `debts` окно есть (для долга банде, корпорации или рипперу `creditor_ref` обязан существовать во все годы долга, жёсткая ссылка).

Примеры:
```yaml
- key: sandevistan_apogee      # Сандевистан «Апогей» есть только в эпоху 2070-х
  available_from: 2070
  ...
- key: early_cyberarm          # грубая ранняя рука: продаётся до появления нормальных моделей
  available_until: 2035
  ...
- key: moxes                   # банда образовалась и существует по 2077 включительно
  available_from: 2040
  available_until: 2077
```
`available_from` не может быть позже `available_until`: загрузчик называет запись и обе даты.

Правила:
1. **Игровая логика берёт каталоги только через `content.available(kind, year)`** (год мира), а не перебором `content.cyberware.values()`. `kind` — имя каталога: `cyberware quickhacks weapons armor districts gangs corps fixers ripperdocs roles origins enemies events gig_templates careers debts`. Метод возвращает словарь `ключ -> запись`.
2. **Ссылка не должна вести на запись, которой нет в нужные годы.** Загрузчик проверяет это и выдаёт предупреждение типа `availability` (в `--strict` это ошибка):
   - *жёсткие ссылки*, цель обязана существовать во все годы записи-источника: `start_district` и `enemy_gang` происхождения, район риппера, стартовое снаряжение и `starting_career` роли, оружие/броня/импланты/взломы врага, `requires` импланта;
   - *мягкие ссылки*, достаточно пересечения по годам (движок отфильтрует список через `available`): `district.fixer` (названный фиксер района, фиксеры 2070-х приходят в 2070), `gangs` и `corps` района, `districts` фиксера, `corps` карьеры, `factions` врага, `districts/roles/careers` в условиях события.
   Пример: роль без окна, у которой в `gear.cyberware` стоит имплант с `available_from: 2070`, даёт предупреждение. Исправление: либо дать роли окно `available_from: 2070`, либо заменить имплант на ранний.
   - *покрытие района* (M1.7b): в каждый год окна района должен быть хотя бы один фиксер, у которого район в `districts` (не легендарный и без порога репутации, то есть не Роуг), и хотя бы один риппер с этим районом или с `district: null` (подпольный). Загрузчик называет район и годы без покрытия. Поэтому ранние фиксеры и рипперы (`available_until: 2069`) живут рядом с каноническими 2070-х.
3. Если у района одна банда с окном 2040..2077, а у самого района окна нет, предупреждения не будет, пока годы пересекаются. Движок покажет банду только в её годы.
4. Ранние импланты (до появления нормальных) грубее: выше цена и потеря человечности, это задают множители эпохи (`chrome_price_mult`, `chrome_humanity_mult` в `eras.yaml`), а не отдельные поля.

## balance.yaml

Путь: `content/balance.yaml`. Один объект, 37 секций. Заполнен целиком (M1.1, M1.4, M1.6, M1.8, 1.7f), менять только по необходимости.
Секции: `time character skills luck checks gigs debt housing activities cyber trauma_team death legacy events crime jail combat healing relations careers world narrator mods_caps enemy_ai netrun chrome_free daily caliber dirt reputation world_effects roles exits car cat coin cyberpsychosis`.
Модель `BalanceModel` не имеет значений по умолчанию: каждое число обязано быть в YAML.

```yaml
gigs:
  dv_by_tier: {1: 13, 2: 15, 3: 17, 4: 19, 5: 21}   # ключи таблиц по уровням всегда 1..5
  pay_by_tier: {1: [300, 600], ...}
  outcomes:
    complication: {pay_pct: 60, hp_loss_per_tier: 3, heat: 1, street_cred: 1, fixer_rel: -5}
death:
  old_age_chance: {70: 0.03, 72: 0.06, ...}          # возраст -> шанс смерти в день рождения
```

Поля M2 (генерация персонажа и долги, числа стартовые):
```yaml
character:
  origin_hint_chance: 0.7   # случайное происхождение: подсказка пресета (origin_hint) берётся с этим шансом; у пресетов с chrome_free_start всегда
  rare_origin_weight: 0.3   # вес выпадения происхождений с chrome_free_start (остальные весят 1)
  enemy_start_rep: -30      # faction_rep с бандой из enemy_gang происхождения на старте
debt:
  principal_step: 100       # стартовая сумма долга округляется до этого шага
  overdraft_creditor: Долг по счетам   # кому должен персонаж, когда эдди уходят в минус, а долгов нет
  collector_event: debt_collector      # id события (events*.yaml), которое ставится в очередь после collector_missed_weeks пропусков
  weekly_rate: 0.0075       # проценты в неделю (0,75%), не выше 0.05
  min_payment_pct: 0.02     # минимальный платёж: доля долга...
  min_payment_floor: 100    # ...но не меньше этого (или весь остаток); списывается сам, если хватает эдди
  legacy_rate_above: 0.02   # миграция при запуске: долги со ставкой выше переводятся на weekly_rate (services/debtfix.py)
```

Новые секции M1.4:
```yaml
mods_caps:        # потолки суммы мода по всем вещам персонажа; остальные моды без потолка
  ap_week: 1
  shop_discount_pct: 50
  ripper_discount_pct: 50
  humanity_loss_pct: -50      # нижняя граница
  humanity_floor: 10          # потолок суммы мода humanity_floor (ингибитор MaxTac даёт 5)
enemy_ai:         # пороги поведения противников (DESIGN.md раздел 6)
  brute: {}
  tactical: {activate_hp_below_pct: 0.6, cancel_charge_below: 30}
  netrunner: {defense_bonus: 2}
  coward: {flee_hp_below_pct: 0.5}
netrun:           # кибердека и быстрые взломы
  base_ice: 0
  enemy_hack_dv_by_danger: {1: 9, 2: 11, 3: 13, 4: 15, 5: 17}
  ram_start_pct: 1.0
  ram_full_recharge_after_combat: true
```

Новые секции M1.6 (все числа стартовые, правятся только здесь):
```yaml
world:                      # личное время: общих часов мира нет
  start_year: 2013          # год, с которого начинается новый род
  future_beat_weeks: 26     # после канона: одно крупное событие от Claude раз в N недель
  history_veto_hours: 24    # окно, в котором админ может отменить сгенерированное событие
chrome_free:                # тело без имплантов, DESIGN.md 7a
  xp_pct: 25                # дополнительный опыт навыков, %
  check_bonus: {social: 1, heal: 1, meditation: 2}
  quickhack_penalty: 5      # штраф к броску взлома через окружение против чистого
daily:                      # DESIGN.md 12a
  base_eddies: 100
  eddies_per_streak_day: 10
  eddies_streak_cap: 100
  streak_rewards: {7: {eddies: 500, tokens: 3}, 30: {eddies: 2000, tokens: 10}}
  quest_tokens: {daily: 1, weekly: 3}   # жетоны за любое выполненное задание периода
  daily_quests: 3
  weekly_quests: 1
  freeze_max: 2
caliber:
  change_dice: 1d10-4       # изменение за сутки в мм
  start_mm: 5
  min_mm: 0
  titles: [{mm: 0, title: Холостой}, {mm: 10, title: Мелкашка}, ...]   # по возрастанию мм
dirt:
  gain_dice: 1d3
  dump_cost: 5
  target_cooldown_days: 3
  defense_check: {stat: COOL, dv: 13}
  defense_fixer_rel_min: 70
  heat_gain: 1
  post_ttl_h: 24
reputation: {street_cred_max: 50, status_max: 50, fame_max: 50}   # street_cred_max равен character.street_cred_max
world_effects:              # коридоры значений мировых эффектов (см. timeline.yaml)
  price_mult: {min: 0.5, max: 3.0}
  district_danger: {min: -2, max: 2}
  gang_war_weeks: {min: 1, max: 12}
  faction_power: {min: -3, max: 3}
  heat_mult: {min: 0.5, max: 2.0}
  gig_pay_mult: {min: 0.5, max: 2.0}
  event_weight: {min: 0.25, max: 4.0}
```

Новые секции M1.8 (все числа стартовые, правятся только здесь):
```yaml
roles:                      # роли из стиля игры, записи в roles.yaml
  max_rank: 10              # ранги 0..10; у заполненной роли ровно столько rank_thresholds
  perk_ranks: [2, 5, 8]     # перки открываются на этих рангах, у заполненной роли перк на каждом
  affinity_max: 500         # выше сродство не растёт; больше любого порога ранга
  second_role_min_rank: 1   # вторая роль показывается с этого ранга
exits:                      # выходы без боя, подробнее ниже
  bribe: {base_by_danger: {1: 150, ...}, step_pct: 0.5, dv_per_step: 1, max_steps: 4, fail_taken_chance: 0.5}
  situation: {faction_rep: {...}, street_cred: {...}, fame: {...}, status: {...}, pack_half_lost: {...}, wounded: {...}, betrayed: {...}, honest_vow: no_lies, honest_talk_bonus: 2}
  fail: {talk_attack_bonus: 2, talk_attack_ticks: 1, flee_free_attacks: 1}
  corridor: {situation_shift_min: -6, situation_shift_max: 8, dv_min: 7, dv_max: 27, talk_bonus_cap: 4}
  surrender: {chrome_strip_count: 1, captured_ransom_pct: 0.5, vanished_weeks: 8}
  maxtac: {...}             # особый нрав, формат как у disposition
  cyberpsycho: {...}
car: {start_hp: 100, tune_dice: 1d20-8, break_chance: 0.15, parts: [...], race_roll: 2d30, stakes_tokens: [0, 1, 2, 3, 5], ...}
cat: {levels: [...], sulk_after_days: 2, leave_after_days: 7, chrome: [...], fight_roll: 1d20, ...}
coin: {start_rate: 100.0, daily_volatility: 0.12, commission_pct: 0.10, shock_volatility_mult: 2.0, ...}
world_effects: {..., coin_shock: {min: 0.2, max: 3.0}}   # новая строка коридоров
```

### Выходы без боя (`exits`)

Итоговая СЛ выхода = СЛ фракции (`disposition` в `gangs.yaml` / `corps.yaml`, для MaxTac и киберпсиха блоки `exits.maxtac` и `exits.cyberpsycho`) + сдвиги ситуации. Сумма сдвигов зажимается в `corridor.situation_shift_min..max`, итог в `corridor.dv_min..dv_max`. `null` вместо СЛ значит «невозможно», никакой сдвиг этого не меняет.

| Что | Ключ | Как действует |
|---|---|---|
| Цена взятки | `bribe.base_by_danger` | эдди по опасности пачки, умножается на `disposition.bribe_cost_mult` |
| Шаг взятки | `bribe.step_pct`, `dv_per_step`, `max_steps` | каждые +50% к базовой цене снижают СЛ взятки на 1, не больше 4 шагов |
| Провал взятки | `bribe.fail_taken_chance` | деньги забирают и всё равно нападают |
| Репутация у фракции | `situation.faction_rep` | `characters.faction_rep` от `min` до `max`; каждые `per_dv` очков сдвигают СЛ взятки и разговора на 1, не больше `dv_max` |
| Уличная репутация | `situation.street_cred` | выше порога по опасности пачки — легче, ниже — труднее (взятка и разговор) |
| Слава, Статус | `situation.fame`, `status` | только разговор |
| Пачка потеряла половину | `situation.pack_half_lost` | сдвиги `bribe`, `talk`, `flee`; порог — `combat.morale_break_fraction` |
| Ранен | `situation.wounded` | тяжёлое ранение, сдвиг побега |
| Раньше кинул | `situation.betrayed` | флаг ставит движок |
| Обет «без лжи» | `situation.honest_vow`, `honest_talk_bonus` | обман запрещён, честный разговор легче |
| Провал | `fail.*` | разговор: враги +2 к атаке в первом тике; побег: бесплатная атака врагов |
| Сдача | `surrender.*` | числа для `chrome_stripped`, `captured`, `vanished`; `robbed` и `beaten` берут `combat.surrender_gang_*` |

Моды: `check.bribe` и `check.talk_exit` правят броски взятки и разговора, `flee` и `check.escape` побег.

Не менять без причины: все СЛ фракций взяты из таблицы DESIGN.md раздел 6 «Выходы без боя». Остальные числа секции (шаги, пороги, коридоры) стартовые.

### Мини-игры: `caliber`, `car`, `cat`, `coin`

Все четыре живут на игроке и переживают смерть персонажей (DESIGN.md 12a). Один раз в сутки по Киеву на игру, даты в `players.mini_last`.
- `car`: ежедневный тюнинг меняет `car_hp` на `tune_dice`, с шансом `break_chance` отваливается деталь из `parts` (ключ попадает в `players.car_parts`, лошадиные силы падают ещё на `break_hp_loss`). Гонка: счёт = `car_hp` − `missing_part_penalty` × число отвалившихся деталей + `race_roll`; побеждает больший счёт. Ставки жетонами из `stakes_tokens` (0 = на интерес), одна гонка на пару в сутки.
- `cat`: уровни в `levels` (подряд с 1, `feedings` по возрастанию, первый 0). Не кормил `sulk_after_days` — кот обижается (штраф `sulk_fight_penalty` в бою), `leave_after_days` — уходит, возврат за `return_cost_tokens`. Хром кота: `chrome` (ключ, название, бонус в бою); покупается в Afterlife эффектом `cat_item:<ключ>`. Бой: счёт = уровень × `level_power` + бонусы хрома + `fight_roll` (− штраф обиды), `fights_per_day` в сутки.
- `coin`: общий курс в `world_state` (`coin_rate`, `coin_history`). Раз в сутки курс меняется на `daily_drift` ± `daily_volatility`, зажат в `min_rate..max_rate`. Продажа в эдди берёт `commission_pct`. Мировой эффект `coin_shock: <множитель>` один раз умножает курс и на `shock_volatility_days` суток умножает волатильность на `shock_volatility_mult` (`world_state.coin_shock_until`).
- `caliber` описан выше (M1.6).

Нужно записей: 1 файл.

### Киберпсихоз и MaxTac: `cyberpsychosis` (1.7f)

DESIGN.md 7 и 7b. Все числа стартовые, правятся только здесь.
- `maxtac_arrival_tick` — на каком тике боя срыва прилетает MaxTac (2). `fugitive_heat` — розыск беглого киберпсиха, не спадает (5). `hunt_weeks` — охота MaxTac за беглым раз в N недель (4). `duty_weeks` — обязательный вызов службы раз в N недель (5). `duty_player_target_chance` — доля вызовов, где цель — беглый персонаж другого игрока (PLAN 7.5).
- `ui` (M3): окно, очередь отправки и самоочистка (DESIGN.md раздел 3). `window_idle_minutes`, `idle_sweep_interval_s`, `temp_message_ttl_s`, `news_ttl_h`, `alert_ttl_h`, `max_ops_per_minute` (не больше 20, лимит Telegram), `max_screen_chars`, `combat_log_rounds`, `cleanup_interval_s`, `cleanup_batch`, `presets_per_page` (кнопок пресетов на странице), `name_blocklist` (подстроки, которых не должно быть в введённом имени).
- `recruit` — шанс предложения контракта после поимки: `base_chance` плюс `tolerance_bonus[тип]` (все пять типов обязательны; Железный и Смэшер «почти всегда») плюс `role_rank_bonus` × лучший ранг среди `roles` (ключи из `roles.yaml`, сейчас `solo`, `lawman`) плюс `street_cred_bonus_per_point` × уличная репутация минус `civilian_kill_penalty` × убитые гражданские; результат зажимается в `min_chance..max_chance`. Счётчик убитых гражданских ведёт движок.
- `isolation_weeks` {min, max} — срок изолятора (бросок при поступлении). `isolation_humanity_per_week` — терапия, человечность в неделю. `chrome_stripped_pct` — доля имплантов, которую снимают (округление вверх, минимум один).
- `humanity_after_contract` — человечность сразу после установки ингибитора; загрузчик требует, чтобы она была не ниже `humanity_floor` любого импланта (сейчас 5). `refusal_shock_damage` — кубики разряда при отказе от приказа (здоровье падает, человечность не ниже порога).
- Срок контракта лежит не здесь, а у карьеры `maxtac` (`service_weeks`).

## presets.yaml

Путь: `content/presets.yaml`. **Стартовые наборы** (DESIGN.md раздел 5): перекос статов, пара навыков, немного снаряжения, иногда карьера. Раньше эти записи лежали в `roles.yaml` под названиями ролей. **Пресет — это не роль**: роль зарабатывается игрой (следующий раздел), поэтому в названии пресета нет слов «Соло», «Нетраннер», «Техник» и других ролей. Сейчас 12 записей: Уличный стрелок, Ребёнок Сети, Гаражный мастер, Санитар, Делец, Уличный музыкант, Медиа-стрингер, Корпоративный стажёр, Патрульный-новичок, Кочевник, Обычный житель, Послушник. Шаг 1.7c добавляет ещё 4 (всего 16; пока меньше, загрузчик печатает `fill`).

```yaml
- key: street_gunner
  name: Уличный стрелок                  # нейтральное название, без слов-ролей
  description: Умеет стрелять и не умеет ждать.   # одна строка для выбора
  stat_template: {BODY: 6, REF: 7, TECH: 3, INT: 4, COOL: 6, EMP: 4, LUCK: 4}   # все 7 статов, 2..8, основные 6-7
  skills: {firearms: 5, melee: 3, stealth: 2, driving: 2}               # 0..6, остальные = 0
  gear:
    weapons: [pistol]          # ключи weapons.yaml
    armor: light_vest          # ключ armor.yaml или null
    cyberware: []              # ключи cyberware.yaml; с chrome_free_start пусто
  starting_career: null        # ключ careers.yaml (Корпоративный стажёр -> corpo, Патрульный -> ncpd) или null
  origin_hint: null            # необязательно: ключ из origins.yaml, который генератор предпочитает; null = любое
  chrome_free_start: false     # необязательно. true = персонаж стартует с флагом chrome_free (DESIGN.md 7a), снаряжение без имплантов
  always_offered: false        # необязательно. true = не входит в случайную тройку, предлагается всегда четвёртым
  available_from: null         # необязательно, окно доступности по годам (см. раздел выше)
  available_until: null
```

Правила:
- Генерация выбирает три случайных пресета среди тех, у кого `always_offered: false`, и добавляет **Обычного жителя** (`civilian`: `always_offered: true`, все статы по 5, пустое снаряжение) четвёртым.
- **Послушник** (`novice`) — пресет с `chrome_free_start: true`, `origin_hint: monastery_novice`. `monastery_novice` остаётся и происхождением (его выбирает генерация происхождения d10, пресет только подсказывает). Числа Послушника придуманы по образцу других пресетов (выдержка и ближний бой выше нормы, ТЕХ низкий).
- Ключ пресета не совпадает с ключом роли: `road_nomad` (пресет) и `nomad` (роль).
- Ссылки на снаряжение и карьеру проверяет загрузчик, они обязаны покрывать все годы пресета (`available_*`).

## roles.yaml

Путь: `content/roles.yaml`. **Эмерджентные роли** (DESIGN.md раздел 5b). Роль не выбирается, а зарабатывается: действия персонажа публикуют события в шину, каждое событие добавляет **сродство** (`characters.role_affinity`) подходящим ролям, сродство остывает на `decay_pct` процентов в неделю, а **ранг** 0–10 считается по порогам. Основная роль — с наибольшим сродством, вторая — следующая. На рангах 2, 5 и 8 открываются **перки** (моды). Перки всех ролей работают по рангам этих ролей, так что гибрид нормален.

Нужно: **13** — Соло, Нетраннер, Техник, Медтех, Фиксер, Рокербой, Медиа, Корпорат, Коп, Кочевник, Монах, Аферист, Торговец. Сейчас полностью заполнена `solo` (образец, **копировать её форму**), остальные 12 — заготовки из `key`, `name`, `description`; на каждую загрузчик печатает `fill`. Шаг 1.7c заполняет их.

```yaml
- key: solo
  name: Соло
  description: Наёмный ствол. Стреляет первым, считает убытки потом.     # одна строка: кто это
  affinity:                      # событие шины -> очки сродства за одно событие (целое 1..20)
    gig_done: 1                  # любой заказ
    gig_done:loud: 3             # громкий заказ (короткая форма gig_done:approach=loud)
    fight_won: 2
    fight_won:min_danger=3: 4    # победа над врагом опасности 3 и выше
    takedown: 1
    crime_done:assault: 1
  decay_pct: 3                   # ПРОЦЕНТЫ (3 = 3% сродства в неделю), целое 1..50
  rank_thresholds: [6, 15, 28, 45, 66, 92, 124, 160, 205, 260]   # сродство для рангов 1..10: ровно 10 чисел по возрастанию
  perks:                         # ровно три перка на рангах 2, 5, 8 (balance.roles.perk_ranks)
    - rank: 2
      name: Боевое чутьё
      description: Чувствуешь, откуда придёт удар. Бонус ко всем боевым проверкам.
      mods: {check.combat: 1}
    - rank: 5
      name: Холодная голова
      description: Громкие заказы даются проще, а пули в тебе держатся дольше.
      mods: {check.gig_loud: 1, hp_max: 5}
    - rank: 8
      name: Профессионал
      description: Бьёшь точнее и больнее, чем положено по прайсу.
      mods: {check.combat: 1, ranged_damage: 1, melee_damage: 1}
  available_from: null           # необязательно, окно доступности по годам
  available_until: null
```

Как заполнять роль
1. **Сродство (`affinity`)**: ключи — только события шины из таблицы в разделе quests.yaml. Форма ключа: `событие`, `событие:значение`, `событие:параметр=значение`. Короткая форма `gig_done:loud` допустима, когда значение принадлежит ровно одному параметру события. `hack` есть и у подхода, и у типа заказа, поэтому пишется `gig_done:approach=hack`. Параметры-числа и ключи только в длинной форме: `fight_won:min_danger=3` (подходит при значении 3 и выше), `drive:district=badlands`. Ключ карьеры или района должен существовать. Опечатка в событии или параметре роняет загрузчик со списком допустимых.
2. **Какой ключ побеждает**: если событию подходят несколько ключей роли, считается один — самый конкретный (больше параметров), при равенстве больший вес. Поэтому `gig_done: 1` и `gig_done:loud: 3` не складываются.
3. **Веса**: целое 1..20. Ориентир темпа: у игрока 3 ОД в неделю, то есть 3–5 событий. Роль-специалист должна набирать 8–12 очков в неделю, побочное занятие 1–3. Отрицательных весов нет.
4. **Остывание и пороги**: `decay_pct` в процентах (3 = 3% в неделю). Равновесие чистого специалиста около (очки в неделю) / (decay_pct / 100), последний порог ставь на 85–90% от него, чтобы десятый ранг требовал полной специализации. Шкалу порогов бери как у `solo`. Последний порог не выше `balance.roles.affinity_max`.
5. **Перки**: ровно по одному на рангах 2, 5, 8, по возрастанию. Перк — `mods` по обычной грамматике (раздел «Мод-ключи»), не пустой. Рост по рангам: ранг 2 — один бонус +1, ранг 5 — два небольших бонуса, ранг 8 — сильнее, но не больше +1 к проверке за ключ и без ломающих баланс ключей (`ap_week`, `humanity_loss_pct`, `glitch_max`). Названия перков по DESIGN.md раздел 5b: «Боевое чутьё» (Соло), «Интерфейс» (Нетраннер), «Мастер» (Техник), «Медицина» (Медтех), «Оператор» (Фиксер), «Харизма» (Рокербой), «Доверие» (Медиа), «Команда» (Корпорат), «Подкрепление» (Коп), «Своя тачка» (Кочевник); у Монаха, Афериста и Торговца названия придумать в духе роли. Для выходов без боя есть моды `check.bribe`, `check.talk_exit` и `flee`.
6. **Какие события кормят какую роль** (DESIGN.md раздел 5b): Нетраннер — `hack`, `quickhack_used`, `gig_done:approach=hack`; Техник — `craft`; Медтех — `heal`; Фиксер — `deal:broker`, `social_time`; Рокербой — `perform`; Медиа — `report`; Корпорат — `work_hard:career=corpo`, `promotion:career=corpo`; Коп — `work_hard:career=ncpd`, `promotion:career=ncpd`; Кочевник — `drive`, `drive:district=badlands`; Монах — `meditate`, `martial_arts`, `service`, `vow_week`; Аферист — `crime_done:pickpocket`, `crime_done:car_theft`, `crime_done:store_robbery` (без `assault`); Торговец — `deal:trade`. Если событий шины не хватает, сначала их добавляют в `QUEST_GOAL_EVENTS` (`schemas.py`), в роли этого не делают.

Заготовка и полуфабрикат
- Запись только с `key`, `name`, `description` — **заготовка**: грузится, на неё печатается предупреждение `fill`. `affinity`, `decay_pct`, `rank_thresholds`, `perks` заполняются **все четыре сразу**.
- Заполнена часть полей — ошибка («role is half filled»).
- Загрузчик проверяет: ровно `balance.roles.max_rank` порогов по возрастанию; перки ровно на `balance.roles.perk_ranks`; ключи карьер и районов в `affinity`; моды перков по каталогам.
- Ссылки на роли из событий: `conditions.roles: [solo]` означает **основную** роль (с наибольшим сродством), `conditions.min_role_rank: {solo: 3}` — ранг этой роли не ниже 3 (любая роль, не обязательно основная). Ранги 1..10, роль должна быть в `roles.yaml`.

## origins.yaml

Путь: `content/origins.yaml`. Нужно: **10** (DESIGN раздел 5, таблица d10).

```yaml
- key: watson_street_kid
  name: Уличный ребёнок Уотсона
  description: ...
  start_district: watson       # ключ района, здесь стартует персонаж
  skill_bonus: {stealth: 1}    # необязательно
  start_street_cred: 0         # необязательно
  enemy_gang: null             # ключ банды, если происхождение даёт врага
  chrome_free_start: false     # необязательно. true = персонаж стартует с флагом chrome_free (DESIGN.md 7a)
  start_vow: null              # необязательно, ключ из vows.yaml: персонаж стартует с этим обетом (серия 0 недель)
  available_from: null         # необязательно, окно доступности по годам
  available_until: null
```

Происхождения пути без хрома (M1.6): `monastery_novice` (Westbrook, Джапантаун) и `preacher_commune` (Heywood), оба с `chrome_free_start: true` и `start_vow: no_chrome`. Редкость выпадения в таблице d10 в записи не закодирована: вес выбора определяет генерация персонажа (M2).

## debts.yaml

Путь: `content/debts.yaml`. Нужно: **10** (d10: Тигриные когти, риппер из Кабуки, банк, Maelstrom, мать-одиночка с пистолетом, корпорация и так далее).

```yaml
- key: tyger_claws_loan
  name: Заём у Тигриных когтей
  creditor: Тигриные когти
  creditor_kind: gang          # gang | corp | ripper | bank | family | other
  creditor_ref: tyger_claws    # ключ банды / корпорации / риппера; обязателен для gang, corp, ripper
  principal_min: 3000
  principal_max: 8000          # сумма бросается в диапазоне 3 000-8 000
  weekly_rate: null            # null = ставка из balance.debt.weekly_rate (0,75%)
  description: ...
```

## traits.yaml

Путь: `content/traits.yaml`. Нужно: **20 черт, 20 примет, 20 одержимостей**. По умолчанию только нарратив. Необязательное поле `mods` (те же ключи, что у имплантов) даёт механический эффект; пока ни у одной записи не заполнено.

```yaml
traits:      # d20
  - {key: cold_blooded, name: Хладнокровный, description: Не вздрагивает, когда стреляют рядом.}   # mods: {check.combat: 1} необязательно
marks:       # d20
  - {key: chrome_scar, name: Шрам от хромированной пластины, description: ...}
obsessions:  # d20
  - {key: debt_free, name: Выплатить всё до последнего эдди, description: ...}
```
Ключи уникальны внутри своего списка.

## names.yaml

Путь: `content/names.yaml`. Нужно: **60 имён и 60 фамилий**, смесь происхождений как в Найт-Сити (латиноамериканские, азиатские, славянские, англо, афро, смешанные). Имя несёт пол, фамилия может иметь женскую форму.

```yaml
first_names:                  # gender: m | f | n (n = унисекс, пол персонажа выпадает случайно)
  - {name: Ксения, gender: f}
  - {name: Тарас, gender: m}
  - {name: Мика, gender: n}
last_names:
  - Ли                                    # не склоняется: одна строка
  - {male: Кузнецов, female: Кузнецова}   # склоняется: обе формы
```
Правила: имена не повторяются, нужны и `m`, и `f` (сейчас 28 / 28 / 4 унисекс); формы фамилий не повторяются ни между записями, ни внутри файла. Склонять нужно славянские фамилии на -ов/-ев/-ин/-ский; украинские на -енко/-чук, корейские, японские, китайские, англо и прочие остаются строкой.

Как это использует генератор (`engine/character_gen.py`):
- Персонаж: имя из списка, пол по имени (унисекс бросает пол), фамилия в форме этого пола. Введённое игроком имя получает пол из списка, а для незнакомого имени по окончанию (-а/-я женское). Введённая вручную фамилия остаётся как есть; фамилия линии (`lineage.family_name`) приводится к полу персонажа по таблице, для незнакомой угадывается по окончанию -ов/-ев/-ёв/-ский.
- Семья: мать и отец (роль `parent`, пол в `data.gender`) и 0..`siblings_max` братьев и сестёр. Все носят фамилию семьи в форме своего пола, имена внутри семьи (с именем персонажа) не повторяются.
- Живы ли родители: возраст родителя = возраст персонажа + `parent_age_gap_min..max`; шанс живого родителя `parent_survival.base` минус `decline_per_year` за каждый год выше `decline_from_age`, не ниже `min_chance`, умноженный на `origin_mult[происхождение]` (беженец теряет чаще). Происхождения из `orphan_origins` (приютский) теряют обоих: умерли (`data.fate = dead`) или неизвестны (`unknown`, доля `orphan_unknown_chance`). Числа в `balance.yaml`, раздел `character`.

## districts.yaml

Путь: `content/districts.yaml`. Нужно: **8** (Уотсон, Вестбрук, Сити-центр, Хейвуд, Санто-Доминго, Пасифика, Догтаун, Бэдлендс), данные из DESIGN раздел 9.

```yaml
- key: watson
  name: Уотсон
  description: ...
  danger: 3                     # 1..5
  fixer: regina_jones           # основной фиксер района (ключ fixers.yaml)
  gangs: [tyger_claws, maelstrom]   # ключи банд; для Сити-центра []
  corps: [arasaka]              # ключи корпораций, присутствующих в районе
  places: [Мегабашня H10, клиника Виктора, «Посмертие»]
  heat_mult: 1.0                # Сити-центр: 2.0 (розыск растёт ×2)
  image_tags: [watson, district]
```
Для фиксера района в его `fixers.yaml` должен быть этот район в `districts`.

## fixers.yaml

Путь: `content/fixers.yaml`. Нужно: **8** (Регина Джонс, Вакако Окада, Дино Диновик, Падре, Эль Капитан, Мистер Хэндс, Дакота Смит, Роуг).

```yaml
- key: regina_jones
  name: Регина Джонс
  districts: [watson]        # Мистер Хэндс: [pacifica, dogtown]
  description: ...
  voice: сухо, по делу, без сантиментов     # подсказка голоса для брифингов
  min_street_cred: 0         # Роуг: 35
  legendary: false           # Роуг: true; Роуг показывается в Уотсоне (Afterlife), основной фиксер района остаётся Регина
```

## gangs.yaml

Путь: `content/gangs.yaml`. Нужно: **15** — Maelstrom, Тигриные когти, Moxes, Valentinos, 6th Street, Voodoo Boys, Animals, Barghest, Wraiths, кочевники Альдекальдо, мусорщики (`scavengers`, без района) и ранние Iron Sights, Bozos, Инквизиторы, Пираньи (2013-2049, чтобы в первые годы на улицах были банды). Окна и источники дат — в QUESTIONS.md, «M1.7b gangs».

```yaml
- key: tyger_claws
  name: Тигриные когти
  name_gen: Тигриных когтей     # необязательно: падежи названия для заголовков заказов ({target_gen} ...)
  name_dat: Тигриным когтям
  name_acc: Тигриных когтей     # винительный как у одушевлённых: «Ограбить Тигриных когтей»
  name_ins: Тигриными когтями
  name_pre: Тигриных когтях
  description: ...
  style: ножи и катаны, чёрная кожа, показная вежливость     # подсказка для нарратора
  disposition: {...}                                         # обязательно, см. «Нрав» ниже
```

Падежи (`name_gen`, `name_dat`, `name_acc`, `name_ins`, `name_pre`): родительный, дательный, винительный, творительный, предложный. Нужны банде, у которой русское название склоняется (Мальстрём, Шестая улица, Рейфы). Пропущенное поле равно `name`, поэтому несклоняемым названиям (Валентинос, Альдекальдо, Айрон Сайтс, Бозо) поля не нужны. У корпораций полей нет: латиница не склоняется, во всех падежах подставляется `name`.

### Нрав (`disposition`)

Обязательный блок у **каждой** банды и корпорации (DESIGN.md раздел 6 «Выходы без боя»). Это данные из дизайна, значения уже стоят в файлах по таблице; менять только вместе с таблицей в DESIGN.md. Общие числа выходов — `balance.yaml`, раздел `exits`.

```yaml
  disposition:
    bribe_dv: 15              # СЛ взятки 5..30 или null (невозможно)
    talk_dv: 17               # СЛ разговора 5..30 или null
    bribe_cost_mult: 2.0      # во сколько раз дороже базовой взятки (exits.bribe.base_by_danger)
    flee_mod: 2               # прибавка к СЛ побега: плюс = труднее, минус = легче; -5..5
    surrender:                # веса исходов сдачи, целые >= 0, хотя бы один > 0
      robbed: 1               # released robbed beaten chrome_stripped arrested captured executed vanished
    talk_bonus_if:            # необязательно: условия, при которых разговор легче
      - {districts: [westbrook], bonus: 2}     # districts, origins, roles, chrome (chromed|clean); все поля записи сразу
    bribe_data_bonus: 3       # необязательно: взятка данными вместо эдди легче на 3 (Voodoo Boys)
    strip_slots: [brain]      # необязательно: chrome_stripped снимает только эти слоты; пусто = любые
    talk_alt: fistfight       # необязательно: вместо разговора может вызвать на кулачный бой (Animals)
    talk_gate: {roles: [medtech], knows_target: true}   # необязательно: говорить можно, только если выполнено любое из условий (киберпсих)
```

Правила:
- Отрицательные веса и сумма весов 0 роняют загрузчик. Исход, которого нет в блоке `surrender`, не случится.
- `talk_bonus_if`: внутри одной записи условия соединяются «и», значения внутри списка «или». Несколько записей складываются, но не больше `exits.corridor.talk_bonus_cap`. Районы, происхождения и роли должны существовать.
- «Своих» в `talk_bonus_if` нет: близость к фракции передаёт `faction_rep` (`exits.situation.faction_rep`), а не условие в записи.
- Охрана корпораций (Arasaka, Militech, Kang Tao, Biotechnica, Petrochem, Zetatech) — одна строка таблицы: взятка 23, разговор 19, арест охраной с шансом исчезнуть. Trauma Team и NetWatch в таблице DESIGN нет, их значения придуманы (см. QUESTIONS.md, M1.8).
- Особые нравы MaxTac и киберпсиха — в `balance.yaml`, `exits.maxtac` и `exits.cyberpsycho`, того же формата.

## corps.yaml

Путь: `content/corps.yaml`. Нужно: **10** — Arasaka, Militech, Kang Tao, Biotechnica, Petrochem, Zetatech (`megacorp`), Trauma Team International (`service`), NetWatch (`service`), NCPD и MaxTac (`law`).

```yaml
- key: arasaka
  name: Arasaka
  description: ...
  kind: megacorp             # megacorp | service | law
  disposition: {...}         # обязательно, формат и значения — в разделе gangs.yaml «Нрав»
```

## ripperdocs.yaml

Путь: `content/ripperdocs.yaml`. Нужно: **3** (DESIGN раздел 8): Виктор Вектор, Фингерс, подпольный риппер.

```yaml
- key: viktor_vektor
  name: Виктор Вектор
  district: watson           # null = подпольный риппер в каждом районе
  description: ...
  price_mod: 1.0             # Фингерс 0.7, подпольный 0.5
  complication_chance: 0.0   # Фингерс 0.2, подпольный 0.35
  infection_chance: 0.0      # подпольный: больше нуля
  scan_lie_chance: 0.0       # Фингерс 0.3
  underground: false
```

## cyberware.yaml (и cyberware_*.yaml)

Путь: `content/cyberware.yaml`, плюс любые файлы `content/cyberware_<категория>.yaml`. Загрузчик читает все `cyberware*.yaml` и объединяет. Ключ уникален во всех файлах сразу, дубль в двух файлах — ошибка с обоими именами файлов. Модель одна на все файлы.
Базовый каталог (M1.4): 22 позиции. Новые имплантов пишет M1.5 по этим правилам.

### Поля

```yaml
- key: mantis_blades
  name: Клинки-богомолы
  slot: forearms             # brain spine eyes skin face circulatory legs forearms hands body: место на теле, ограничений нет
  price: 3000
  humanity_cost: 7           # ПЧ до множителя кибертолерантности
  description: ...
  tier: street               # street | mid | military, см. ниже
  category: combat           # combat | noncombat | booster | netrun | defense, см. ниже
  requires: []               # ключи имплантов, которые уже должны стоять (дека -> neural_link)
  exclusive_group: null      # группа взаимозаменяемых имплантов, см. список в CONTENT_KEYS.md
  mods: {skill.melee: 1}     # ВСЕ числовые бонусы, кроме оружия и активного режима (таблица ниже)
  weapon_damage: 3d6         # кубики оружия или не указывать
  weapon_targets: 1          # мононить: 2
  active: null               # активный имплант на заряде (Kerenzikov, Сандевистан, Берсерк, ракеты)
  deck: null                 # блок кибердеки, обязателен для exclusive_group: cyberdeck
  resurrection: false        # Второе сердце
  smasher_only: false        # Полная конверсия
  not_for_sale: false        # true = нет в каталогах рипперов (ингибитор MaxTac), см. ниже
```

Старых полей `skill_bonus`, `combat_stat_bonus`, `strength_bonus`, `flee_bonus`, `bleed_reduction`, `armor` в схеме больше нет: всё это пишется в `mods` (`skill.melee`, `check.combat`, `strength`, `flee`, `bleed`, `armor`). Любое из старых имён в YAML роняет загрузчик.

### Нет в продаже (`not_for_sale`) и файл `cyberware_special.yaml`

`not_for_sale: true` (по умолчанию `false`) значит, что имплант не попадает в каталоги рипперов: движок, собирая каталог, пропускает такие записи. Выдаются они только карьерой, событием (`cyberware_grant:<ключ>`, бесплатно, без ПЧ-проверки продажи) или как снаряжение врага. Загрузчик запрещает их в `gear.cyberware` пресетов (стартовое снаряжение): ошибка с текстом «is not_for_sale». В `enemies*.yaml` они разрешены. Позиции без полки лежат в `content/cyberware_special.yaml`; сейчас там `maxtac_inhibitor` (категория `defense`, уровень `military`, `humanity_floor: 5`, мелкие боевые бонусы и побочка `mood_weekly: -3`, `check.social: -1`; разряд при отказе от приказа задаёт `balance.cyberpsychosis.refusal_shock_damage`).

### Мод-ключи (`mods`)

`mods` — словарь `ключ: целое число`. Значение со знаком, 0 писать нельзя, предел по модулю 100. Бонусы одного ключа от всех вещей персонажа складываются. Потолки (`ap_week`, скидки, `humanity_loss_pct`) лежат в `balance.yaml`, раздел `mods_caps`. Ключ, которого нет в таблице, роняет загрузчик: он называет ключ и перечисляет допустимые.

Составные ключи пишутся через точку, без пробелов, строчными буквами, кроме статов (`stat.REF`).

| Ключ | Что делает |
|---|---|
| `stat.<СТАТ>` | +N к стату. Статы: `BODY REF TECH INT COOL EMP LUCK` (заглавными). `stat.LUCK` меняет размер пула удачи |
| `skill.<навык>` | +N к проверкам навыка. Навыки: `firearms melee stealth netrunning tech medicine persuasion driving` |
| `xp.<навык или all>` | +N% к опыту навыка (`xp.all` — всех навыков) |
| `check.<контекст>` | +N к проверкам в контексте: `combat gig_loud gig_quiet gig_talk gig_hack crime career social escape heal bribe talk_exit`. `bribe` и `talk_exit` — взятка и разговор как выход из боя (DESIGN.md раздел 6) |
| `hp_max` | +N к максимуму здоровья |
| `armor` | +N к защите (вычитается из входящего урона вместе с бронёй) |
| `damage_reduction` | входящий урон меньше на N |
| `melee_damage`, `ranged_damage` | +N к урону за попадание |
| `strength` | +N к проверкам силы |
| `flee` | +N к побегу |
| `bleed` | урон от кровотечения меньше на N |
| `hp_regen` | +N здоровья в неделю |
| `mood_weekly` | +N к настроению раз в неделю (минус допустим, это побочка) |
| `ap_week` | +N ОД в неделю. Потолок +1 в сумме, только редкие дорогие импланты |
| `salary_pct.<карьера или all>` | +N% зарплаты. Карьеры из `careers.yaml`: `corpo ncpd trauma_team media rockerboy mechanic` |
| `gig_pay_pct` | +N% оплаты заказов |
| `crime_reward_pct` | +N% добычи с криминала |
| `shop_discount_pct`, `ripper_discount_pct` | скидка N% в магазинах и у рипперов, потолок 50% в сумме |
| `relation_gain` | +N к росту отношений от позитивных действий |
| `heat_decay` | розыск спадает быстрее на N в неделю |
| `humanity_loss_pct` | изменение потери человечности от будущих имплантов, в процентах. Стабилизатор: `-20`. Потолок -50% в сумме |
| `therapy_bonus` | +N человечности за терапию |
| `humanity_floor` | нижний порог человечности: потеря не опускает её ниже N, киберпсихоза нет. Значение складывается по вещам и обрезается потолком `mods_caps.humanity_floor`. Только для ингибитора MaxTac, остальным не давать |
| `glitch_max` | +N к максимуму глюков |
| `ram_max`, `ram_recharge` | +N к RAM деки и к подзарядке RAM за тик |
| `quickhack_cost` | +N к цене быстрых взломов в RAM (минус делает взломы дешевле) |
| `quickhack_targets` | +N целей у быстрого взлома |
| `ice` | +N к защите от взломов вражеских нетраннеров |
| `enemy_scan` | если сумма больше 0, в бою видны импланты врагов и их заряд |
| `active.<группа или all>.<поле>` | правка активных имплантов (только усилители, см. ниже) |

Ключ `active`: `active.sandevistan.recharge_per_tick: 5`, `active.all.min_charge: -10`. Группа — `exclusive_group` импланта, у которого есть блок `active`, либо `all` (все активные). Допустимые группы показывает загрузчик в сообщении об ошибке и список в `CONTENT_KEYS.md`. Поле — одно из шести:

| Поле | Что правит |
|---|---|
| `min_charge` | порог включения в процентах заряда (минус облегчает включение) |
| `recharge_per_tick` | подзарядка за тик вне работы |
| `recharge_on_kill` | подзарядка за убийство |
| `recharge_on_takedown` | подзарядка за обезвреживание |
| `duration_ticks` | длительность на полном заряде, в тиках |
| `cost` | заряд за срабатывание или за цель (реактивные и ракеты) |

Примеры допустимых `mods`:
```yaml
mods: {skill.firearms: 2, skill.stealth: 1}                    # оптика Kiroshi «Сталкер»
mods: {armor: 4}                                               # подкожная броня
mods: {xp.all: 10, salary_pct.corpo: 15, check.career: 1}      # нейрокопроцессор корпората
mods: {ap_week: 1, mood_weekly: -3}                            # регулятор сна: сильный плюс и побочка
mods: {humanity_loss_pct: -20}                                 # нейростабилизатор
mods: {active.sandevistan.recharge_per_tick: 5}                # охладитель позвоночника (усилитель)
```

### Уровень (`tier`) и категория (`category`)

`tier` нужен врагам для подбора хрома и текстам для описаний. Выбирается по цене:
- `street` — цена меньше 3 500;
- `mid` — от 3 500 до 9 999;
- `military` — от 10 000.

`category` — раздел каталога:
- `combat` — оружие, рефлексы, броня-в-бою, оптика с бонусом к стрельбе, Kerenzikov, Сандевистан, Берсерк, ракеты;
- `defense` — броня, снижение урона, кровотечение, воскрешение, `ice`;
- `netrun` — нейропорт, кибердеки, всё про RAM и быстрые взломы;
- `noncombat` — карьера, торговля, учёба, отношения, криминал, выживание, сон, память;
- `booster` — усилитель: сам ничего не делает, меняет другие импланты через `active.*`. Правила: `mods` не пустой, блоков `active`, `deck` и `weapon_damage` нет.

### Кибердека (`deck`)

Нужен только имплантам с `exclusive_group: cyberdeck` (и запрещён остальным). Загрузчик проверяет это в обе стороны.
```yaml
  exclusive_group: cyberdeck
  requires: [neural_link]
  mods: {skill.netrunning: 2}
  deck: {ram_max: 6, ram_recharge: 1, hack_bonus: 0}
```
- `ram_max` — пул RAM деки (1..30), `ram_recharge` — подзарядка RAM за тик (0..30);
- `hack_bonus` — бонус только к броскам быстрых взломов в бою (0..10). Бонус к Нетраннингу вне боя и в заказах идёт отдельно, через `skill.netrunning`. Не дублировать одно и то же в двух местах.

### Принцип «дорогое = гибкое»

Это правило для любого импланта, не только боевых (DESIGN.md раздел 8).
- Дешёвая модель: узкий бонус и побочка. Побочка пишется в `mods` с минусом (`mood_weekly: -3`, `humanity_loss_pct: 15`) или в сильной цене по ПЧ и в описании.
- Дорогая модель: бонус шире и чище, без побочки, часто с вторым применением. Дорогая модель не просто «+3 вместо +1».
- У активных имплантов гибкость выражена параметрами: дешёвая модель включается только на полном заряде (`min_charge: 100`), не выключается раньше (`can_cancel: false`), выгорает до нуля и не подзаряжается от убийств. Дорогая включается от малого заряда, выключается досрочно (`can_cancel: true`), даёт `recharge_on_kill` и `recharge_on_takedown`.
- Запрещено ломать лесенку: более дорогая модель того же типа не может быть хуже дешёвой ни по одному параметру без причины в описании.
- Усилители (`booster`) работают как билд-блоки: один усилитель правит одно-два поля или целую группу, цена растёт с шириной (`active.all.*` дороже, чем `active.sandevistan.*`).

### Активный имплант (`active`)

Режим и поля `active`: `mode` (`duration` длительный, `reactive` реактивный, `shot` выстрел), `button` (русская подпись), `min_charge`, `duration_ticks`, `can_cancel`, `cost`, `trigger_toggle`, `recharge_per_tick`, `recharge_on_kill`, `recharge_on_takedown`, `effects` (`extra_actions check_bonus melee_bonus damage_reduction enemy_attack_penalty dodge next_attack_bonus damage targets`). Образцы — восемь моделей в `cyberware.yaml` и таблица в DESIGN.md раздел 8. Усилители правят именно эти шесть числовых полей: `min_charge recharge_per_tick recharge_on_kill recharge_on_takedown duration_ticks cost`.

## quickhacks.yaml

Путь: `content/quickhacks.yaml`. Быстрые взломы (DESIGN.md раздел 8 «Кибердека и быстрые взломы»): программы, которые покупают у фиксера-нетраннера или у риппера и тратят из RAM деки. Образцовая запись одна (M1.4), остальные 12 пишет M1.5.

```yaml
- key: short_circuit
  name: Короткое замыкание
  description: Скачок напряжения в чужой хром. Искры из имплантов, дым из глазниц.
  price: 400
  ram_cost: 2              # 1..20, тратится из RAM деки
  dv_base: 9               # 5..30
  dv_per_danger: 2         # 0..5; СЛ против цели = dv_base + dv_per_danger * опасность цели
  tier: street             # street | mid | military, по цене, как у имплантов
  effects: {damage: 2d6}
  environmental: false     # необязательно. true = взлом идёт через окружение (камеры, свет, автоматы) и работает по чистым
```

`environmental` (M1.6): против цели без хрома (флаг `chrome_free`, DESIGN.md 7a) работают только такие взломы, и с штрафом `balance.chrome_free.quickhack_penalty`. Обычный взлом, зацепившийся за имплант, по чистому не проходит. Ставить `true` стоит взломам, которым не нужен имплант цели: отвлечь, ослепить светом, оглушить звуком. Взломы, которые бьют по самому хрому (слив заряда, отключение имплантов, сбой оружия), остаются `false`. Поле можно проставить и новым, и существующим взломам (проставляет шаг 1.7).

Проверка: ИНТ + Нетраннинг + бонусы деки и `check.*` против СЛ. Взлом вражеского нетраннера против игрока бросает защита игрока (ИНТ + Нетраннинг + `ice`) против `balance.netrun.enemy_hack_dv_by_danger`.

Поля `effects` (все необязательны, но нужен хотя бы один из первых пяти):

| Поле | Значение |
|---|---|
| `damage` | кубики урона, `2d6` |
| `stun_ticks` | цель пропускает N тиков (0..5) |
| `attack_penalty` + `penalty_ticks` | штраф к атаке цели на N тиков. Только вместе: оба больше нуля или оба не указаны |
| `drain_charge` | снять N пунктов заряда с имплантов цели (1..100) |
| `disable_ticks` | выключить работающий имплант цели на N тиков (0..5) |
| `spread` | `true`: эффект переходит на соседнюю цель. Один `spread` без другого эффекта запрещён |

Принцип «дорогое = гибкое» и здесь: дешёвый взлом бьёт одним эффектом по одной цели, дорогой комбинирует (урон плюс оглушение, слив плюс отключение) или распространяется, при этом тратит больше RAM и стоит дороже. Сильные эффекты (`disable_ticks`, `drain_charge`, `spread`) только у `mid` и `military`.

## enemies.yaml (и enemies_*.yaml)

Путь: `content/enemies.yaml`, плюс любые `content/enemies_<фракция>.yaml`. Загрузчик читает все `enemies*.yaml` и объединяет, ключ уникален во всех файлах. Противник — архетип: в бою он играет по тем же правилам, что и игрок (DESIGN.md раздел 6 «Противники»): те же импланты, заряд, взломы и модификаторы.

```yaml
- key: maelstrom_ganger
  name: Мэлстромовец-берсерк
  factions: [maelstrom]      # ключи из gangs.yaml и corps.yaml, либо cyberpsycho
  danger: 2                  # 1..5
  hp: null                   # необязательно; null = 6 + 4 * опасность (balance.combat)
  attack: 12                 # бросок атаки = attack + d10 против защиты игрока; ориентир 8 + 2 * опасность
  defense: 15                # СЛ, которую бьёт игрок, чтобы попасть; ориентир 11 + 2 * опасность
  weapon: combat_knife       # ключ weapons.yaml или null (кулаки и имплант-оружие)
  armor: light_vest          # ключ armor.yaml или null
  cyberware: [berserk_zetatech, kiroshi_optics]   # ключи cyberware*.yaml
  quickhacks: []             # ключи quickhacks.yaml; нужна кибердека в cyberware
  ai: brute                  # brute | tactical | netrunner | coward
  leader: false              # может ли быть лидером пачки
```

Правила для `cyberware` врага те же, что для снаряжения роли: ключи существуют, без повторов, одна модель на `exclusive_group`, `requires` выполнены (дека требует `neural_link` в списке), `full_conversion` запрещена. Для `quickhacks` нужна кибердека в `cyberware`; архетип с `ai: netrunner` обязан иметь и деку, и хотя бы один взлом. Поля `attack` и `defense` не выходят за 5..30 и держатся около ориентиров по опасности (± 3).

### Профили поведения (`ai`)

Числа порогов лежат в `balance.yaml`, раздел `enemy_ai`.
- `brute` (громила): включает всё, как только хватает заряда, и никогда не выключает досрочно. Для силового хрома без досрочного выключения.
- `tactical` (тактик): включает длительные импланты, когда здоровье ниже 60% или у игрока уже работает свой. Выключает досрочно, если заряд ниже 30% и игрок не угрожает. Копит заряд. Ему имеет смысл давать дорогой хром с `can_cancel: true`.
- `netrunner` (нетраннер): держится сзади (защита +2), бьёт быстрыми взломами, первая цель взлома — работающий имплант игрока. Нужны дека и взломы.
- `coward` (трус): бежит при здоровье ниже 50%.

### Почерк фракций

Хром, оружие и поведение архетипа должны читаться как фракция (DESIGN.md раздел 6):
- Maelstrom: тяжёлый хром и Берсерки, ближний бой, `brute`;
- Тигриные когти: Сандевистаны и клинки (богомолы, катаны), быстрые и дерзкие, `tactical`;
- Voodoo Boys: нетраннеры, деки и быстрые взломы, `netrunner`;
- Animals: сила, руки-гориллы, Берсерк, `brute`;
- Arasaka: дорогие гибкие модели (`mid` и `military`, с `can_cancel`), `tactical`;
- Militech: военные модели, броня, ракеты;
- MaxTac (архетипы с фракцией `ncpd`, опасность 4-5): Сандевистан «Апогей» и Kerenzikov Boosted, `tactical`;
- остальные банды и Kang Tao: по описанию в `gangs.yaml` и `corps.yaml`.
Чем выше опасность, тем дороже и гибче хром: опасность 1-2 — `street`, 3-4 — `mid`, 5 — `military`.

### Правило покрытия

У каждой фракции должен быть хотя бы один архетип на каждом уровне опасности, где она встречается в заказах:
- каждая банда из `gangs.yaml`: опасность 1, 2, 3;
- `arasaka`, `militech`, `kang_tao`, `ncpd`: опасность 2, 3, 4, 5.

Пока каталог неполон, нарушение выводится предупреждением (`WARNING`), код выхода 0. `python -m app.content.loader --strict` превращает предупреждения в ошибки (код 1). Перед сдачей каталога прогнать с `--strict`. Один архетип может числиться в нескольких фракциях (`factions: [maelstrom, scavengers]`) и закрывать уровень для каждой.

## weapons.yaml

Путь: `content/weapons.yaml`. Нужно: **8**. Урон из DESIGN раздел 6: пистолет 2d6, пистолет-пулемёт 2d6 (+1 попытка), дробовик 5d6 вблизи, винтовка 5d6; добавить ближний бой (нож, бита, катана и т. п.) и четвёртую-восьмую позиции по вкусу.

```yaml
- key: pistol
  name: Пистолет
  description: ...
  damage: 2d6
  skill: firearms            # firearms | melee
  price: 300
  close_range_only: false    # дробовик: true
  extra_attempts: 0          # пистолет-пулемёт: 1
  min_street_cred: 0
  mods: {}                   # необязательно, ключи как у имплантов: {ranged_damage: 1}
```

## armor.yaml

Путь: `content/armor.yaml`. Нужно: **4** (лёгкая, средняя, тяжёлая, штурмовая; защита примерно 7 / 11 / 13 / 15 по CP RED, цены растут).

```yaml
- {key: light_vest, name: Лёгкий бронежилет, description: ..., defense: 7, price: 400}   # mods: {} необязательно, ключи как у имплантов
```

## housing.yaml

Путь: `content/housing.yaml`. Нужно: **4** (DESIGN раздел 11): капсула в Кабуки 100, комната в мегабашне H10 250, апартаменты в Джапантауне 600, лофт в Корпо-плазе 2 000. Еда 50–150 по уровню жилья. Ключ `capsule_kabuki` обязателен (стартовое жильё в `balance.character.start_housing`).

```yaml
- key: capsule_kabuki
  name: Капсула в Кабуки
  description: ...
  district: watson           # район, где жильё покупается
  tier: 1                    # 1..4; с tier 3 (апартаменты) регенерация ×2
  rent: 100                  # эдди в неделю
  food: 50                   # эдди в неделю
  min_status: 0              # статус, нужный для съёма (лофт в Корпо-плазе: 10)
```

## careers.yaml

Путь: `content/careers.yaml`. Нужно: **6**: Корпорат, NCPD, Trauma Team, Медиа (журналист N54), Рокербой, Механик/техник.

```yaml
- key: corpo
  name: Корпорат
  description: ...
  requirements: {stats: {INT: 5}, skills: {}}    # минимумы для найма
  promotion_check: {stat: INT, skill: persuasion}
  corps: [arasaka]            # возможные работодатели или null
  ranks:                      # ровно 5
    - {name: Стажёр, salary: 400, promotion_dv: 13, min_weeks: 12}
    - ...
    - {name: Директор, salary: 3000, promotion_dv: null, min_weeks: 0}   # у последней ступени promotion_dv = null
  service_weeks: null        # необязательно: срок контракта в неделях (MaxTac, 104). Раньше уйти нельзя, иначе дезертирство
  grantable_by_recruitment: false   # true = эту карьеру может выдать вербовка (balance.cyberpsychosis); нужен service_weeks
```

Требования `requirements` (все необязательны): `stats`, `skills`, `from_career: {career: <ключ>, min_rank: 1..5}` (нормальный путь: сейчас в этой карьере на этой ступени или выше), `min_humanity_lost: N` (сумма ПЧ установленных имплантов до множителя толерантности). Ключ в `from_career` проверяет загрузчик.

Карьера `maxtac` (1.7f): обычный путь — NCPD ступень 3 (Детектив) и выше плюс хром не меньше чем на 20 ПЧ, статы BODY 5 и REF 6, стрельба 3. Вербовка киберпсиха (`grantable_by_recruitment`) игнорирует все требования и кладёт персонажа на ступень 1 на `service_weeks`. Ступень 5 карьеры `ncpd` больше не называется «МаксТэк»: MaxTac — отдельная карьера, последняя ступень NCPD «Капитан участка». Поле ступени `min_humanity_lost` осталось в схеме, но им никто не пользуется.

## gig_templates.yaml

Путь: `content/gig_templates.yaml`. Нужно: **14** — по **2 шаблона на каждый из 7 типов** (`delivery theft elimination hack escort cyberpsycho missing`).

```yaml
- key: data_theft
  type: hack
  titles: ["Слить данные {target_gen}", "Шард из сейфа {target_gen}"]   # плейсхолдеры цели, см. ниже
  targets: [corp, gang]                                      # corp | gang
  approaches:                                                # все четыре, в каждом 2-3 проверки
    loud:  [{stat: REF, skill: firearms}, {stat: BODY, skill: melee}]
    quiet: [{stat: REF, skill: stealth}, {stat: INT, skill: netrunning}]
    talk:  [{stat: COOL, skill: persuasion}, {stat: COOL, skill: persuasion}]
    hack:  [{stat: INT, skill: netrunning}, {stat: TECH, skill: tech}]
  tiers: [1, 2, 3, 4, 5]     # необязательно: уровни, на которых шаблон встречается
```
СЛ проверки берётся из `balance.gigs.dv_by_tier`. `cyberpsycho` обычно уровни 3-5.

Плейсхолдеры заголовка (других нет, загрузчик проверяет): `{target}` — название цели в именительном падеже, `{target_gen}` родительный, `{target_dat}` дательный, `{target_acc}` винительный, `{target_ins}` творительный, `{target_pre}` предложный. Для банды берутся поля `name_*` из gangs.yaml (пропущенное равно `name`), для корпорации во всех падежах её `name` латиницей. Заголовок пишется естественной фразой, которая читается и с бандой, и с корпорацией: «Дрон над складом {target_gen}» даёт «Дрон над складом Мальстрёма» и «Дрон над складом Arasaka»; «Ограбить {target_acc}» даёт «Ограбить Тигриных когтей». Не писать «Действие: {target}». Подставляет `engine/gigs.py::target_forms` и `gig_title`.

## events.yaml

Путь: `content/events.yaml`. Нужно: **40** — 10 `street`, 6 `implants` (импланты и человечность), 6 `money` (долг и деньги), 6 `relationships`, 4 `ncpd`, 4 `work`, 4 `rare`.
Примеры из DESIGN раздел 12: засада Мусорщиков, дешёвый хром у подпольного риппера, звонок бывшей, родственнику нужны деньги, проверка документов NCPD, зависимость от брейнданса, сосед стал киберпсихом, хедхантер Arasaka, коллектор по долгу, приглашение в «Посмертие», драка в баре, найденный осколок с данными.

```yaml
- id: scav_ambush
  title: Засада мусорщиков
  category: street            # street implants money relationships ncpd work rare
  weight: 3                   # целое >= 1
  public: false               # true = запись уходит в городскую ленту
  faction: scavengers         # необязательно: ключ банды, корпорации или cyberpsycho. С ним бой, начатый насильственным выбором, сначала предлагает выходы без боя (взятка, разговор, побег, сдача) против нрава этой фракции; без него боя просто случается
  conditions:                 # все поля необязательны, null = без ограничения
    districts: [watson, pacifica]
    roles: null               # ключи из roles.yaml: ОСНОВНАЯ роль персонажа (с наибольшим сродством)
    min_role_rank: null       # {solo: 3}: ранг роли не ниже N (1..10); любая роль из roles.yaml
    careers: null
    min_heat: null
    max_heat: null
    max_humanity: null
    min_street_cred: null
    min_status: null          # статус не ниже N (события корпоративных дверей)
    min_fame: null            # слава не ниже N (узнают на улице, зовут на сцену)
    max_mood: null
    requires: [has_cyberware] # флаги, все должны быть истинны
    forbids: []               # флаги, все должны быть ложны
    lacks_cyberware: []       # импланты, которых у персонажа быть НЕ должно (событие, которое их продаёт или дарит)
  text: "Переулок, двое в масках и шокер. Им нужен твой хром."
  choices:                    # 2-3 варианта
    - label: Драться          # вариант с проверкой: check + success + fail
      check: {stat: BODY, skill: melee, dv: 15}     # skill можно опустить (проверка одного стата)
      success: {text: "...", effects: {street_cred: 1}}
      fail: {text: "...", effects: {start_combat: 2}}
    - label: Отдать эдди      # вариант без проверки: text + effects
      effects: {eddies: -200}
      text: "..."
      violent: false          # необязательно. true = насильственный вариант (избиение, выстрел), даже без start_combat
```
В событиях доступно окно `available_from` / `available_until` (см. «Доступность по времени»).

Флаги условий: `has_cyberware has_cyberdeck has_weapon has_armor has_debt debt_overdue has_job has_partner has_child has_protege has_family_alive has_trauma_team is_infected is_addicted is_wounded is_wanted low_mood in_jail is_fugitive_psycho`. `is_fugitive_psycho` истинен, пока у персонажа стоит флаг `fugitive_psycho` (его ставит эпизод киберпсихоза и эффект `flag_set:fugitive_psycho`). Произвольные флаги персонажа (их ставит `flag_set:<имя>`) читают условия `flags_all: [имя]` (все должны стоять) и `flags_none: [имя]` (ни один не должен стоять).

**Сюжетные цепочки** (`events_arcs.yaml`). Первый шаг — обычное случайное событие с `flags_none: [arc_<имя>]`, первый же выбор ставит `flag_set:arc_<имя>: true`, поэтому цепочка случается раз за жизнь. Следующие шаги — `forced_only: true`. Эффект `queue_event:<id>: true` приносит событие со следующей неделей; через `later` (`effects: {queue_event:<id>: true}`) — через N недель. Загрузчик проверяет, что событие существует. Финальный шаг даёт настоящую награду: имплант, вещь, контакт или выплаты.

Ключи `effects` (значения — целые числа; флаги — `true`):

| Ключ | Значение |
|---|---|
| `eddies hp mood humanity street_cred heat debt luck_pool glitches` | целое со знаком (`debt: 500` увеличивает долг) |
| `skill_xp:<skill>` | целое, опыт навыку: `skill_xp:firearms: 2` |
| `rel_npc:<tag>` | целое; тег `family partner friend contact protege child random` |
| `rel_fixer:<key>` | целое; ключ фиксера из `fixers.yaml` |
| `add_npc:<template>` | `true` или число; шаблон `partner friend contact protege child` |
| `start_combat` | опасность 1-5: `start_combat: 2` |
| `jail_weeks` | недели 1-52 |
| `infection`, `remove_random_cyberware` | `true` |
| `flag_set:<name>`, `flag_clear:<name>` | `true`, имя snake_case |
| `career_set:<карьера>` | `true`: персонаж переходит в эту карьеру на ступень 1 (прежняя карьера заменяется, стаж с нуля). Ключ проверяет загрузчик |
| `career_clear` | `true`: персонаж уходит из карьеры (увольнение, дезертирство) |
| `cyberware_grant:<имплант>` | `true`: имплант ставится бесплатно, без цены, ПЧ и проверки `not_for_sale`; уже стоящий в той же `exclusive_group` заменяется. Ключ проверяет загрузчик |
| `death:<cause>` | `true`, причина из списка выше |
| `status`, `fame` | целое со знаком: репутация Статуса и Славы (потолки в `balance.reputation`) |
| `dirt` | целое со знаком: компромат игрока на город |
| `tokens` | целое со знаком: жетоны Afterlife игрока |
| `vow_break` | `true`: нарушены все обеты, которые держит персонаж. `vow_break:<ключ обета>: true` ломает только один. Серия сгорает, срабатывают `break_effects` обета |
| `buff:<ключ карты>` | `true`: временные моды карты из `tarot.yaml` до полуночи по Киеву |
| `item_grant:<вещь>` | `true`: оружие из `weapons.yaml` или броня из `armor.yaml` бесплатно в инвентарь (надевается, если слот пуст; вторая такая же не кладётся). Ключ проверяет загрузчик |
| `employer:<корпорация>` | `true`: работодатель карьеры, заданной в тех же эффектах `career_set` (ключ из `corps.yaml`) |
| `scan_tolerance` | `true`: персонаж узнаёт настоящую кибертолерантность (честный скан) |
| `debt_grace` | `true`: пропущенные недели всех долгов списываются (кредитор согласился ждать) |
| `later` | список отложенных исходов (ниже) |
| `queue_event:<id события>` | `true`: событие придёт со следующей неделей (шаг сюжетной цепочки) |

Обещание = механика. Если текст исхода обещает имплант, вещь, контакт, работу, скидку или деньги потом, эффекты обязаны это дать: `cyberware_grant`, `item_grant`, `add_npc:contact`, `career_set`, `later`. Не можешь дать — перепиши текст, чтобы он этого не обещал. Имплант или вещь из `cyberware_grant` / `item_grant` должны существовать во все годы события (`available_from` / `available_until` события внутри окна импланта): загрузчик это проверяет. Событию, которое продаёт или дарит имплант, ставь `conditions.lacks_cyberware: [ключ]`, чтобы оно не выпало тому, у кого имплант уже стоит.

Отложенный исход (`later`): через `weeks` недель жизни персонажа (1–104) отчёт недели бросает `chance` (0..1, по умолчанию 1). Успех применяет `effects` и показывает `text`, провал — `fail_effects` и `fail_text` (обязателен при `chance` меньше 1). `hint` — строка на экране исхода: «через 4 недели: <hint>». Внутри `effects` может быть ещё один `later`: так делаются выплаты по частям, цепочка обрывается на первом провале. Бой, тюрьма, исчезновение и смерть внутри `later` запрещены. Вложение должно быть настоящей ставкой: заметный шанс сгореть и ожидание немного в плюс.

```yaml
- label: Вложиться
  effects:
    eddies: -500
    later:
      - weeks: 4
        chance: 0.7
        text: "Кафе в Кабуки: первая выручка по твоей доле."
        fail_text: "Кафе прогорело. Вложение пропало."
        hint: первая выручка кафе
        effects:
          eddies: 300
          later:
            - {weeks: 4, chance: 0.9, text: "Вторая выплата.", fail_text: "Кафе закрылось.", effects: {eddies: 300}}
  text: "Ты подписываешь бумаги и отдаёшь пятьсот."
```

Правило насилия (небоевой выход)
- Выбор считается **насильственным**, если у него есть `violent: true` или `start_combat` в любом из исходов (`effects`, `success`, `fail`). Бой, в который проваленная проверка может скатиться, тоже считается: у игрока должен быть выбор, где боя нет вовсе.
- Если в событии есть хотя бы один насильственный выбор, то должен быть и хотя бы один ненасильственный: заплатить, поговорить, убежать, сдаться, позвать помощь. Событие, где насильственны все выборы, даёт предупреждение типа `violent` и в `--strict` ошибку. Загрузчик печатает список таких `id` отдельной строкой.
- Выбор, который на деле насильственный (вырубить, ударить, выстрелить), но не запускает бой, нужно помечать `violent: true` вручную: проверить это код не может.
- В 1.6 это предупреждение: на текущих 40 событиях оно ничего не находит (нигде нет события, где все выборы насильственны), но часть насильственных выборов без `start_combat` не помечена `violent: true` (например `neighbour_cyberpsycho`, «Вырубить его»). Их проставляет шаг 1.7d.

Правила для событий
- Эффекты вариантов соразмерны: мелкий риск `hp: -3..-8`, `eddies` в сотнях; смерть (`death:`) только в редких событиях и с проверкой.
- Каждое событие читается без контекста: игрок видит только `text`, `label` и текст исхода.
- В каждом файле уникальные `id`.

## eras.yaml

Путь: `content/eras.yaml`. Эпохи мира (DESIGN.md 9a). Сейчас одна образцовая запись (`early_chrome`, 2013-2019); шаг 1.7a заменяет её полным списком от раннего хрома до 2077 и дальше.

```yaml
- key: early_chrome
  name: Ранний хром
  from_year: 2013
  to_year: 2019             # включительно
  brief: "Для Claude, до 600 знаков: что за время, что происходит в мире и городе."
  player_text: "Что читает игрок, когда эпоха начинается. Коротко, в стиле Найт-Сити."
  chrome_price_mult: 1.5    # множитель цен на импланты в эту эпоху
  chrome_humanity_mult: 1.5 # множитель потери человечности от имплантов
  news_tone: "осторожный, любопытный: новые технологии вызывают восторг и страх"   # подсказка тона для новостей
```

Правила:
- Эпохи идут без дыр и наложений: каждая следующая начинается в год после конца предыдущей (`to_year` предыдущей + 1). Дыру и наложение загрузчик называет по ключам обеих эпох и годам.
- `balance.world.start_year` (2013) должен попадать в эпохи.
- Последняя эпоха должна доходить до 2200: мир не должен выйти за последнюю эпоху. Если эпохи кончаются раньше 2077, загрузчик даёт предупреждение типа `eras`.
- `brief` не длиннее 600 знаков. Множители больше 0: ранний хром дороже и тяжелее (больше 1), зрелый рынок дешевле.
- Роли, импланты и районы привязываются к эпохам не здесь, а своими `available_from/until`.

## timeline.yaml

Путь: `content/timeline.yaml`. Канонные события мира (DESIGN.md 9a). Когда мир доходит до `year` и `week`, событие срабатывает: пост `announcement` в ленту, применение `effects`, запись в историю, `description` уходит Claude как контекст. Сейчас одна образцовая запись `sample_chrome_rush` с `canon: false`; шаг 1.7a заменяет её 25-35 реальными.

```yaml
- id: sample_chrome_rush
  year: 2014                # должен попадать в эпохи
  week: 10                  # 1..52
  title: Название события
  description: "Контекст для Claude: что произошло и как это ощущается в городе."
  announcement: "Текст поста в ленту, до 600 знаков, в стиле новостей эпохи."
  effects:                  # меню мировых эффектов, см. ниже; необязательно
    price_mult.combat: 0.9
    event_weight.implants: 1.5
  canon: true               # необязательно, по умолчанию true; false для образцов и придуманных событий
  source_note: "Откуда дата: страница вики или книга RED, с названием"   # обязательно, не пустое
```

Правила:
- `canon: true` ставится только если в `source_note` реальный источник (cyberpunk.fandom.com, книги RED). Дату, которую не удалось проверить, не придумывают: либо `canon: false` с пометкой, что она условная, либо события нет.
- `id` уникален; года вне эпох — ошибка.
- Эффекты применяются движком через `engine/world.py::apply_world_effects` с коридорами из `balance.world_effects`.

### Меню мировых эффектов (`effects`)

Только эти ключи. Опечатка или неизвестная форма роняет загрузчик, и сообщение перечисляет все допустимые формы.

| Ключ | Значение | Что делает |
|---|---|---|
| `price_mult.<цель>` | число > 0 (множитель) | цены. Цель: `combat noncombat booster netrun defense` (категории имплантов), `weapons`, `housing` |
| `district_danger.<район>` | целое со знаком, не 0 | опасность района: `district_danger.watson: 1` |
| `gang_war.<банда>` | целое >= 1 (недели) | война банды на столько недель |
| `faction_power.<фракция>` | целое со знаком, не 0 | сила банды или корпорации |
| `unlock.<ключ предмета>` | `true` | предмет становится доступен (вне его окна `available_*`) |
| `ban.<ключ предмета>` | `true` | предмет запрещён: у риппера запрещённый хром даёт розыск |
| `heat_mult` | число > 0 | множитель роста розыска |
| `gig_pay_mult` | число > 0 | множитель оплаты заказов |
| `coin_shock` | число > 0 | один раз умножает курс эдди-крипты (0.4 — обвал, 1.8 — взлёт) и на несколько суток усиливает волатильность (`balance.coin`) |
| `event_weight.<категория>` | число > 0 | вес категории событий: `street implants money relationships ncpd work rare` |
| `district_open.<район>` | `true` | район открывается |
| `district_close.<район>` | `true` | район закрывается |
| `era.<ключ эпохи>` | `true` | смена эпохи на указанную |

Проверки загрузчика:
- ключ предмета (`unlock`, `ban`) есть в `cyberware*`, `quickhacks`, `weapons` или `armor`; район, банда, фракция, эпоха есть в своих файлах;
- значение попадает в коридор `balance.world_effects` (цены 0.5..3.0, опасность района -2..2, война банды 1..12 недель, сила фракции -3..3, множители розыска и оплаты 0.5..2.0, вес событий 0.25..4.0, `coin_shock` 0.2..3.0). Если канон требует больше, расширяют коридор в балансе, а не нарушают его;
- в одном событии нельзя одновременно `unlock` и `ban` одного предмета, `district_open` и `district_close` одного района.

Примеры:
```yaml
effects: {price_mult.combat: 0.8}                                  # боевой хром дешевеет на 20%
effects: {ban.sandevistan_apogee: true, heat_mult: 1.5}            # предмет запрещён, розыск растёт быстрее
effects: {gang_war.maelstrom: 4, district_danger.watson: 1}        # война банды на 4 недели и район опаснее
effects: {district_close.dogtown: true, era.datakrash: true}       # район закрыт, начинается новая эпоха
```

## vows.yaml

Путь: `content/vows.yaml`. Обеты (DESIGN.md 7a): добровольный запрет в обмен на бонусы за серию. Сейчас одна запись `no_chrome`; шаг 1.7c добавляет ещё три («без насилия», «без брейнданса», «без лжи»).

```yaml
- key: no_chrome
  name: Обет чистого тела
  description: Ни одного импланта. Тело остаётся таким, каким его дали.
  forbids: [cyberware]       # что ломает обет: cyberware violence braindance crime lies (хотя бы одно)
  streak_bonuses:            # по возрастанию weeks; бонусы накапливаются
    - {weeks: 4, mods: {mood_weekly: 1}}
    - {weeks: 26, mods: {xp.all: 10, relation_gain: 1}}
  break_effects: {mood: -10, fame: -1}   # один раз при нарушении; ключи как в effects событий
```

Правила:
- Серия считается в неделях, пока обет держится. `mods` бонуса действуют, пока серия не меньше `weeks`. Ключи `mods` по обычной грамматике (раздел «Мод-ключи»), в них не бывает 0 и пустого словаря.
- Нарушение обета приходит от шины событий по одному из значений `forbids`; серия сгорает, срабатывают `break_effects`. Событие тоже может сломать обет эффектом `vow_break`.
- `break_effects` — те же ключи, что у вариантов событий (`mood`, `fame`, `status`, `eddies`, `flag_set:...`), не использовать `start_combat`, `death:` и `jail_weeks` (загрузчик этого не проверяет).
- Происхождение берёт обет полем `start_vow`.

## tarot.yaml

Путь: `content/tarot.yaml`. Карты Мисти: 22 Старших аркана. Раз в сутки игрок тянет карту, моды действуют до полуночи по Киеву. Сейчас одна карта `the_fool`; шаг 1.7c добавляет остальные 21.

```yaml
- key: the_fool
  name: Шут
  text: "Прыгай, чумба. Земля подождёт. Сегодня у тебя больше везения, чем здравого смысла."   # реплика Мисти
  mods: {stat.LUCK: 1, check.crime: -1}   # временные, могут быть отрицательными; по грамматике «Мод-ключи»
  eddies: 50                              # со знаком, необязательно; в записи нужны mods или eddies
  weight: 1                               # вес выпадения, целое >= 1
```

Правила: карты разные по знаку (светлые, тёмные, обоюдные); у тёмных минус не должен быть смертельным (не больше -2 к проверкам, `hp_max` и `armor` лучше не трогать); текст — один-два предложения в голосе Мисти, без эмодзи; ключ — английское имя аркана.

## quests.yaml

Путь: `content/quests.yaml`. Ежедневные и недельные задания (DESIGN.md 12a). Сейчас одна запись; шаг 1.7c доводит до 25 ежедневных и 8 недельных, поровну по путям.

```yaml
- key: daily_quiet_gig
  text: Сделай заказ тихо              # игроку, одна короткая строка
  period: daily                        # daily | weekly
  paths: [combat, noncombat]           # кому выдавать: combat noncombat chrome_free any (any не сочетается с другими)
  goal: {event: gig_done, approach: quiet}   # событие шины и параметры, см. таблицу
  target: 1                            # сколько раз событие должно случиться, >= 1
  rewards: {eddies: 150, xp: 1}        # eddies xp tokens street_cred status fame, хотя бы одна награда
```

`rewards.tokens` добавляется к базовым жетонам за выполнение (`balance.daily.quest_tokens`), поэтому обычно пишется только у редких заданий. `xp` — опыт навыка, какого именно, решает движок. Неизвестное событие или параметр роняют загрузчик, сообщение перечисляет допустимые.

### События шины для `goal`

Ежедневные задания, обеты и легенды недели слушают шину `services/game_events.py`. Только эти события, с этими параметрами (все необязательны, лишнего не писать). Это **один общий список**: те же события кормят сродство ролей (`affinity` в `roles.yaml`, формат ключей в разделе roles.yaml). Параметр-ключ (`career`, `district`) должен существовать в своём файле:

| `event` | Когда публикуется | Параметры |
|---|---|---|
| `gig_done` | закончен заказ | `approach` (`loud quiet talk hack`), `type` (тип заказа), `min_tier` (1-5), `outcome` (`clean complication fail disaster`) |
| `crime_done` | совершено преступление | `kind` (`pickpocket car_theft store_robbery assault`), `success` (true/false) |
| `fight_won` | выигран бой | `min_danger` (1-5) |
| `takedown` | противник обезврежен без убийства | нет |
| `family_time` | время с семьёй | нет |
| `social_time` | время с другом или партнёром | нет |
| `meditate` | медитация | нет |
| `martial_arts` | боевые искусства | нет |
| `service` | служение общине | нет |
| `craft` | ремонт, сборка, крафт | нет |
| `heal` | лечение: первая помощь, операция | `target` (`self other`) |
| `deal` | сделка: посредничество и связи, бизнес и перепродажа | `kind` (`broker trade`) |
| `perform` | выступление на сцене | нет |
| `report` | репортаж, съёмка | нет |
| `drive` | поездка, вождение | `district` (ключ района) |
| `hack` | взлом вне боя | нет |
| `quickhack_used` | быстрый взлом в бою | нет |
| `vow_week` | прожита неделя с удержанным обетом | нет |
| `train` | тренировка | `skill` (навык) |
| `study` | учёба | `skill` (навык) |
| `rest` | отдых | нет |
| `bar` | поход в бар | нет |
| `braindance` | брейнданс | нет |
| `therapy` | терапия | нет |
| `work_hard` | усиленная работа по карьере | `career` (ключ карьеры) |
| `promotion` | повышение по карьере | `career` (ключ карьеры) |
| `cyberware_installed` | установлен имплант | нет |
| `debt_paid` | выплачена часть долга | нет |
| `event_resolved` | закрыто событие | `category` (категория события), `nonviolent` (true/false) |
| `week_lived` | прожита неделя | `max_heat` (0-5, розыск не выше), `min_mood` (0-100, настроение не ниже) |

Примеры: «не попадись NCPD» = `{event: week_lived, max_heat: 0}` с `target: 3`; «проведи время с семьёй» = `{event: family_time}`; «помедитируй» = `{event: meditate}`; «реши событие без драки» = `{event: event_resolved, nonviolent: true}`.

Правила выдачи: путь `any` подходит всем; `chrome_free` — персонажам с флагом `chrome_free`; `combat` и `noncombat` — по статистике действий. Задание для пути `chrome_free` не должно требовать хрома (`cyberware_installed`), для `noncombat` — боя (`fight_won`, `gig_done` с `approach: loud`).

## afterlife_shop.yaml

Путь: `content/afterlife_shop.yaml`. Магазин жетонов у бармена Afterlife (DESIGN.md 12a). Жетоны нельзя купить за эдди и передать. Сейчас четыре позиции: `stim_shot` и три кошачьих (`cat_claws`, `cat_optics`, `cat_tail`, эффект `cat_item:<ключ>`); шаг 1.7c доводит до 8.

```yaml
- key: stim_shot
  name: Стим
  description: Один укол от бармена. +2 к одной проверке на твой выбор в ближайшую неделю.
  price_tokens: 3          # целое >= 1
  effect: stim             # stim | recharge_all | streak_freeze | tarot_reroll | title:<текст титула> | cat_item:<ключ хрома кота>
```

Эффекты: `stim` (+2 к одной проверке), `recharge_all` (полная зарядка имплантов), `streak_freeze` (заморозка серии, не больше `balance.daily.freeze_max`), `tarot_reroll` (переброс карты Мисти), `title:<текст>` (косметический титул, 1-40 знаков, например `title:Свой в «Посмертии»`), `cat_item:<ключ>` (хром для кота: ключ из `balance.cat.chrome`, сейчас `claws optics tail`; бонус в кошачьем бою лежит в балансе, в магазине только цена и текст). Другое значение или неизвестный хром кота роняет загрузчик со списком допустимых.

## icons.yaml

Иконки бота (`app/bot/ui/style.py`). Запись: `key` (имя в коде; `arcana.<ключ карты>` для карт Мисти), `emoji` (обычный эмодзи), `custom_emoji_id` (необязательно, только цифры). С `custom_emoji_id` текст сообщений показывает премиум-эмодзи, а `emoji` остаётся запасным; кнопки всегда показывают `emoji`. Загрузчик требует все ключи, которые использует код (`REQUIRED_ICONS`), и аркан для каждой карты `tarot.yaml`. Иконка ставится в начало строки или кнопки, по одной.

## fallback_texts.yaml

Путь: `content/fallback_texts.yaml`. Тексты на случай, когда Claude недоступен (и пока нарратор — заглушка). Нужно: **по 3 варианта** на каждый вид и на каждый исход заказа.

```yaml
gig_brief:   [ "...", ... ]              # 3 варианта
gig_result:                              # 3 варианта на КАЖДЫЙ исход
  clean:        [ ... ]
  complication: [ ... ]
  fail:         [ ... ]
  disaster:     [ ... ]
event_text:     [ ... ]
event_outcome:  [ ... ]
obituary:       [ ... ]
news:           [ ... ]
news_quiet:     [ ... ]                  # «тихая ночь», когда публичных записей нет
npc_chat:       [ ... ]
unique_event:   [ ... ]
```
Всего 12 списков, 36 текстов.

Плейсхолдеры `{в_скобках}` — только поля payload этого вида. Неизвестный плейсхолдер роняет загрузчик.

| Вид | Разрешённые поля |
|---|---|
| `gig_brief` | `name role district fixer gig_title gig_type tier pay target` |
| `gig_result` | `name fixer gig_title approach eddies hp heat street_cred` |
| `event_text` | `name district event_title` |
| `event_outcome` | `name district event_title choice_label` |
| `obituary` | `name role age cause street_cred achievement` |
| `news` | `district count names` |
| `news_quiet` | нет |
| `npc_chat` | `name npc_name npc_role` |
| `unique_event` | `name district title` |

Тексты `gig_result` описывают исход без оценки цифр: числа в `eddies hp heat street_cred` подставляются как есть (могут быть отрицательными). Заготовка не должна содержать лишних последствий.
Лимиты длины: обычный текст до 600 знаков, некролог до 900, новости до 1 500.
