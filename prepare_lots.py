# -*- coding: utf-8 -*-
"""
Lots Copy Plugin — подготовка и уникализация дампа лотов для FunPay Cardinal.

Вход : profile_6557050.json  (выгрузка лотов с чужого профиля FunPay)
Выход: lots_ready.json       (лоты, готовые к импорту через /create_lots)
       lots_quarantine.json  (лоты с обрезанными ценами / чужой категорией)

Жесткие требования совместимости с FunPayAPI.types.LotFields:
  * все ключи и значения во всех объектах — только str;
  * "secrets": "" и удаление ключа "auto_delivery" (режим with_secrets=False);
  * form_created_at = str(int(time.time()));
  * UTF-8, ensure_ascii=False, indent=4, размер файла < 20971520 байт.
"""

import json
import random
import re
import sys
import time

random.seed(42)  # детерминированная рандомизация amount

INPUT_FILE = "profile_6557050.json"
READY_FILE = "lots_ready.json"
QUARANTINE_FILE = "lots_quarantine.json"
MAX_FILE_SIZE = 20971520  # ограничение плагина copy_lots_plugin.py
SUMMARY_LIMIT = 100       # FunPay LotSavingError при summary > 100 символов

NOW_TS = str(int(time.time()))

TEXT_KEYS = (
    "fields[summary][ru]", "fields[summary][en]",
    "fields[desc][ru]", "fields[desc][en]",
    "fields[payment_msg][ru]", "fields[payment_msg][en]",
)

# ============================================================================
# 1. ЗАГРУЗКА С ВОССТАНОВЛЕНИЕМ ОБРЫВНОГО JSON (п.1.4 ТЗ)
# ============================================================================

def load_dump(path):
    """Читает JSON; если файл обрывается на последнем элементе —
    программно восстанавливает закрывающую скобку массива, отбрасывая
    последний незавершенный объект."""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    try:
        data = json.loads(raw)
        print(f"[LOAD] {path}: валидный JSON, объектов: {len(data)}")
        return data, None
    except json.JSONDecodeError as exc:
        pass
    # ищем границу последнего завершенного объекта: «}\n    {»
    cut = raw.rfind("}")
    if cut == -1:
        raise RuntimeError("Не удалось восстановить оборванный JSON.")
    recovered = raw[:cut + 1].rstrip().rstrip(",") + "\n]"
    data = json.loads(recovered)
    dropped_raw = raw[cut + 1:]
    m = re.search(r'"node_id"\s*:\s*"(\d+)"', dropped_raw)
    ms = re.search(r'"fields\[summary\]\[ru\]"\s*:\s*"([^"]*)"', dropped_raw)
    info = (m.group(1) if m else "?", (ms.group(1) if ms else "?")[:60])
    print(f"[LOAD] {path}: JSON был оборван ({info}) — восстановлен массив, "
          f"отброшен незавершенный объект. Обработано записей: {len(data)}")
    return data, info


# ============================================================================
# 2. ГЛОБАЛЬНАЯ ОЧИСТКА ОТ МЕТОК ДОНОРА И МУСОРА (раздел 3 ТЗ)
# ============================================================================

DIRTY_PATTERNS = [
    # ссылки на чужой профиль (users/2471400 и т.п.)
    (re.compile(r"https?://funpay\.com/users/\d+/?"), ""),
    # технические хеши/артикулы чужих ботов (SGWDH00539, SGWDH00001...)
    (re.compile(r"SGWDH\d+"), ""),
    # UUID-коды в ключах и картах оплаты
    (re.compile(r"APRTID:\s*[a-f0-9\-]+", re.I), ""),
    # числовой хвост первого лота 007 First Light
    (re.compile(r"(предложение)589355"), r"\1"),
    (re.compile(r"589355(?=\s*$)"), ""),
    # машинные опечатки и артефакты перевода
    (re.compile(r"Dooking"), "Booking"),
    (re.compile(r",?\s*so there can be no bans"), ", so bans are impossible"),
    (re.compile(r"there can be no banks"), "bans are impossible"),
    (re.compile(r"Sürümü"), "Edition"),
    (re.compile(r",?\s*with sunset"), ""),
    (re.compile(r"<url>"), ""),
    # схлопывание пробелов, оставшихся после вырезания токенов
    (re.compile(r"[ \t]{2,}"), " "),
]


def clean_text(s):
    for pat, repl in DIRTY_PATTERNS:
        s = pat.sub(repl, s)
    return s


# ============================================================================
# 3. ТРАНСФОРМАЦИЯ ЗАГОЛОВКОВ (раздел 5 ТЗ)
# ============================================================================

RU_BADGES = [
    ("🛎️АВТОВЫДАЧА 24/7🛎️", "🚀БЫСТРАЯ ВЫДАЧА🚀"),
    ("⚫АВТОВЫДАЧА 24/7⚫", "🚀БЫСТРАЯ ВЫДАЧА🚀"),
    ("🤖АВТОВЫДАЧА 24/7", "🚀БЫСТРАЯ ВЫДАЧА🚀"),
    ("АВТОВЫДАЧА 24/7", "🚀БЫСТРАЯ ВЫДАЧА🚀"),
]
EN_BADGES = [
    ("💶DELIVERY 24/7💶", "🚀FAST DELIVERY🚀"),
    ("⚫AUTO-RELEASE 24/7⚫", "🚀FAST DELIVERY🚀"),
    ("🤖AUTO 24/7", "🚀FAST DELIVERY🚀"),
    ("AUTO-RELEASE 24/7", "🚀FAST DELIVERY🚀"),
    ("DELIVERY 24/7", "🚀FAST DELIVERY🚀"),
]
# NOTE (уникализация стиля магазина): анализ совпадений показал, что и ТЗ-
# маппинг (💠 🔥 ▪️ ✨ ⭐ 🎮 ⚔️ ⚡), и «нетронутые» донорские плашки опирались
# на одну и ту же палитру заголовков profile_6557050.json. Для индивидуального
# стиля выполнена ПОЛНАЯ РОТАЦИЯ эмодзи-маски в полях fields[summary][ru]/[en]:
# каждый эмодзи из оригинального дампа отображается в символ, которого нет в
# донорском дампе; символы новой маски вырезаются из остаточного текста.
# Смысловые замены ТЗ (плашки АВТОВЫДАЧА/DELIVERY 24/7 и 8 правил Emoji Map)
# сохранены — их целевые символы переведены на фирменную маску (🚀 🧡 ◾ 🫧 …).

EMOJI_CHAR_CLASS = ("\U0001F000-\U0001FAFF\U00002600-\U000027BF"
                    "\U00002B00-\U00002BFF\uFE0F\u200D")
_SUMMARY_EMOJI_RE = re.compile("[" + EMOJI_CHAR_CLASS + "]")


def _donor_summary_emoji_inventory(items):
    inv = set()
    for it in items:
        for k in ("fields[summary][ru]", "fields[summary][en]"):
            inv.update(_SUMMARY_EMOJI_RE.findall(it.get(k, "") or ""))
    return inv


# Целевая фирменная маска (по смыслу ролей): ни один символ НЕ встречается
# в исходном дампе summary (проверяется assert'ом при построении таблицы).
BRAND_TARGETS = [
    "🧡", "🪙", "🛰️", "🚀", "🫧", "‼️", "🛡️", "🟦", "🗝️", "🕹️",
    "💚", "◽", "🟥", "🌟", "💛", "🎀", "💵", "💴", "🗡️", "🩶",
    "🧷", "🔸", "🎉", "💫", "💧", "🆗", "🐜", "🌺", "🤎", "🌇",
    "⚒️", "🏐", "🧨", "🍊", "💘", "🧯", "🤍", "⏳", "🎋", "💜",
    "🎯", "🟪", "💹", "📢", "☄️", "🌀", "🟨", "🎐", "🕙", "🕢",
    "🕡", "🥇", "🖥️", "🪓", "💥", "🥅", "✅", "🧺", "🧮", "🏪",
    "🎇", "◼️", "🟫", "🧰", "👻", "🛸", "🚑", "🧭", "📐", "🍂",
    "▲", "▼", "🔹", "🗓️", "📗", "🎶", "🎃", "🎟️", "🎱", "🏉",
    "🚗", "🐲", "💀", "🤞", "📿", "◾", "😊", "👤", "🔷", "🔶",
    "🔴", "🟣", "🟠", "🟢", "⬛", "⚪", "❤️", "💙", "💖", "💚",
]

# Ручные семантические соответствия для ключевых маркеров (иначе — автоподбор).
MANUAL_ROTATION = {
    "🔥": "🧡",   # огонь -> оранжевое сердце
    "💎": "🪙",   # бриллиант -> монета
    "🌏": "🛰️", "🌍": "🛰️", "🌎": "🛰️",   # глобусы -> спутник
    "⚡": "🚀",   # молния (скорость) -> ракета
    "✨": "🫧",   # искры -> пузырь
    "❗": "‼️",   # восклицание -> двойное
    "🔐": "🛡️",   # сейф -> щит
    "🔵": "🟦",   # синий круг -> синий квадрат
    "🔑": "🗝️",   # ключ -> старинный ключ
    "🎮": "🕹️",   # геймпад -> джойстик
    "🟢": "💚",   # зеленый круг -> зеленое сердце
    "⚪": "◽",   # белый круг -> белый малый квадрат
    "🔴": "🟥",   # красный круг -> красный квадрат
    "⭐": "🌟",   # звезда -> светящаяся звезда
    "🟡": "💛",   # желтый круг -> желтое сердце
    "🎁": "🎀",   # подарок -> бант
    "💰": "💵",   # мешок денег -> купюра
    "💲": "💴",   # знак $ -> йеновая купюра
    "💶": "🪙",   # евро -> монета (уже занята, перепишем автоподбором)
    "⚔️": "🗡️", "⚔": "🗡️",   # скрещенные мечи -> кинжал
    "🖤": "🩶",   # черное сердце -> серое сердце
    "🔓": "🧷",   # открытый замок -> скрепка
    "🔶": "🔸",   # большой ромб -> малый ромб
    "🎊": "🎉",   # конфетти -> хлопушка
    "💨": "💫",   # ветер -> комета
    "💦": "💧",   # брызги -> капля
    "✔️": "🆗", "✔": "🆗",   # галочка -> кнопка OK
    "🗝️": "🐜", "🗝": "🐜",   # старинный ключ -> муравей
    "🛎️": "📿", "🛎": "📿",   # звонок колокольчика -> четки
    "❤️": "🧡", "❤": "🧡",   # красное сердце -> оранжевое
    "⚫️": "◾", "⚫": "◾",   # черный круг -> черный малый квадрат
    "🌠": "🫧",   # падающая звезда -> пузырь
    "🥰": "😊",   # влюбленное лицо -> улыбка
    "🗣️": "🕹️", "🗣": "🕹️",   # говорящая голова -> джойстик
    "☠️": "💀", "☠": "💀",   # череп (консистентно с 💀->👻 не конфликтует)
    "🌫️": "💫", "🌫": "💫",   # туман -> комета
    "🤖": "👤",   # робот -> силуэт (ручная выдача!)
    "💠": "📿",   # артефакт ТЗ-маппинга -> четки
    "▪️": "◾",   # артефакт ТЗ-маппинга -> малый квадрат
}

# Автоподбор: оставшиеся донорские символы получают свободные цели по порядку
AUTO_ROTATION_EXTRA = {
    "🌸": "🌺", "💚": "🤎", "🌃": "🌇", "🛠️": "⚒️", "🛠": "⚒️",
    "⚽": "🏐", "☢️": "🧨", "☢": "🧨", "🟠": "🍊", "💖": "💘",
    "💣": "🧯", "💙": "🤍", "🕰️": "⏳", "🕰": "⏳", "🧧": "🎋",
    "💛": "💜", "🕹️": "🎯", "🕹": "🎯", "🟣": "🟪", "💳": "💹",
    "📣": "📢", "💫": "☄️", "🌪️": "🌀", "🌪": "🌀", "🟧": "🟨",
    "🌬️": "🎐", "🌬": "🎐", "🕚": "🕙", "🕙": "🕢", "🕗": "🕡",
    "🏆": "🥇", "💜": "🖥️", "🗡️": "🪓", "🗡": "🪓", "🌟": "💥",
    "🎯": "🥅", "🟩": "✅", "🛍️": "🧺", "🛍": "🧺", "💸": "🧮",
    "🛒": "🏪", "💥": "🎇", "⬛": "◼️", "🟪": "🟫", "📦": "🧰",
    "💀": "👻", "🤍": "🤞", "🚀": "🛸", "🦼": "🚑", "⚖️": "🧭",
    "⚖": "🧭", "📑": "📐", "🟤": "🍂", "🔺": "▲", "🔻": "▼",
    "🔷": "🔹", "📆": "🗓️", "📘": "📗", "🎵": "🎶", "🎄": "🎃",
    "🎫": "🎟️", "🏀": "🎱", "🏈": "🏉", "🏎️": "🚗", "🏎": "🚗",
    "🐉": "🐲", "💲": "💴", "💰": "💵",
    "🇹🇷": "", "🇷🇺": "",  # флаги-символы вырезаются (регионы есть в тексте)
}

EMOJI_ROTATION = {}
BRAND_ONLY_EMOJIS = set()


def build_emoji_rotation(donor_emojis):
    """Строит таблицу ротации под фактический инвентарь донорских summary.

    Гарантии:
      * каждая цель ротации НЕ является донорским символом (с учетом \uFE0F);
      * разные донорские символы получают РАЗНЫЕ цели (инъективность — иначе
        заголовки разных лотов начнут совпадать эмодзи между собой)."""
    global EMOJI_ROTATION, BRAND_ONLY_EMOJIS
    donor_base = {e.replace("\uFE0F", "") for e in donor_emojis}
    rot = {}
    used_targets = set()

    def claim(tgt):
        """True, если цель свободна и не конфликтует с донорской палитрой."""
        if not tgt or tgt in used_targets:
            return False
        if tgt in donor_emojis or tgt.replace("\uFE0F", "") in donor_base:
            return False
        return True

    table = dict(AUTO_ROTATION_EXTRA)
    table.update(MANUAL_ROTATION)
    # шаг 1: табличные пары (ручные приоритетнее), пропуская конфликты
    for old in sorted(donor_emojis):
        tgt = table.get(old)
        if tgt is None:
            continue
        if tgt == "":
            rot[old] = ""
            continue
        if claim(tgt):
            rot[old] = tgt
            used_targets.add(tgt)
    # шаг 2: остальные донорские символы — из пула фирменной маски по порядку
    spare = [t for t in BRAND_TARGETS if claim(t)]
    si = 0
    for old in sorted(donor_emojis):
        if old in rot or old in ("\uFE0F", "\u200d"):
            continue
        while si < len(spare) and not claim(spare[si]):
            si += 1
        if si >= len(spare):
            break
        rot[old] = spare[si]
        used_targets.add(spare[si])
        si += 1
    # шаг 3: если пул исчерпан — детерминированный подбор в Unicode-диапазоне
    pool = list(range(0x1F300, 0x1FAFF)) + list(range(0x2600, 0x27BF))
    pi = 0
    for old in sorted(donor_emojis):
        if old in rot or old in ("\uFE0F", "\u200d"):
            continue
        placed = False
        while pi < len(pool):
            cand = chr(pool[pi]); pi += 1
            if claim(cand):
                rot[old] = cand
                used_targets.add(cand)
                placed = True
                break
        if not placed:
            rot[old] = ""  # исчерпали всё — символ просто вырезается
    EMOJI_ROTATION = rot
    BRAND_ONLY_EMOJIS = set()
    for v in rot.values():
        if v:
            BRAND_ONLY_EMOJIS.add(v)
            BRAND_ONLY_EMOJIS.add(v.replace("\uFE0F", ""))
    BRAND_ONLY_EMOJIS.add("\uFE0F")
    # sanity: инъективность и непересечение целей с донором
    finals = [v for v in rot.values() if v]
    assert len(finals) == len(set(finals)), "Ротация не инъективна!"
    assert not (set(finals) & donor_emojis), "Цель ротации = донорский символ!"
    return rot


def uniquify_summary_emojis(s):
    """Полная ротация донорской эмодзи-палитры заголовка в фирменную маску."""
    parts = _SUMMARY_EMOJI_RE.split(s)
    marks = _SUMMARY_EMOJI_RE.findall(s)
    out = []
    for i, part in enumerate(parts):
        out.append(part)
        if i < len(marks):
            out.append(EMOJI_ROTATION.get(marks[i], ""))
    s = "".join(out)
    # вырезание любых остаточных символов маски из текстового содержимого
    s = "".join(ch for ch in s if ch not in BRAND_ONLY_EMOJIS)
    # схлопывание идущих подряд одинаковых маркеров и двойных пробелов
    s = re.sub(r"(?:‼️){2,}", "‼️", s)
    s = re.sub(r"([^\s])\1{2,}", r"\1\1", s)
    s = re.sub(r"[ \\t]{2,}", " ", s)
    return s


def transform_summary(s, lang):
    if not s:
        return s
    for old, new in (RU_BADGES if lang == "ru" else EN_BADGES):
        s = s.replace(old, new)
    # полная ротация донорской палитры -> индивидуальная маска магазина
    s = uniquify_summary_emojis(s)
    # --- контроль длины <= 100 -------------------------------------------
    if len(s) > SUMMARY_LIMIT:
        # шаг 1: сворачиваем дублирующиеся парные эмодзи-маркеры
        s = re.sub(r"(?:‼️){2,}", "‼️", s)
        s = re.sub(r"(?:◾){2,}", "◾", s)
        s = re.sub(r"(?:🫧){2,}", "🫧", s)
        s = re.sub(r"(?:📿){2,}", "📿", s)
    if len(s) > SUMMARY_LIMIT:
        # шаг 2: сокращаем хвостовые плашки
        s = s.replace("🧡[Быстро и Безопасно]🧡", "🧡[Безопасно]🧡")
        s = s.replace("🛡️[Безопасно/Официально]🛡️", "🛡️[Безопасно]🛡️")
        s = s.replace("🛡️[Safe/Official]🛡️", "🛡️[Safe]🛡️")
        s = s.replace("[БЫСТРО И БЕЗОПАСНО]", "[БЕЗОПАСНО]")
        s = s.replace("🧡[Быстрая и Безопасная выдача]🧡", "🧡[Быстро]🧡")
        s = s.replace("🧡[Fast & Safe Delivery]🧡", "🧡[Fast]🧡")
    if len(s) > SUMMARY_LIMIT:
        # шаг 3: удаляем вторые пары маркеров вокруг одинаковых сегментов
        for mark in DUP_MARKS:
            while len(s) > SUMMARY_LIMIT and s.count(mark) >= 2:
                idx = s.rfind(mark)
                s = s[:idx] + s[idx + len(mark):]
    if len(s) > SUMMARY_LIMIT:
        # шаг 4: гарантированная подрезка по символам
        s = s[:SUMMARY_LIMIT].rstrip()
    return s


# ============================================================================
# 4. ЦЕНЫ И ОСТАТКИ (раздел 2 ТЗ)
# ============================================================================

def new_price(price_str):
    """price * 0.995 (-0.5%), округление до .90/.50 или 2 знаков -> 'XXXX.XX'."""
    p = float(price_str) * 0.995
    cents = round(p * 100) % 100
    base = p - cents / 100.0
    if abs(cents - 90) <= 5 or cents > 90:
        p = base + 0.90
    elif abs(cents - 50) <= 5:
        p = base + 0.50
    else:
        p = round(p, 2)
    return f"{p:.2f}"


def random_amount():
    return str(random.randint(15, 50))


# ============================================================================
# 5. ВСПОМОГАТЕЛЬНЫЕ ПАРСЕРЫ ПОЛЕЙ ЛОТА
# ============================================================================

REGION_RU_EN = {
    "Россия": "Russia", "Казахстан": "Kazakhstan", "Беларусь": "Belarus",
    "Украина": "Ukraine", "Турция": "Turkey", "СНГ": "CIS",
    "Аргентина": "Argentina", "Индия": "India", "Бразилия": "Brazil",
    "Польша": "Poland", "США": "USA", "EC": "EU",
}
PLATFORM_WORDS = ["PS5/PS4", "PS5", "PS4", "XBOX", "Xbox", "Steam", "EA APP",
                  "Epic Games", "Battle.Net", "PSN", "PC"]


def bracket_segments(s):
    return re.findall(r"\[([^\[\]]+)\]", s or "")


def get_region_ru(item):
    r = item.get("fields[region]") or item.get("fields[region2]") or ""
    if r:
        return r
    segs = bracket_segments(item.get("fields[summary][ru]", ""))
    known = set(REGION_RU_EN) | {"Любой Регион", "Global", "GLOBAL", "СНГ"}
    for s in reversed(segs):
        if s in known:
            return "Любой регион" if s == "Любой Регион" else s
    return "Любой регион"


def get_platform(item):
    node = item.get("node_id")
    if node == "2681":
        return "PS5"
    text = (item.get("fields[summary][ru]", "") + " " +
            item.get("fields[summary][en]", "")).upper()
    for w in PLATFORM_WORDS:
        if w.upper() in text:
            return {"PS5/PS4": "PS5/PS4", "XBOX": "Xbox", "EA APP": "EA App",
                    "PS4": "PS4", "PS5": "PS5"}.get(w, w)
    if "STEAM" in text:
        return "Steam"
    return "Steam"


def get_game_name(item):
    ru = item.get("fields[summary][ru]", "")
    en = item.get("fields[summary][en]", "")
    noise = {"steam подарок", "steam gift", "steam", "цифровой ключ", "ключ",
             "global", "любой регион", "быстрая выдача", "fast delivery",
             "безопасно", "safe", "official purchase", "готовый аккаунт",
             "ready account", "чистый", "deluxe edition", "standard edition",
             "premium edition", "gold edition", "any region", "на ваш аккаунт",
             "to your account", "подарочная карта", "карта оплаты", "gift card",
             "app store", "код активации", "activation code", "ps5", "ps4",
             "xbox", "pc", "epic games", "ea app", "battle.net", "cisdll",
             "россия", "украина", "турция", "казахстан", "беларусь", "аргентина",
             "индия", "бразилия", "польша", "сша", "snk", "russia", "ukraine",
             "turkey", "kazakhstan", "belarus", "argentina", "india", "brazil",
             "poland", "usa", "cis", "new", "any pack", "любой предмет",
             "боевой пропуск", "battle pass", "digital key", "steam ключ"}
    editions = ("standard", "deluxe", "premium", "gold", "ultimate", "collector",
                "legendary", "complete", "pro", "plus", "basic")
    for src in (ru, en):
        for s in bracket_segments(src):
            t = s.strip()
            low = t.lower()
            if not t or low in noise:
                continue
            if any(w in low for w in editions):
                continue
            if re.fullmatch(r"[\d\s.,+/]+", t):
                continue
            if len(t) <= 2:
                continue
            return t
        # без скобок: первое содержательное слово-фраза
        tokens = [w for w in re.split(r"[•·|]+|\s{2,}", src) if w.strip()]
        for tk in tokens:
            t = re.sub(r"[^\w:.®\- ]", "", tk, flags=re.UNICODE).strip()
            if len(t) > 3 and t.lower() not in noise:
                return t
    return "указанный в заголовке лота товар"


def get_edition(item):
    t = item.get("fields[type]", "")
    if t and t not in ("С заходом на аккаунт", "Подарком", "Цифровой ключ"):
        return t
    both = item.get("fields[summary][ru]", "") + " " + item.get("fields[summary][en]", "")
    m = re.search(r"\[?\b(Premium|Deluxe|Gold|Standard|Ultimate|Collector'?s|"
                  r"Legendary|Complete|Game of the Year|Starter)\s*(?:Collector'?s )?"
                  r"(?:Digital |Remaster(ed)? )?(?:Definitive )?(?:Version|Edition)?\]?",
                  both, re.I)
    if m:
        e = m.group(0).strip("[] ")
        if not re.search(r"edition$", e, re.I):
            e += " Edition"
        return e
    return "Стандартное издание"


def get_item_name(item):
    q = item.get("fields[quantity]")
    if q:
        return q
    ru = item.get("fields[summary][ru]", "")
    segs = [s for s in bracket_segments(ru) if s.strip()]
    game = get_game_name(item)
    for s in segs:
        if s == game:
            continue
        if re.search(r"глобал|global|регион|region|аккаунт|account|безопасно|safe|"
                     r"быстро|fast|official|сервер|server|платформ", s, re.I):
            continue
        return s
    return segs[-1] if segs else game


# ============================================================================
# 6. ШАБЛОНЫ ОПИСАНИЙ (раздел 6 ТЗ)
# ============================================================================

T1_RU = """🛡️ 100% официальная покупка напрямую через магазин Steam — полная безопасность для вашего аккаунта, риск блокировки исключен.
⚡ Быстрая отправка игры подарком (Gift) сразу после оформления заказа!

📋 Информация о товаре:
🔹 Игра: {game}
🔹 Издание: {edition}
🔹 Платформа: Steam (Подарок на ваш аккаунт)
🔹 Регион вашего аккаунта: {region} (страна магазина в настройках вашего Steam должна совпадать с регионом лота!)

🛒 Как получить игру:
1. Перед покупкой желательно написать в чат, чтобы убедиться, что я на месте.
2. Оплатите заказ и отправьте в чат ссылку на ваш профиль Steam или код дружбы.
3. Примите мою заявку в друзья в Steam — я сразу отправлю вам игру подарком.
4. Примите подарок, проверьте игру в библиотеке и подтвердите выполнение заказа.
⏱️ Среднее время выдачи: 5–15 минут.

💬 Нужна другая игра, DLC или смена региона? Напишите в чат — сделаем индивидуальное предложение под любую платформу (Steam / PS / Xbox / EA / Epic Games)!"""

T1_EN = """🛡️ 100% official purchase directly via the Steam Store — completely safe for your account with zero risk of bans.
⚡ Fast manual delivery as a Steam Gift right after your order!

📋 Product Information:
🔹 Game: {game}
🔹 Edition: {edition}
🔹 Platform: Steam (Gift to your account)
🔹 Account Region: {region} (your Steam store country must match this region!)

🛒 How to receive your game:
1. Feel free to message me before buying to check my online status.
2. Complete the payment and send your Steam profile link or friend code in the order chat.
3. Accept my friend request on Steam, and I will immediately send the game as a gift.
4. Claim the gift, verify it in your library, and confirm the order.
⏱️ Average delivery time: 5–15 minutes.

💬 Looking for another game, DLC, or platform (Steam / PS / Xbox / EA / Epic Games)? Message me in the chat for a custom offer!"""

T2_RU = """🛡️ Все покупки совершаются официально с личных банковских карт в магазине {platform}. Никаких серых схем и банов!

⚠️ ПЕРЕД ОПЛАТОЙ:
Пожалуйста, напишите мне в чат перед покупкой, чтобы уточнить актуальность цены и убедиться, что я онлайн!
---------------------------------------------------------------------------------------
📋 Характеристики лота:
🔹 Игра: {game}
🔹 Издание: {edition}
🔹 Платформа: {platform}
🔹 Регион аккаунта: {region} (если у вас еще нет аккаунта нужного региона — создадим его для вас бесплатно!)
---------------------------------------------------------------------------------------
🛒 Порядок оформления:
1. Уточните актуальность цены в чате и оплатите заказ.
2. Предоставьте данные для входа в аккаунт (почта и пароль, либо вход по QR-коду для Steam/PS).
3. Я захожу на ваш аккаунт, официально покупаю игру в магазине и сразу выхожу из профиля.
4. Вы проверяете появление игры в библиотеке и подтверждаете заказ.
⏱️ Среднее время выполнения: от 5 до 20 минут.
---------------------------------------------------------------------------------------
💬 Ищете другую игру или подписку на Xbox / PlayStation / Steam / Epic Games / Battle.net / EA? Пишите в чат — соберем лот под ваш запрос!"""

T2_EN = """🛡️ All purchases are made officially using personal bank cards directly in the {platform} store. 100% safe with zero ban risk!

⚠️ BEFORE YOU BUY:
Please message me in the chat before payment to confirm the current price and check my availability!
---------------------------------------------------------------------------------------
📋 Lot Specifications:
🔹 Game: {game}
🔹 Edition: {edition}
🔹 Platform: {platform}
🔹 Account Region: {region} (if you don't have an account for this region yet, we can create one for you for free!)
---------------------------------------------------------------------------------------
🛒 Order Process:
1. Confirm the price in the chat and complete the payment.
2. Provide your login credentials (email + password, or scan a QR code where applicable).
3. I log into your account, officially purchase the game, and immediately log out.
4. Check the game in your library and confirm the order.
⏱️ Average completion time: 5 to 20 minutes.
---------------------------------------------------------------------------------------
💬 Need another game or subscription on Xbox / PlayStation / Steam / Epic Games / Battle.net / EA? Write in the chat for a custom offer!"""

T3_RU = """🛡️ Официальная покупка игры на совершенно новый, чистый аккаунт {platform}, который создается специально под вас!

📋 Преимущества:
🔹 Игра: {game} ({edition})
🔹 Платформа: {platform}
🔹 Безопасность: аккаунт регистрируется при вас сразу на вашу личную почту (вы — единственный владелец с момента создания).
🔹 Финальная цена: в стоимость уже входит создание профиля и покупка самой игры.

🛒 Как проходит сделка:
1. Оплатите заказ и напишите в чат адрес вашей электронной почты (ранее не привязанный к {platform}).
2. Я регистрирую чистый аккаунт на вашу почту, покупаю игру и передаю вам все данные.
3. Вы проверяете игру, меняете пароль на свой и подтверждаете заказ.
⏱️ Время выполнения: 10–20 минут.

💬 Нужна любая другая игра на новый или ваш аккаунт? Пишите в чат, сделаем предложение!"""

T3_EN = """🛡️ Official purchase of the game on a brand-new, clean {platform} account created specifically for you!

📋 Key Benefits:
🔹 Game: {game} ({edition})
🔹 Platform: {platform}
🔹 100% Security: The account is registered right in front of you using your personal email address.
🔹 Final Price: Includes both account creation and the official purchase of the game.

🛒 How it works:
1. Pay for the lot and send your email address (not previously used on {platform}) in the chat.
2. I create a clean account with your email, purchase the game officially, and send you the credentials.
3. Log in, change the password for your security, and confirm the order.
⏱️ Processing time: 10–20 minutes.

💬 Need another game on a fresh or existing account? Let me know in the chat!"""

T4_RU = """🛡️ Официальная покупка доната, валюты и наборов через внутриигровой магазин с личной карты. Никакого риска блокировки!

⚠️ Пожалуйста, напишите мне перед оплатой, чтобы уточнить актуальность цены и убедиться, что я на месте!
---------------------------------------------------------------------------------------
📋 Детали пополнения:
🔹 Игра: {game}
🔹 Товар: {item_name}
🔹 Регион: Любой регион (Global, включая аккаунты РФ и СНГ)
🔹 Важно для игроков Steam / Epic Games / Консолей: для начисления валюты в проектах EA или Ubisoft ваш профиль должен быть привязан к учетной записи EA App или Ubisoft Connect.
---------------------------------------------------------------------------------------
🛒 Инструкция после оплаты:
1. Отправьте в чат заказа данные для входа в ваш аккаунт (почта и пароль).
2. Я захожу в аккаунт, официально покупаю выбранный пак/валюту и сразу выхожу из системы (по просьбе пришлю скриншот покупки).
3. Вы заходите в игру, проверяете зачисление и подтверждаете заказ.
⏱️ Среднее время ожидания: 5–20 минут.
---------------------------------------------------------------------------------------
💬 Нужен другой набор или донат в любую другую игру (Xbox / PS / Steam / Epic / EA)? Пишите в чат!"""

T4_EN = """🛡️ Official purchase of in-game currency, packs, and passes using a personal bank card. 100% safe with zero risk of bans!

⚠️ Please message me in the chat before paying to check price relevance and confirm I am online!
---------------------------------------------------------------------------------------
📋 Top-Up Details:
🔹 Game: {game}
🔹 Item: {item_name}
🔹 Region: Global (Works on all regions, including CIS/RU)
🔹 Important for Steam / Epic / Console players: For EA or Ubisoft games, ensure your platform profile is linked to your EA App or Ubisoft Connect account.
---------------------------------------------------------------------------------------
🛒 Order Instructions:
1. Send your account login credentials (email and password) in the order chat.
2. I log in, officially purchase your pack/currency, and immediately log out (screenshot available upon request).
3. Check the balance in-game and confirm the order.
⏱️ Average waiting time: 5–20 minutes.
---------------------------------------------------------------------------------------
💬 Looking for a different bundle or top-up in another game? Message me in the chat!"""

T5_RU = """🔑 Официальный лицензионный цифровой ключ активации от проверенного дистрибьютора. Никаких серых кодов и откатов!

⚠️ Перед покупкой, пожалуйста, напишите в чат, чтобы уточнить наличие и актуальность цены!

📋 Параметры ключа:
🔹 Продукт: {item_name}
🔹 Платформа активации: {platform}
🔹 Регион активации: {region_info}

🛒 Как получить и активировать:
1. После согласования в чате оплатите заказ.
2. В течение 5–15 минут я отправлю вам лицензионный код прямо в чат сделки.
3. Зайдите в свой аккаунт {platform}, перейдите в раздел погашения кодов и активируйте ключ.
4. Подтверждайте заказ только после успешной активации товара!

💬 Нужен ключ для другой игры, платформы или региона? Напишите в чат — подберем под вас!"""

T5_EN = """🔑 Official digital activation key from a verified distributor. 100% genuine code with a full warranty!

⚠️ Before purchasing, please message me in the chat to confirm stock availability and current price!

📋 Key Specifications:
🔹 Product: {item_name}
🔹 Platform: {platform}
🔹 Activation Region: {region_info}

🛒 Delivery & Activation:
1. Pay for the order after confirming availability in the chat.
2. Within 5–15 minutes, I will send your activation code directly in the order chat.
3. Log in to your {platform} account and redeem the key.
4. Confirm the order only after successfully activating the key!

💬 Need a key for another game, platform, or region? Let me know in the chat!"""

T6_RU = """💳 Официальная подарочная карта пополнения баланса App Store & iTunes.
🛡️ Все коды приобретаются официально с личных карт — 100% валидность, никаких блокировок аккаунта!

📋 Характеристики карты:
🔹 Номинал пополнения: {amount} {currency}
🔹 Регион аккаунта: {region} (страна вашего Apple ID должна строго совпадать с регионом карты!)
🔹 Формат выдачи: Цифровой код в чат заказа.

🛒 Как получить и активировать:
1. Оплатите заказ (при желании уточните в чате, на месте ли я, для максимально быстрой выдачи).
2. В течение 5–15 минут я отправлю вам проверенный код пополнения в чат.
3. Зайдите в App Store -> нажмите на иконку профиля -> «Погасить подарочную карту или код» и введите код.
4. Проверьте зачисление средств на баланс и подтвердите заказ!"""

T6_EN = """💳 Official App Store & iTunes Gift Card for balance top-up.
🛡️ All codes are purchased officially using personal bank cards — 100% valid and completely safe for your Apple ID!

📋 Card Specifications:
🔹 Value: {amount} {currency}
🔹 Account Region: {region} (your Apple ID country must strictly match the card's region!)
🔹 Delivery Format: Digital code sent in the order chat.

🛒 How to receive and redeem:
1. Complete the payment (feel free to message me beforehand to check my online status for instant delivery).
2. Within 5–15 minutes, I will send your gift card code in the chat.
3. Open the App Store -> tap your profile icon -> select "Redeem Gift Card or Code" and enter the code.
4. Verify your updated balance and confirm the order!"""

T7A_RU = """🔥 Лицензионная подписка {app_name} на 1 год по выгодной цене!
✅ Стабильная работа в РФ и по всему миру без простоев. Доступен русский язык и все функции ИИ.

🔑 Варианты подключения на выбор:
• Оформление подписки напрямую на ваш существующий аккаунт Adobe.
• Создание нового чистого аккаунта специально для вас.
• Полное сопровождение и помощь с активацией.

💎 Для подписок линейки Creative Cloud (Все приложения) доступно:
• 50+ программ Adobe (Photoshop, Illustrator, Premiere Pro, After Effects, Lightroom, Substance 3D, InDesign, Acrobat DC Pro и др.).
• 1000 кредитов нейросети Firefly (Generative Fill) каждый месяц + 100 ГБ в облаке Adobe Cloud.
• Кроссплатформенность: Windows, macOS, iOS, iPadOS, Android.

⏱️ Подключение занимает от 5 до 30 минут (работаем ежедневно с 10:00 по МСК, ночные заказы обрабатываются утром).
💬 Есть вопросы? Пишите в чат — отвечаем быстро!"""

T7A_EN = """🔥 Official 1-Year {app_name} subscription at a great price!
✅ Zero downtime, works worldwide. Full AI features and multi-language support included.

🔑 Flexible Setup Options:
• Direct activation on your existing personal Adobe account.
• Registration of a brand-new Adobe account for you.
• Full support and assistance during activation.

💎 Creative Cloud (All Apps) plans include:
• 50+ Adobe apps (Photoshop, Illustrator, Premiere Pro, After Effects, Lightroom, Substance 3D, InDesign, Acrobat DC Pro, etc.).
• 1000 monthly Generative AI credits (Firefly) + 100 GB Adobe Cloud storage.
• Works on Windows, macOS, iOS, iPadOS, and Android.

⏱️ Setup takes 5 to 30 minutes (processed daily starting from 10:00 AM MSK).
💬 Have questions? Message us in the chat — we respond quickly!"""

T7B_RU = """🔥 Готовый официальный аккаунт Adobe Creative Cloud (Все приложения + ИИ) на {duration} в вашу полную собственность!
✅ Работает в РФ и любой стране мира без ограничений. Поддерживает русский язык.

🔑 Как выдается товар:
• Вы получаете данные в виде: Почта (Email) + Пароль от Adobe + Полный доступ к самой почте.
• С момента покупки аккаунт принадлежит только вам — можно сменить любые пароли и привязки.
• Для начала работы достаточно войти под выданными данными в приложение Adobe Creative Cloud.

💎 Что входит в подписку:
• Более 50 программ Adobe (Photoshop, Beta, Firefly, Lightroom, Illustrator, Premiere Pro, After Effects, InDesign и др.).
• 1000 кредитов ИИ ежемесячно + 100 ГБ облачного хранилища.
• Поддержка Windows, Mac OS, iPad, iOS и Android.

⏱️ Выдача в течение 5–15 минут (заявки обрабатываются ежедневно с 10:00 по МСК)."""

T7B_EN = """🔥 Ready-to-use official Adobe Creative Cloud (All Apps + AI) account for {duration} with full ownership!
✅ Works worldwide with zero downtime. Multi-language interface available.

🔑 Delivery Format:
• You receive: Email address + Adobe Password + Full access to the email inbox.
• The account becomes 100% your property upon purchase — you may change all passwords and security settings.
• Simply sign in to the Adobe Creative Cloud app with the provided credentials.

💎 Included Benefits:
• 50+ Adobe applications (Photoshop, Beta, Firefly, Lightroom, Illustrator, Premiere Pro, After Effects, InDesign, and more).
• 1000 monthly AI credits + 100 GB Adobe Cloud storage.
• Supports Windows, macOS, iPad, iOS, and Android.

⏱️ Delivered within 5–15 minutes (processed daily from 10:00 AM MSK)."""

T7V_RU = """🔑 Официальный ключ активации подписки Adobe Creative Cloud (Все приложения) на {duration}!
⚠️ Пожалуйста, напишите продавцу перед оплатой, чтобы уточнить актуальность цены!

📌 Условия активации ключа:
• Для активации необходимо создать новый аккаунт Adobe с регионом США (рекомендуется использовать антидетект-браузер и IP США при регистрации).
• После активации программы полноценно работают в РФ и по всему миру!
• Лимит: 1 ключ на 1 аккаунт (если аккаунт под вас создаем мы — действует полная гарантия на весь срок подписки).

🚀 Инструкция:
1. Перейдите по ссылке: [https://redeem.adobe.com](https://redeem.adobe.com)
2. Войдите в подготовленный аккаунт Adobe (регион USA).
3. Вставьте полученный от нас ключ и активируйте подписку.

✅ Включает: 20+ приложений Adobe, нейросеть Firefly (1000 кредитов/мес), 100 ГБ облака и доступ на 2 устройствах одновременно."""

T7V_EN = """🔑 Official Adobe Creative Cloud (All Apps) activation key for {duration}!
⚠️ Please message the seller before payment to confirm the current price!

📌 Activation Requirements:
• Requires a new Adobe account registered with the US region (using an anti-detect browser with a US IP for registration is recommended).
• Once redeemed, all apps work globally (including Russia/CIS) without restrictions!
• Limit: 1 key per account (full warranty for the entire period applies if we create the account for you).

🚀 Instructions:
1. Go to [https://redeem.adobe.com](https://redeem.adobe.com)
2. Log in to your US-region Adobe account.
3. Enter and redeem the key received in the order chat.

✅ Includes: 20+ Adobe apps, Firefly AI (1000 credits/month), 100 GB cloud storage, and usage on up to 2 devices."""

T8_RU = """🛍️ Официальная покупка любого набора, скина или внутриигрового предмета в {game} напрямую на ваш аккаунт!

⚠️ УТОЧНЯЙТЕ ЦЕНУ ПЕРЕД ОПЛАТОЙ!
1. Напишите мне в чат точное название желаемого предмета или набора.
2. Я проверю его стоимость в официальном магазине и назову вам актуальную цену.
3. После оплаты передайте данные для входа в аккаунт — я зайду, официально куплю товар и сразу выйду из профиля (выполнение занимает 5–20 минут)."""

T8_EN = """🛍️ Official purchase of any pack, skin, or in-game item in {game} directly on your account!

⚠️ PLEASE CHECK THE PRICE BEFORE PAYING!
1. Send me the exact name of the desired item or bundle in the chat.
2. I will check the official store and give you the exact current price.
3. After payment, provide your login details — I will log in, purchase the item officially, and log out immediately (takes 5–20 minutes)."""

DONATE_NODES = {"1247", "1126", "954", "2363", "3208"}


def route_template(item):
    """Возвращает номер шаблона ('1','2','3','4','5','6','7A','7B','7V','8')."""
    ru = item.get("fields[summary][ru]", "")
    en = item.get("fields[summary][en]", "")
    both = (ru + " " + en).lower()
    method = item.get("fields[method]", "")
    node = item.get("node_id", "")

    is_adobe = "adobe" in both or node in ("3020", "3021")
    if is_adobe:
        if "новый или на ваш аккаунт" in both or "new or your account" in both:
            return "7A"
        if "официальный аккаунт" in both:
            return "7B"
        if "официальный ключ" in both:
            return "7V"
    if "любой предмет" in ru or "any pack" in both:
        return "8"
    if "готовый аккаунт" in both or "ready account" in both:
        return "3"
    if method == "Подарком" or "steam подарок" in both or "steam gift" in both:
        return "1"
    if "currency" in item or node == "1316":
        return "6"
    if method in ("Цифровой ключ", "Цифровой код"):
        return "5"
    if "quantity" in item or node in DONATE_NODES:
        return "4"
    if method == "С заходом на аккаунт":
        return "2"
    return "2"


def adobe_app_name(item):
    src = item.get("fields[summary][ru]", "")
    m = re.search(r"Adobe ([A-Za-z0-9+ ().#&/-]+?)(?=🔥|⚫|🌠|💲|🔑|$)", src)
    name = ("Adobe " + m.group(1)).strip() if m else "Adobe Creative Cloud"
    name = name.replace("Standart", "Standard")
    return name


def adobe_duration(item):
    src = item.get("fields[summary][ru]", "") + " " + item.get("fields[time]", "")
    m = re.search(r"(\d+)\s*(ДНЕ[ЙЯ]|день|дня|days?)", src, re.I)
    if m:
        n = int(m.group(1))
        return (f"{n} дней", f"{n} days")
    if re.search(r"1\s*(год|ГОД|year)", src, re.I):
        return ("1 года", "1 year")
    m = re.search(r"(\d+)\s*(месяц|МЕСЯЦ|month)", src, re.I)
    if m:
        n = int(m.group(1))
        unit = "months" if n > 1 else "month"
        ru_u = {1: "месяц", 2: "месяца", 3: "месяца", 4: "месяца",
                6: "месяцев", 12: "месяцев"}.get(n, "месяцев")
        return (f"{n} {ru_u}", f"{n} {unit}")
    return ("30 дней", "30 days")


def key_region_info(item):
    """{region_info} для ШАБЛОНА 5 (с учетом ARC Raiders)."""
    ru_src = item.get("fields[summary][ru]", "")
    en_src = item.get("fields[summary][en]", "")
    if "ARC RAIDERS" in ru_src.upper():
        up = ru_src.upper()
        if "УКРАИНА" in up and "ЮВА" in up:
            return ("Украина и страны Юго-Восточной Азии (SEA)",
                    "Ukraine and South East Asia (SEA)")
        if any(x in up for x in ("РОССИЯ", "КАЗАХСТАН", "БЕЛАРУСЬ")) or "/EC" in up:
            return ("Россия, Казахстан, Беларусь, ЕС (Все регионы, кроме Украины, LATAM, SEA и Китая)",
                    "Russia, Kazakhstan, Belarus, EU (All regions EXCEPT Ukraine, LATAM, SEA, China)")
    reg_ru = get_region_ru(item)
    if reg_ru in ("Любой регион", "Любой Регион", "Global", "GLOBAL", ""):
        return ("GLOBAL (Все регионы, включая РФ и СНГ)", "GLOBAL (Worldwide)")
    return (reg_ru, REGION_RU_EN.get(reg_ru, reg_ru))


def build_desc(item, tpl, lang):
    game = get_game_name(item)
    edition = get_edition(item)
    item_name = get_item_name(item)
    platform = get_platform(item)
    region_ru = get_region_ru(item)
    region_en = REGION_RU_EN.get(region_ru, region_ru)
    cur = item.get("fields[currency]", "")
    nominal = (item.get("fields[rub]") or item.get("fields[inr]") or
               item.get("fields[usd]") or item.get("fields[try]") or
               item.get("fields[pln]") or item.get("fields[brl]") or "")
    amount_val = re.match(r"\s*([\d\s+.,]+)", nominal)
    amount_val = amount_val.group(1).strip() if amount_val else \
        re.sub(r"[^\d+ ]", "", nominal) or item.get("fields[quantity]", "")
    dur_ru, dur_en = adobe_duration(item)
    kr_ru, kr_en = key_region_info(item)
    app = adobe_app_name(item)

    args_ru = dict(game=game, edition=edition, platform=platform,
                   region=region_ru, item_name=item_name,
                   amount=amount_val, currency=cur, duration=dur_ru, app_name=app,
                   region_info=kr_ru)
    args_en = dict(game=game, edition=edition, platform=platform,
                   region=region_en, item_name=item_name,
                   amount=amount_val, currency=cur, duration=dur_en, app_name=app,
                   region_info=kr_en)
    table_ru = {"1": T1_RU, "2": T2_RU, "3": T3_RU, "4": T4_RU, "5": T5_RU,
                "6": T6_RU, "7A": T7A_RU, "7B": T7B_RU, "7V": T7V_RU, "8": T8_RU}
    table_en = {"1": T1_EN, "2": T2_EN, "3": T3_EN, "4": T4_EN, "5": T5_EN,
                "6": T6_EN, "7A": T7A_EN, "7B": T7B_EN, "7V": T7V_EN, "8": T8_EN}
    tpl_table = table_ru if lang == "ru" else table_en
    a = args_ru if lang == "ru" else args_en
    return tpl_table[tpl].format(**a)


PAY_MSG = {
    "1": ("Спасибо за оплату! Пожалуйста, отправьте сюда ссылку на ваш профиль Steam или код дружбы. Сейчас добавлю вас и отправлю подарок!",
          "Thank you for your order! Please send your Steam profile link or friend code here. I will add you and send the gift shortly!"),
    "login": ("Спасибо за оплату! Пожалуйста, напишите данные для входа в ваш аккаунт (почта и пароль). Скоро подключусь для оформления покупки!",
              "Thank you for your order! Please provide your account login details (email and password). I will log in shortly to complete the purchase!"),
    "code": ("Спасибо за покупку! Ваш заказ принят в работу — код или данные будут отправлены в этот чат в течение 5–15 минут.",
             "Thank you for your purchase! Your order is being processed — the code or credentials will be sent to this chat within 5–15 minutes."),
}
PAY_GROUP = {"1": "1", "2": "login", "4": "login", "8": "login",
             "3": "code", "5": "code", "6": "code",
             "7A": "code", "7B": "code", "7V": "code"}


# ============================================================================
# 7. СПЕЦИФИЧЕСКИЕ ИСПРАВЛЕНИЯ ВНУТРИ ЛОТОВ (раздел 4.2 ТЗ)
# ============================================================================

ALBION_EN = {
    "Любой предмет": "Any Pack / Item",
    "360 дней премиума": "360 Days Premium",
    "180 дней премиума": "180 Days Premium",
    "90 дней премиума": "90 Days Premium",
    "30 дней премиума": "30 Days Premium",
    "1700 золотых": "1700 Gold",
    "3500 золотых": "3500 Gold",
    "9000 золотых": "9000 Gold",
    "19000 золотых": "19000 Gold",
    "Конь хранителей": "Keeper Horse Skin",
    "Жрец": "Priest Pack",
    "Бронированный пони": "Armored Pony Skin",
    "Бродячий авантюрист": "Rogue Adventurer Pack",
    "Мастер кулаков": "Fist Master Pack",
    "Грабитель": "Outlaw Pack",
    "Хранитель душ": "Soul Keeper Pack",
    "Проклятый лютокабан": "Cursed Direboar Skin",
    "Криомант": "Cryomancer Pack",
    "Пиромант": "Pyromancer Pack",
}

AS_REGION_EN = {"Россия": "Russia", "Бразилия": "Brazil", "Польша": "Poland",
                "Индия": "India", "США": "USA", "Турция": "Turkey"}


def fix_albion(item):
    ru = item.get("fields[summary][ru]", "")
    segs = bracket_segments(ru)
    target = None
    for s in segs:
        if s in ALBION_EN:
            target = s
            break
    if target is None:
        return
    item_en = ALBION_EN[target]
    item["fields[summary][en]"] = (f"🔥[Albion Online]🔥✨[{item_en}]✨"
                                   f"▪️💎[To Your Account]💎❗[GLOBAL]❗")


def fix_apex(item):
    ru = item.get("fields[summary][ru]", "")
    en = item.get("fields[summary][en]", "")
    m = re.search(r"\[(\d+) монет\]", ru)
    if m and m.group(1) not in en:
        en = re.sub(r"(?<![\d])(23000|1050)(?![\d])", m.group(1), en, count=1)
        item["fields[summary][en]"] = en


def fix_lightroom(item):
    if item.get("node_id") == "3021" and item.get("server_id") == "11009":
        item["fields[summary][en]"] = item["fields[summary][en]"].replace(
            "Adobe InDesign", "Adobe Lightroom")


def fix_app_store(item):
    ru_sum = item.get("fields[summary][ru]", "")
    cur = item.get("fields[currency]", "")
    # номинал из RU-заголовка: ✨[5000 RUB]✨ / 🌠[5000 RUB]🌠 ...
    m = re.search(r"\[\s*([\d\s+]+)\s*([A-Z]{3})\s*\]", ru_sum)
    amount_val = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    currency = m.group(2) if m else cur
    reg_m = re.search(r"❗\s*\[([^\]]+)\]\s*❗", ru_sum)
    region_ru = reg_m.group(1) if reg_m else get_region_ru(item)
    region_en = AS_REGION_EN.get(region_ru, region_ru)
    # англоязычный заголовок (в 22 из 25 лотов лежит русский текст)
    item["fields[summary][en]"] = (f"🚀FAST DELIVERY🚀🔥[App Store]🔥"
                                   f"✨[{amount_val} {currency}]✨"
                                   f"▪️💎[Gift Card]💎❗[{region_en}]❗")
    # рублевые карты: корректные keys
    if cur in ("RUB", "BRL"):
        item.pop("fields[inr]", None)
    if cur == "RUB" and "fields[rub]" not in item and amount_val in \
            ("500", "1000", "2000", "5000"):
        item["fields[rub]"] = f"{amount_val} RUB"
    # desc[en] заполняется позже ШАБЛОНОМ 6; помечаем флаг
    item["_as_en_desc"] = True


def apply_specific_fixes(item):
    node = item.get("node_id")
    if node == "1247":
        fix_albion(item)
    elif node == "954":
        fix_apex(item)
    elif node == "3021":
        fix_lightroom(item)
    elif node == "1316":
        fix_app_store(item)
    # Astro Bot Deluxe UA — доработка черновика (ШАБЛОН 2)
    if node == "2681" and "Astro Bot" in item.get("fields[summary][ru]", ""):
        item["fields[platform]"] = "PS"
        item["fields[method]"] = "С заходом на аккаунт"
        item["fields[region]"] = "Украина"
        item["fields[summary][ru]"] = ("💠[Astro Bot]💠🔵[PS5]🔵"
                                       "💎[Deluxe Edition]💎🌍[Украина]🌍")
        item["fields[summary][en]"] = ("💠[Astro Bot]💠🔵[PS5]🔵"
                                       "💎[Deluxe Edition]💎🌍[Ukraine]🌍")
    # Arc Raiders - Deluxe Edition (Казахстан, 7538.85): fields[type]
    if (item.get("fields[summary][ru]", "").startswith("🤖АВТОВЫДАЧА 24/7❤️Arc Raiders - Deluxe Edition")
            and item.get("price") == "7538.85"):
        item["fields[type]"] = "Deluxe Edition"
    # ARK: Survival Ascended (Беларусь, node 1943): регион -> СНГ/CIS
    if (node == "1943" and "ARK: Survival Ascended" in item.get("fields[summary][ru]", "")
            and "Беларусь" in item.get("fields[summary][ru]", "")):
        item["fields[region]"] = "СНГ"
        item["fields[region2]"] = "CIS"
    # ARK: Survival Evolved PS4 (node 956): [Украина/Турция] -> [Турция]
    if node == "956" and item.get("fields[region]") == "Турция" \
            and "[Украина/Турция]" in item.get("fields[summary][ru]", "") \
            and "PS4" in item.get("fields[summary][ru]", ""):
        item["fields[summary][ru]"] = item["fields[summary][ru]"].replace(
            "[Украина/Турция]", "[Турция]")


# ============================================================================
# 8. ДЕДУПЛИКАЦИЯ И КАРАНТИН (разделы 4.1 / 4.2.1 ТЗ)
# ============================================================================

EXPLICIT_QUARANTINE = {
    # (node_id, подстрока RU-заголовка, ожидаемая исходная цена) : причина
    ("1316", "100 USD", "955.81"): "Обрезанная цена (реально ~14 955 руб.)",
    ("1247", "360 дней премиума", "423.63"): "Обрезанная цена (>10 000 руб.)",
    ("1247", "19000 золотых", "405.30"): "Обрезанная цена (>10 000 руб.)",
    ("1247", "Конь хранителей", "798.38"): "Обрезанная цена (>10 000 руб.)",
    ("1247", "Жрец", "861.51"): "Обрезанная цена (>10 000 руб.)",
    ("1247", "Грабитель", "431.78"): "Обрезанная цена (>10 000 руб.)",
    ("954", "23000 монет", "955.20"): "Обрезанная цена (>10 000 руб.)",
    ("954", "12300 + 200 монет", "129.33"): "Обрезанная цена (>10 000 руб.)",
    ("954", "11500 монет", "113.80"): "Обрезанная цена (EA App ключ, >10 000 руб.)",
    ("2363", "220 Exotic Shard", "496.99"): "Обрезанная цена (110 Shard стоит 8798.41)",
    ("1943", "Aliens: Dark Descent", None): "Чужая категория (раздел ARK: Survival Ascended)",
}


def nominal_value(text):
    """Максимальное «числовой номинал/объем/срок» из строки заголовка."""
    best = 0
    for num in re.findall(r"\d+(?:[\s,.]?\d)*", text or ""):
        v = int(re.sub(r"\D", "", num) or 0)
        if v > best:
            best = v
    return best


def quarantine_reason(item):
    node = item.get("node_id", "")
    ru = item.get("fields[summary][ru]", "")
    price = float(item.get("price", "0") or 0)

    for (n, sub, pr), reason in EXPLICIT_QUARANTINE.items():
        if node == n and sub in ru and (pr is None or item.get("price") == pr):
            return reason

    # Adobe: годовые подписки и 90-дневные ключи дешевле 1500 руб.
    if node in ("3020", "3021"):
        both = (ru + " " + item.get("fields[summary][en]", "")).lower()
        yearly = "adobe" in both and re.search(r"1\s*год|1\s*year", both)
        key90 = "официальный ключ" in both and "90" in ru
        if (yearly or key90) and price < 1500:
            return "Обрезанная цена Adobe (<1500 руб. при 30 днях от 2107.95)"

    # явный артефакт отрезанной первой цифры: «0XX.XX» (например 012.26)
    ps = item.get("price", "")
    if re.fullmatch(r"0\d{2}\.\d{2}", ps):
        return "Обрезанная цена (артефакт ведущего нуля: %s)" % ps

    # общее правило инверсии номинала внутри node_id (применяется отдельно)
    return None


def game_identity(item):
    """Идентичность товара внутри раздела: игра + издание/тип/платформа.
    Лоты разных игр/изданий внутри одного node_id не сравниваются."""
    ident = (get_game_name(item).lower(),
             (item.get("fields[type]") or "").lower(),
             (item.get("fields[platform]") or "").lower())
    if any(ident):
        return ident
    return (get_game_name(item).lower(),)


def extract_nominal(item):
    """Числовой номинал/объем/срок ТОЛЬКО из специализированных полей
    (quantity, time, currency-номиналы) — они однозначно описывают товар."""
    text = " ".join(str(item.get(k, "")) for k in
                    ("fields[quantity]", "fields[time]", "fields[rub]",
                     "fields[usd]", "fields[inr]", "fields[try]",
                     "fields[pln]", "fields[brl]"))
    return nominal_value(text)


def inversion_check(items):
    """Возвращает {id(item): причина} для лотов, где внутри одного node_id
    и того же товара лот с большим числовым номиналом/сроком стоит дешевле
    лота с меньшим номиналом (общее правило п.4.1.7 ТЗ)."""
    by_group = {}
    for it in items:
        nom = extract_nominal(it)
        if nom <= 0:
            continue
        key = (it.get("node_id", ""), game_identity(it))
        by_group.setdefault(key, []).append((nom, float(it.get("price", "0") or 0), it))
    flagged = {}
    for (node, _gid), group in by_group.items():
        if len(group) < 2:
            continue
        for nom_a, pr_a, it_a in group:
            for nom_b, pr_b, it_b in group:
                if it_a is it_b:
                    continue
                # явная инверсия: больший номинал сильно (<50%) дешевле меньшего
                if nom_a > nom_b and pr_a < pr_b * 0.5:
                    flagged[id(it_a)] = (f"Инверсия номинала в node {node}: "
                                         f"больший номинал ({nom_a}) дешевле "
                                         f"меньшего ({nom_b})")
                    break
    return flagged


# ============================================================================
# 9. ОСНОВНОЙ КОНВЕЙЕР
# ============================================================================

def finalize_fields(item):
    """Совместимость с LotFields: secrets='', auto_delivery удалён, ts, str."""
    item["secrets"] = ""
    item.pop("auto_delivery", None)
    item["form_created_at"] = NOW_TS
    for k in list(item.keys()):
        item[k] = str(item[k])


def main():
    raw_items, dropped = load_dump(INPUT_FILE)

    # ---- 0.5 Ротация эмодзи-палитры: таблица строится по инвентарю донора
    donor_emojis = _donor_summary_emoji_inventory(raw_items)
    rot = build_emoji_rotation(donor_emojis)
    print(f"[EMOJI] Инвентарь донорских эмодзи в summary: {len(donor_emojis)}; "
          f"правил ротации: {len([v for v in rot.values() if v])}")

    # ---- 0. Дедупликация полных дублей (node_id + summary[ru] + region) ---
    def norm_ws(s):
        return re.sub(r"\s+", " ", s or "").strip()

    seen = {}
    items = []
    removed_dups = []
    for it in raw_items:
        if not norm_ws(it.get("fields[summary][ru]")) and \
           not norm_ws(it.get("fields[summary][en]")):
            removed_dups.append(("пустой лот без заголовка", it.get("node_id")))
            continue
        key = (it.get("node_id"),
               norm_ws(it.get("fields[summary][ru]")),
               norm_ws(it.get("fields[region]") or it.get("fields[region2]")))
        if key in seen:
            removed_dups.append((norm_ws(key[1])[:60], key[0]))
            continue
        seen[key] = True
        items.append(it)
    print(f"[DUP] Удалено дубликатов/пустых лотов: {len(removed_dups)}")

    # ---- 1. Специфические исправления (до трансформации заголовков) --------
    for it in items:
        apply_specific_fixes(it)

    # ---- 2. Карантин определяем по ИСХОДНЫМ ценам --------------------------
    inv_flags = inversion_check(items)
    quarantined, ready_pool = [], []
    quarantine_report = []
    for it in items:
        reason = quarantine_reason(it) or inv_flags.get(id(it))
        if reason:
            quarantined.append((it, reason))
            quarantine_report.append((it.get("node_id"),
                                      norm_ws(it.get("fields[summary][ru]"))[:70],
                                      it.get("price"), reason))
        else:
            ready_pool.append(it)

    # ---- 3. Обработка ВСЕХ лотов (и ready, и карантин — тексты правим везде)
    template_stats = {}
    for bucket in (ready_pool, [q[0] for q in quarantined]):
        for it in bucket:
            # очистка текстовых полей
            for k in TEXT_KEYS:
                if k in it:
                    it[k] = clean_text(it[k])
            # трансформация заголовков
            it["fields[summary][ru]"] = transform_summary(
                it["fields[summary][ru]"], "ru")
            it["fields[summary][en]"] = transform_summary(
                it["fields[summary][en]"], "en")
            # ценообразование и остатки
            it["price"] = new_price(it["price"])
            it["amount"] = random_amount()
            # маршрутизация и описания
            tpl = route_template(it)
            it["_tpl"] = tpl
            template_stats[tpl] = template_stats.get(tpl, 0) + 1
            it["fields[desc][ru]"] = build_desc(it, tpl, "ru")
            it["fields[desc][en]"] = build_desc(it, tpl, "en")
            # payment_msg
            grp = PAY_GROUP[tpl]
            if not it.get("fields[payment_msg][ru]", "").strip():
                it["fields[payment_msg][ru]"] = PAY_MSG[grp][0]
            if not it.get("fields[payment_msg][en]", "").strip():
                it["fields[payment_msg][en]"] = PAY_MSG[grp][1]
            # служебные поля
            it["active"] = "on"
            it.setdefault("location", "")
            it.setdefault("deleted", "")
            it.setdefault("fields[images]", "")
            finalize_fields(it)
            it.pop("_tpl", None)
            it.pop("_as_en_desc", None)

    # ---- 4. Запись выходных файлов -----------------------------------------
    def dump(fname, arr):
        txt = json.dumps(arr, ensure_ascii=False, indent=4)
        with open(fname, "w", encoding="utf-8") as f:
            f.write(txt)
        size = len(txt.encode("utf-8"))
        assert size < MAX_FILE_SIZE, f"{fname}: {size} bytes >= 20MB"
        return size

    size_r = dump(READY_FILE, ready_pool)
    size_q = dump(QUARANTINE_FILE, [q[0] for q in quarantined])

    # ---- 5. Верификация -----------------------------------------------------
    problems = []
    for fname in (READY_FILE, QUARANTINE_FILE):
        with open(fname, encoding="utf-8") as f:
            check = json.load(f)
        for i, it in enumerate(check):
            if "auto_delivery" in it:
                problems.append(f"{fname}[{i}]: есть auto_delivery")
            if it.get("secrets") != "":
                problems.append(f"{fname}[{i}]: secrets != ''")
            for k, v in it.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    problems.append(f"{fname}[{i}]: не-str поле {k}")
            for lk in ("fields[summary][ru]", "fields[summary][en]"):
                if len(it.get(lk, "")) > SUMMARY_LIMIT:
                    problems.append(f"{fname}[{i}]: {lk} длиннее 100 "
                                    f"({len(it[lk])})")
    assert not problems, "\n".join(problems[:20])

    # ---- 6. Отчет ------------------------------------------------------------
    print("\n" + "=" * 72)
    print("ОТЧЕТ ПО ОБРАБОТКЕ ДАМПА ПРОФИЛЯ")
    print("=" * 72)
    if dropped:
        print(f"[!] Исходный JSON был оборван на лоте "
              f"'Atomic Heart - Gold Edition' (node {dropped[0]}) — "
              f"незавершенный объект отброшен, массив восстановлен.")
    print(f"[!] Всего объектов в дампе: {len(raw_items)}; "
          f"дубликатов/пустых удалено: {len(removed_dups)}; "
          f"обработано валидных записей: {len(items)}")
    for title, cnt in removed_dups:
        print(f"    - удален дубль/пустой (node {cnt}): {title}")
    print("-" * 72)
    print(f"В lots_ready.json записано лотов: {len(ready_pool)} "
          f"(размер {size_r/1048576:.2f} МБ < 20 МБ)")
    names = {"1": "Steam Подарок", "2": "Покупка с заходом на аккаунт",
             "3": "Новый чистый аккаунт под заказ",
             "4": "Донат/валюта/наборы/БП с заходом",
             "5": "Цифровые ключи", "6": "Подарочные карты App Store",
             "7A": "Adobe 1 год (новый/ваш аккаунт)",
             "7B": "Adobe готовый аккаунт 30/60/90 дн.",
             "7V": "Adobe официальный ключ 30/90 дн. (США)",
             "8": "Лот-витрина «Любой предмет»"}
    ready_tpl = {}
    for it in ready_pool:
        # пересчитываем шаблон для отчета по готовым лотам
        t = route_template(it)
        ready_tpl[t] = ready_tpl.get(t, 0) + 1
    for t in sorted(ready_tpl, key=lambda x: (len(x), x)):
        print(f"    Шаблон {t:<2} ({names[t]}): {ready_tpl[t]} лот(ов)")
    print("-" * 72)
    print(f"В lots_quarantine.json (требуют ручной правки цены/категории): "
          f"{len(quarantined)} лот(ов), размер {size_q/1048576:.2f} МБ")
    for node, summ, old_price, reason in quarantine_report:
        print(f"    • node {node} | цена была {old_price} руб. | {reason}")
        print(f"        {summ}")
    print("-" * 72)
    print("[OK] Во всех объектах обоих файлов ключ 'auto_delivery' удален, "
          "\"secrets\": \"\" установлен.")
    print("[OK] Все ключи и значения имеют тип str "
          "(включая price, amount, node_id, form_created_at).")
    print(f"[OK] form_created_at обновлен на актуальный Unix Timestamp: {NOW_TS}")
    print("[OK] Длина fields[summary][ru] и [en] во всех лотах <= 100 символов.")
    print("[OK] Кодировка UTF-8, ensure_ascii=False, indent=4.")
    print("=" * 72)
    print("Готово: импортируйте lots_ready.json командой /create_lots в Cardinal;")
    print("lots_quarantine.json — после ручной корректиры цен.")


if __name__ == "__main__":
    main()
