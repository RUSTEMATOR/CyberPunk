# Night City Life — библиотека картинок v1

Картинки генерируются вручную в ChatGPT и кладутся в `images/` под указанными именами.
Список файлов и тегов лежит в `images/manifest.yaml` (уже собран по таблицам ниже). Файла нет на диске: запись пропускается, бот работает без картинки. Положил файл рядом с манифестом и всё: перезапуск не нужен, манифест перечитывается при изменении.

Проверка манифеста: `python -m app.services.images` (покажет ошибки, отсутствующие файлы и файлы, которых нет в манифесте).

## Что бот реально использует в v1

Картинки получают только посты ленты города в группе. Окна игроков в v1 текстовые, поэтому районы, фиксеры, заказы и бой пока не показываются (эти картинки на будущее, их можно не делать).

| Пост ленты | Тег, который обязателен | Что ещё учитывается (от важного к менее важному) | Если картинки нет |
|---|---|---|---|
| Ночные новости | `news` | | текст |
| Некролог | `death` | | текст |
| Киберпсихоз (срыв) | `cyberpsycho` | `at_<район>` (например `at_watson`) | текст |
| Изолятор после срыва | нет | `ward`, затем `cyberpsycho` | текст |
| Охота и захват МаксТак, вербовка | `maxtac` | | текст |
| Смена эпохи | нет | `era_<ключ эпохи>`, затем `era`, затем `news` | текст |
| Канонное событие мира | нет | `canon_<id события>`, затем `canon`, затем `news` | текст |
| Аресты, побеги, розыск | | | всегда текст |

Правила выбора:
- Берётся картинка с наибольшим весом совпавших тегов, при равенстве случайная (одна и та же подряд не выпадает, если есть выбор).
- Картинка с тегом `era_<ключ>`, `canon_<id>` или `at_<район>` берётся только для своей эпохи, события или района. Общая картинка (`era`, `canon`, `cyberpsycho` без `at_`) подходит всегда.
- Ключи эпох: `early_chrome`, `corporate_wars`, `datakrash`, `time_of_red`, `reconstruction`, `unification_war`, `free_city`, `year_2077`, `after_canon` (`content/eras.yaml`). Ключи районов: `watson`, `westbrook`, `city_center`, `heywood`, `santo_domingo`, `pacifica`, `dogtown`, `badlands`. Id канонных событий смотри в `content/timeline.yaml` (поле `id`).
- Подпись: текст поста (до 1 024 видимых символов, теги не считаются) идёт подписью к фото. Длиннее: фото без подписи и текст следующим сообщением. Оба удаляются в один срок.
- Telegram запоминает отправленный файл: бот хранит его `file_id` в таблице `image_cache` и повторно не загружает. Заменил файл новым: он загрузится заново сам.
- Файл не больше 10 МБ, расширения `.png`, `.jpg`, `.jpeg`, `.webp`.

Минимум для запуска, чтобы лента ожила (4 картинки): `news_studio.png`, `death.png`, `cyberpsycho.png`, `maxtac.png`. Дальше по желанию: `era_change.png`, `canon_event.png`, `psych_ward.png`, `graveyard.png` (вторая картинка для некрологов) и специальные картинки под отдельные эпохи и события.

## Общие правила
- Формат 3:2, горизонтальный, PNG или JPG, до 2 МБ (Telegram сжимает всё равно).
- Без текста на картинке, без логотипов и узнаваемых персонажей.
- Один стиль на всю библиотеку. Перед каждым промптом вставлять префикс:

> Gritty cyberpunk illustration, 2070s megacity, heavy neon against dark night, rain and haze, high contrast, cinematic composition, painterly digital art with grain, muted palette with acid yellow, magenta and cyan accents, no text, no logos.

## Районы (показываются у фиксера и при переезде)
| Файл | Теги | Промпт (после префикса) |
|---|---|---|
| district_watson.png | watson, district | Dense run-down district of stacked megabuildings and street markets, Asian signage glow, crowds under overpasses, steam from food stalls |
| district_westbrook.png | westbrook, district | Upscale nightlife quarter with lantern-lit streets, neon pleasure district alleys, glossy wet pavement, expensive cars |
| district_city_center.png | city_center, district, corp | Canyon of corporate glass towers, one massive dark tower dominating the skyline, security drones, clean empty plaza |
| district_heywood.png | heywood, district | Sunny-at-dusk Latino neighborhood with lowriders, painted murals, palm trees, a church and a cantina, warm orange neon |
| district_santo_domingo.png | santo_domingo, district | Industrial suburb of factories, power plants and cheap housing blocks, smokestacks, rusty trucks, tired workers |
| district_pacifica.png | pacifica, district | Abandoned unfinished seaside resort, half-built hotels, graffiti, makeshift camps, broken roller coaster against the sea |
| district_dogtown.png | dogtown, district | Walled-off militarized slum around a giant stadium, black market stalls, armed guards on scaffolding, searchlights |
| district_badlands.png | badlands, district | Desert outside the city at dusk, dead highways, nomad caravan of armored vehicles, distant city skyline glowing |

## Люди и места
| Файл | Теги | Промпт (после префикса) |
|---|---|---|
| fixer_generic_1.png | fixer | Middle-aged fixer in a booth of a smoky bar, chrome jaw, holographic contract floating above the table, looking at the viewer |
| fixer_generic_2.png | fixer | Fixer woman in an expensive coat on a rooftop, talking through an earpiece, city lights below |
| ripperdoc_clean.png | ripperdoc, clinic | Small clean underground clinic with a surgical chair, robotic arms, monitors, a calm doctor with optic implants |
| ripperdoc_dirty.png | ripperdoc, clinic, underground | Filthy back-alley surgery, rusty tools, flickering light, plastic sheets, unsettling surgeon with mismatched cybernetic hands |
| bar_mercs.png | bar | Legendary mercenary bar interior, crowded, warm red light, shrine of photos on the wall, tough people drinking |
| braindance.png | braindance | Person in a cheap apartment wearing a neural headset, eyes glazed, holographic memories swirling around |
| apartment_capsule.png | home, poor | Tiny capsule hotel room, sleeping pod, cables, a noodle cup, rain on a small window |
| apartment_megabuilding.png | home | Cramped apartment in a megabuilding, view of other towers, TV news on, gun on the table |
| corp_office.png | corp, career | Sterile corporate office at night, glass walls, executives in suits, holographic charts, oppressive atmosphere |

## Заказы и бой
| Файл | Теги | Промпт (после префикса) |
|---|---|---|
| gig_clean.png | gig, clean | Silhouette of a mercenary walking away from a quiet building, job done, rain, neon reflections |
| gig_complication.png | gig, complication | Mercenary running down a stairwell with a briefcase, alarms flashing red, bullet holes in the wall |
| gig_fail.png | gig, fail, disaster | Bleeding mercenary slumped against a dumpster in an alley, sirens light in the distance |
| gig_hack.png | gig, hack | Netrunner in a bathtub of ice, cables plugged into the neck, glowing data streams around |
| combat_street.png | combat | Street shootout between gang members and a lone fighter behind a car, muzzle flashes, sparks |
| combat_melee.png | combat, melee | Close-quarters fight in a neon corridor, glowing arm blades, motion blur |
| scavengers.png | scavengers, event | Masked scavenger gang in a dark tiled room, bathtub full of ice, stolen implants on a tray |

## Город и последствия
| Файл | Теги | Промпт (после префикса) |
|---|---|---|
| borg.png | borg | A towering full-body cyborg stepping out of a back-alley ripper clinic into the rain, more chrome than flesh: exposed hydraulic joints, armored plates over the torso, glowing optics behind a featureless steel face, steam rising from vents, cables still hanging from the spine, a crowd of street kids and a patrol drone staring from a distance, puddles reflecting the neon (owner request 27.10: the post «В Найт-Сити новый борг») |
| cyberpsycho.png | cyberpsycho, humanity | Heavily augmented person screaming in the middle of a burning street, glitching eyes, civilians fleeing |
| maxtac.png | maxtac, police, heat | Heavily armored police tactical squad in black exosuits landing from a vehicle, red and blue lights |
| police_stop.png | police, heat | Street police officers stopping a person against a wall, scanning them with a handheld device |
| prison.png | prison | Overcrowded high-tech prison block, inmates in orange, guards on catwalks, harsh white light |
| death.png | death | Rainy rooftop funeral, a few people in dark coats around a body under a sheet, city lights below |
| graveyard.png | death, graveyard | Vertical columbarium wall of small screens showing faces of the dead, candles and neon |
| news_studio.png | news | Late-night news broadcast studio, anchor in front of a wall of screens with city footage |
| trauma_team.png | trauma, rescue | Armored medical rescue squad in a flying ambulance vehicle, pulling an injured person aboard |

## Ленточные события (добавлено в M11)
| Файл | Теги | Промпт (после префикса) |
|---|---|---|
| era_change.png | era | Skyline of the city at dawn with a huge glowing calendar hologram over it, a new era beginning, crowds looking up |
| canon_event.png | canon | Emergency news bulletin projected over a plaza, crowd frozen looking at huge screens, reflections in puddles |
| psych_ward.png | ward | Padded isolation ward of a cyberpsychosis clinic, a restrained patient behind glass, medical staff with sedatives, cold light |

Пример картинки под одну эпоху и одно событие (теги дописать в манифест):
```yaml
  - file: era_datakrash.png
    tags: [era, era_datakrash]
  - file: canon_datakrash.png
    tags: [canon, canon_datakrash_2022]
  - file: cyberpsycho_watson.png
    tags: [cyberpsycho, at_watson]
```
