<!-- Copyright (C) 2026 Artur Yakymenko -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Russian glossary

Use natural Russian desktop terminology, sentence case and concise labels. Actions
use infinitives; status and error messages use impersonal phrasing. Instructions
use the polite plural imperative and lowercase вы when a pronoun is necessary.
Preserve complete meanings, placeholders, markup, line breaks, technical identifiers,
fixed units and date-pattern field order. Do not translate firmware menu choices
that the person must find on a separate screen.

| English | Russian |
| --- | --- |
| Programs & Updates | Программы и обновления |
| Settings | Настройки |
| Notes | Заметки |
| Emaki Notes | Заметки Emaki |
| Welcome | Добро пожаловать |
| Welcome to Emaki | Добро пожаловать в Emaki |
| Clocks | Часы |
| What's new | Что нового |
| clock shade | панель часов |
| Change language | Изменить язык |
| update reminder | напоминание об обновлении |
| app / application | приложение |
| program / package | программа / пакет |
| computer | компьютер |
| file / folder | файл / папка |
| password | пароль |
| sudo key | ключ sudo (temporary authority, never a stored password) |
| download | загрузить / загрузка |
| update | обновить / обновление |
| install / uninstall | установить / удалить |
| display / screen | экран |
| workspace | рабочий стол |
| desktop (whole environment) | среда рабочего стола |
| launcher | меню запуска |
| boot menu / startup menu | меню загрузки |
| panel / dock | панель / док |
| notification | уведомление |
| popup | всплывающее уведомление |
| Do not disturb | Не беспокоить |
| restore point | точка восстановления |
| snapshot | снимок |
| backup | резервная копия |
| rollback | откат |
| sign in / sign out | войти / выйти из сеанса |
| lock / unlock | заблокировать / разблокировать |
| restart / shut down | перезапустить / выключить |
| suspend / sleep | спящий режим |
| hibernate | гибернация |
| power | питание |
| network / wireless | сеть / беспроводной |
| keyboard / shortcut | клавиатура / сочетание клавиш |
| Command / command (Mac key / keycap) | Command / command |
| wallpaper | обои |
| Warmth (Night Light) | Тёплый оттенок |
| mouse / trackpad | мышь / сенсорная панель |
| pin / unpin | закрепить / открепить |
| login name | имя пользователя |
| report a problem | сообщить о проблеме |
| report / GitHub issue | отчёт / обращение на GitHub |
| speakers / headphones | динамики / наушники |
| clipboard | буфер обмена |
| storage | хранилище |
| partition / volume / subvolume | раздел / том / подтом |
| boot / startup partition | загрузка / загрузочный раздел |
| erase disk / format | стереть диск / форматировать |
| live session | временный сеанс |
| startup password (encrypted disk) | пароль диска |
| shrink partition | уменьшить раздел |
| root partition | корневой раздел |
| development kit / Rust toolchain | набор для разработки / тулчейн Rust |
| review / preview | обзор / предпросмотр |
| default | по умолчанию |
| enabled / disabled | включено / отключено |
| on / off | вкл. / выкл. |
| Apply / Cancel / Close | Применить / Отмена / Закрыть |
| Save / Delete / Remove | Сохранить / Удалить / Убрать |
| Back / Next / Done | Назад / Далее / Готово |
| Retry / Continue | Повторить / Продолжить |
| Keep it (partition) | Не удалять |
| Restart after (update badge) | Нужен перезапуск |
| Picked by Emaki | Выбор Emaki |
| A good place to start | С чего начать |
| Make yourself at home | Чувствуйте себя как дома |

Use Удалить for uninstalling software, deleting an account or destroying data;
Убрать fits removing an item from a list. Match the grammatical gender of the
surrounding sentence when translating states. Use ё where appropriate.

Use Гц, мс, Б and пикс. for displayed Hz, ms, B and px units. Multi-letter byte units
(MB, GB, GiB) stay in Latin letters. Separate a number
from a percent sign with a non-breaking space (U+00A0). A time-entry hint uses
ЧЧ:ММ; executable date/time patterns retain their original syntax and field order.

Keep Emaki, Windows (the OS), niri, Arch, AUR, Flathub, Secure Boot, GRUB, snapper and other product
names unchanged. Keep command arguments, protocol values, confirmation tokens,
paths, firmware labels and keyboard key names unchanged. Display labels are
translated separately from stable identifiers. Rich / Minimal are translated as Полный / Минимальный in software selection labels.

Russian has three plural forms for both Qt numerus and gettext: one (1, 21),
few (2–4, 22–24), many (0, 5–20, 25–30). Numbers 11–14 use many.

`nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);`

Abbreviations and invariant count labels may use identical text in all three
forms. Unchanged technical messages are documented individually in
`ru-unchanged.json`.
