# Реестр ключей контента

Единый список ключей для всех YAML-файлов. Ссылаться можно только на ключи из этого списка. Новые ключи добавляет только тот, кто владеет файлом, и только в своём файле.

- **districts**: watson, westbrook, city_center, heywood, santo_domingo, pacifica, dogtown, badlands
- **fixers**: ранние, 2013-2069, оригинальные: schetovod (watson), old_lantern (westbrook), faceless_manager (city_center), deacon (heywood), tia_marisol (santo_domingo), old_axle (badlands), ti_jean (pacifica, с 2062); канонические 2070-х (`available_from: 2070`): regina_jones (watson), wakako_okada (westbrook), dino_dinovic (city_center), padre (heywood), el_capitan (santo_domingo), mr_hands (pacifica, dogtown), dakota_smith (badlands), rogue (watson, уличная репутация 35+)
- **gangs**: ранние (2013-2049), `iron_sights` (2013-2049), `bozos` (2013-2049), `inquisitors` (2013-2049), `piranhas` (2020-2049); основные: `maelstrom` (с 2020), `tyger_claws` (с 2020), `valentinos` (с 2037), `sixth_street` (с 2026), `aldecaldos` (с 2015), `voodoo_boys` (с 2062), `moxes` (с 2067), `animals` (с 2070), `barghest` (с 2070); без окна: wraiths, scavengers
- **corps**: arasaka, militech, kang_tao, biotechnica, petrochem, zetatech, trauma_team, netwatch, ncpd
- **ripperdocs**: ранние оригинальные (до 2069): doc_hollis (watson), doc_tanaka (westbrook), gale_clinic (city_center); viktor_vektor (watson) и fingers (westbrook) с 2070; underground_pacifica с 2062, underground_dogtown с 2070; underground_watson, underground_westbrook, underground_city_center, underground_heywood, underground_santo_domingo, underground_pacifica, underground_dogtown, underground_badlands
- **presets** (12, `presets.yaml`, стартовые наборы, не роли): street_gunner (Уличный стрелок), net_kid (Ребёнок Сети), garage_mechanic (Гаражный мастер), orderly (Санитар), dealer (Делец), street_musician (Уличный музыкант), media_stringer (Медиа-стрингер), corp_intern (Корпоративный стажёр), rookie_patrolman (Патрульный-новичок), road_nomad (Кочевник), civilian (Обычный житель, `always_offered: true`, предлагается четвёртым и не входит в случайную тройку), novice (Послушник, `chrome_free_start`, `origin_hint: monastery_novice`). Шаг 1.7c добавляет ещё 4; M1.7b добавил citinet_kid (Ребёнок CitiNet, с 2030), net_kid теперь до 2022
- **roles** (13, `roles.yaml`, роли растут из стиля игры): solo (заполнена, образец), netrunner, techie, medtech, fixer, rockerboy, media, corpo, lawman, nomad, monk, hustler, trader (12 заготовок, заполняет шаг 1.7c). Ключи ролей не совпадают с ключами пресетов; `conditions.roles` в событиях называет основную роль
- **origins**: watson_street_kid, corpo_brat, badlands_nomad, ex_ganger (с 2020, враг maelstrom), ex_ganger_early (2013-2019, враг iron_sights), corp_war_veteran, refugee, megabuilding_kid, orphanage_kid, ex_con, ripper_kid, monastery_novice (westbrook, `chrome_free_start`, обет `no_chrome`), preacher_commune (heywood, `chrome_free_start`, обет `no_chrome`)
- **weapons**: pistol, heavy_pistol, smg, shotgun, assault_rifle, sniper_rifle, katana, combat_knife
- **armor**: light_vest, kevlar_jacket, medium_armorjack, heavy_armorjack
- **cyberware** (93, по файлам; окна доступности в M1.7b, ранние грубые модели в `cyberware_early.yaml`):
  - `cyberware.yaml` (22): mantis_blades, neural_link, cyberdeck_paraline, cyberdeck_netdriver, kiroshi_optics, kiroshi_stalker, subdermal_armor, biomonitor, reinforced_tendons, gorilla_arms, monowire, synthskin_face, second_heart, full_conversion, kerenzikov, kerenzikov_boosted, sandevistan_zetatech, sandevistan_dynalar, sandevistan_apogee, berserk_zetatech, berserk_militech, projectile_launcher
  - `cyberware_combat.yaml` (25): kiroshi_hunter, kiroshi_predator, militech_eagle, smartlink_gun, ballistic_coprocessor, reflex_tuner, subdermal_grip, dermal_laminate, dermal_ablative, reinforced_skeleton, blood_pump, pain_editor, lynx_paws, camo_ghost, camo_military, spinal_cooler, launcher_autoloader, tactical_processor, adrenaline_pump, takedown_coupler, phase_shifter, berserk_multiplexer, capacitor, power_core, synaptic_accelerator
  - `cyberware_netrun.yaml` (8): cyberdeck_street_garage, cyberdeck_military_harpoon, subdermal_firewall, cerberus_shield, neural_ice_military, ram_expander, quickhack_compiler, multithread_chip
  - `cyberware_noncombat.yaml` (25): sleep_regulator, memory_module, neural_stabilizer, endocrine_regulator, hidden_subdermal_stash, trade_cortex, immune_booster, pheromone_gland, corpo_coprocessor, corp_loyalty_plate, ncpd_badge, lie_detector, trauma_filter, surgeon_fingers, media_optics, neuro_journal, vocal_synth, fan_channel, mechanic_fingers, ledger_cache, calm_damper, vestibular_stab, dust_filter, gig_voice_modulator, contact_book
  - `cyberware_early.yaml` (12, ранние грубые двойники более поздних моделей, `available_until`): cyberoptic_early (optics), cyberarm_early, wolver_blades, cyberdeck_early (cyberdeck), boosted_reflexes_early, smartgun_link_early, pain_damper_early, bone_lace_early (до 2029), memory_chip_early (до 2044), lung_filter_early, pheromone_pump_early, immune_implant_early (до 2015, bioware с 2016)
  - `cyberware_special.yaml` (1, `not_for_sale`, нет в каталогах рипперов): maxtac_inhibitor (defense, military, мод `humanity_floor: 5`)
- **events_maxtac.yaml** (7, 1.7f): maxtac_duty_block_call, maxtac_duty_known_face, maxtac_duty_leash_glitch, maxtac_duty_way_out (условие `careers: [maxtac]`); psycho_fugitive_old_friend, psycho_fugitive_drone_sweep, psycho_fugitive_red_hour (условие `is_fugitive_psycho`)
- **housing**: capsule_kabuki, megabuilding_h10, japantown_apartment, corpo_plaza_loft
- **careers**: corpo, ncpd, trauma_team, media, rockerboy, mechanic, maxtac (1.7f, `service_weeks`, `grantable_by_recruitment`, нормальный путь из `ncpd` ступени 3+ с хромом на 20 ПЧ)
- **quickhacks** (12): short_circuit, distraction, reboot_optics, weapon_glitch, cripple_movement, sonic_shock, overheat, implant_drain, cyberware_malfunction, contagion, synapse_burnout, system_collapse
- **enemies**: maelstrom_ganger
- **eras**: early_chrome (2013-2019, образец; шаг 1.7a заменит списком эпох)
- **timeline**: sample_chrome_rush (образец формата, `canon: false`; шаг 1.7a заменит реальными событиями)
- **vows**: no_chrome (остальные три обета добавляет шаг 1.7c; `no_lies` обязателен: на него ссылается `balance.exits.situation.honest_vow`, пока обета нет, загрузчик печатает предупреждение `fill`)
- **tarot**: the_fool (остальные 21 карту добавляет шаг 1.7c)
- **quests**: daily_quiet_gig (остальные задания добавляет шаг 1.7c)
- **afterlife_shop**: stim_shot, cat_claws, cat_optics, cat_tail (кошачий хром, эффекты `cat_item:claws|optics|tail`; остальные позиции добавляет шаг 1.7c)

## Соглашения по ключам (M1.4)

- **Квикхаки** (`quickhacks.yaml`): английское название программы или действия в snake_case, без префикса: `short_circuit`, `reboot_optics`, `weapon_glitch`. Уровни `street`, `mid`, `military` в ключ не включаются.
- **Противники** (`enemies*.yaml`): `<фракция>_<архетип>`, фракция — ключ банды или корпорации, архетип — роль на английском: `maelstrom_ganger`, `tyger_claws_blade`, `voodoo_boys_runner`, `arasaka_enforcer`, `ncpd_maxtac_agent`. Если у фракции несколько архетипов одной роли, добавляется слово по отличию (`maelstrom_ganger_brute`), но не число. Киберпсихи: `cyberpsycho_<тип>`. Файлы по фракциям: `enemies_maelstrom.yaml`, `enemies_corps.yaml` и так далее.
- **Файлы по маске**: `cyberware_<категория>.yaml` (`cyberware_combat`, `cyberware_noncombat`, `cyberware_netrun`), `enemies_<фракция>.yaml`, `events_<тема>.yaml`. Ключи уникальны сразу во всех файлах одного вида; новый файл не создаёт новое пространство имён.
- **Фракции врага** (`factions`): любой ключ из **gangs** или **corps**, либо `cyberpsycho`. MaxTac — это `ncpd`.
- **Новые файлы M1.6** (по одному образцу, остальное наполняет 1.7). Формулы ключей:
  - `eras.yaml`: название эпохи без года, snake_case: `early_chrome`, `corporate_wars`, `datakrash`, `time_of_red`, `reconstruction`, `unification_war`, `year_2077`. Ключ не содержит годов, кроме эпохи, которая названа по году.
  - `timeline.yaml` (`id`): `<краткое_название_события>` плюс год в конце, если название неоднозначно: `arasaka_tower_bombing_2023`. Образец начинается с `sample_`, такие записи при наполнении заменяются.
  - `vows.yaml`: `no_<что запрещено>`: `no_chrome`, `no_violence`, `no_braindance`, `no_lies`. `forbids` называет запрет на языке движка (`cyberware`, `violence`, `braindance`, `crime`, `lies`), ключ обета на человеческом.
  - `tarot.yaml`: английское название аркана в snake_case с артиклем: `the_fool`, `the_magician`, `the_high_priestess`, `wheel_of_fortune`, `death`, `the_tower`. Всего 22 карты, порядок Старших арканов.
  - `quests.yaml`: `<period>_<смысл>`: `daily_quiet_gig`, `weekly_meditate_5`. Период в ключе обязателен, число цели добавляется только если оно отличает задания с одинаковой целью.
  - `afterlife_shop.yaml`: по эффекту: `stim_shot`, `recharge_all`, `streak_freeze`, `tarot_reroll`, `title_<смысл>` для титулов.
- **События шины** (`goal.event` в `quests.yaml` и ключи `affinity` в `roles.yaml`; один общий список): `gig_done crime_done fight_won takedown family_time social_time meditate martial_arts service craft heal deal perform report drive hack quickhack_used vow_week train study rest bar braindance therapy work_hard promotion cyberware_installed debt_paid event_resolved week_lived`. Новые в 1.8 (для сродства ролей): `craft` (ремонт, сборка, крафт), `heal` (параметр `target`: `self other`), `deal` (параметр `kind`: `broker` посредничество и связи, `trade` бизнес и перепродажа), `perform` (выступление), `report` (репортаж, съёмка), `drive` (параметр `district`), `hack` (взлом вне боя), `quickhack_used` (быстрый взлом в бою), `vow_week` (неделя с удержанным обетом). `work_hard` и `promotion` получили параметр `career` (ключ карьеры). Параметры каждого перечислены в `CONTENT_GUIDE.md`, раздел quests.yaml. Новое событие шины заводит сначала `QUEST_GOAL_EVENTS` в `schemas.py`.
- **Ключи для мировых эффектов** (`timeline.yaml`): категории цен `combat noncombat booster netrun defense weapons housing`; категории событий `street implants money relationships ncpd work rare`; множитель курса монеты `coin_shock` (без параметра, коридор 0.2..3.0); предметы для `unlock`/`ban` берутся из **cyberware**, **quickhacks**, **weapons**, **armor**; районы, банды, корпорации и эпохи — из списков выше.
- **Ключи эффектов 1.7f**: `career_set:<ключ карьеры>`, `cyberware_grant:<ключ импланта>` (оба со значением `true`, ключ проверяет загрузчик), `career_clear: true`. Флаг условий события `is_fugitive_psycho`. Мод `humanity_floor` (потолок суммы в `mods_caps.humanity_floor`). Поле импланта `not_for_sale`. Секция баланса `cyberpsychosis`.
- **Ключи эффектов событий M1.6**: `status`, `fame`, `dirt`, `tokens` (целые), `vow_break` (или `vow_break:<ключ обета>`), `buff:<ключ карты из tarot.yaml>`.
- **Мод-ключи** (`mods`): грамматика в `CONTENT_GUIDE.md`. Параметры берутся из этих списков: статы (`BODY REF TECH INT COOL EMP LUCK`), навыки, контексты `combat gig_loud gig_quiet gig_talk gig_hack crime career social escape heal bribe talk_exit` (`bribe` и `talk_exit` — выходы без боя: взятка и разговор, DESIGN.md раздел 6), карьеры (**careers** выше), группы (ниже).

## Группы `exclusive_group` (текущий список)

Имплант из группы заменяет другой имплант той же группы. Для `active.<группа>.<поле>` допустимы только группы, где есть активные импланты (отмечены «активные»), либо `all`.

- **cyberdeck**: cyberdeck_paraline, cyberdeck_netdriver, cyberdeck_street_garage, cyberdeck_military_harpoon (обязателен блок `deck`)
- **dermal_plating**: dermal_laminate, dermal_ablative (подкожные пластины, не активные)
- **camo** (активные): camo_ghost, camo_military (оптическая маскировка)
- **optics**: kiroshi_optics, kiroshi_stalker, kiroshi_hunter, kiroshi_predator, militech_eagle
- **kerenzikov** (активные): kerenzikov, kerenzikov_boosted
- **sandevistan** (активные): sandevistan_zetatech, sandevistan_dynalar, sandevistan_apogee
- **berserk** (активные): berserk_zetatech, berserk_militech
- **projectile_launcher** (активные): projectile_launcher

Новую группу заводит тот, кто добавляет первый имплант в неё, и сразу вписывает сюда.

## Ключи 1.8: нрав фракций, мини-игры

- **Нрав** (`disposition`): обязателен у каждой записи `gangs.yaml` и `corps.yaml`; особые нравы `maxtac` и `cyberpsycho` лежат в `balance.yaml`, раздел `exits`. Исходы сдачи: `released robbed beaten chrome_stripped arrested captured executed vanished`. Условия `talk_bonus_if`: `districts` (ключи районов), `origins` (ключи происхождений), `roles` (ключи ролей), `chrome` (`chromed` или `clean`). `strip_slots`: слоты имплантов (список в `CONTENT_GUIDE.md`). `talk_alt`: `fistfight`.
- **Разделы баланса 1.8**: `roles` (`max_rank 10`, `perk_ranks [2, 5, 8]`), `exits`, `car`, `cat`, `coin`. Ключи внутри: детали машины `car.parts` (bumper, exhaust, spoiler, headlights, mirror); хром кота `cat.chrome` (claws, optics, tail) — на них ссылается эффект магазина `cat_item:<ключ>`.
- **Ключи `world_state`** (мини-игры): `coin_rate`, `coin_history`, `coin_shock_until`; полный список в `app/db/models.py::WORLD_STATE_KEYS`.
- **Колонки БД 1.8**: `players.car_hp car_name car_parts cat coins mini_last`, `characters.preset role_affinity faction_rep`.
