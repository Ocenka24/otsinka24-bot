#!/usr/bin/env python3
"""ОЦІНКА24 — Telegram Bot v5.3 | Google Maps + Адмін-панель + БД"""

import asyncio, logging, os, uuid
from datetime import datetime
from io import BytesIO

import warnings
warnings.filterwarnings("ignore", category=UserWarning)

import asyncpg

try:
    import googlemaps as _googlemaps
    _gmaps_available = True
except ImportError:
    _gmaps_available = False

from dotenv import load_dotenv
load_dotenv()

from PIL import Image, ImageDraw, ImageFont, ImageOps
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ConversationHandler, ContextTypes, MessageHandler, filters,
)

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Конфіг ────────────────────────────────────────────────
BOT_TOKEN          = os.getenv("BOT_TOKEN", "")
ADMIN_IDS          = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
CHANNEL_ID         = int(os.getenv("CHANNEL_ID", "0"))
DATABASE_URL       = os.getenv("DATABASE_URL")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")

gmaps = None
if _gmaps_available and GOOGLE_MAPS_API_KEY:
    try:
        gmaps = _googlemaps.Client(key=GOOGLE_MAPS_API_KEY)
    except Exception as _e:
        logging.warning(f"Google Maps Client init failed: {_e} — геокодування через Nominatim")

WEBSITE     = "https://ocenka24.com.ua/"
EMAIL       = "info@ocenka24.com.ua"
PHONE1      = "0 800 502-977"
PHONE2      = "+38 (050) 3000-173"
PHONE2_RAW  = "+380503000173"   # для посилань tel:
_mgr_raw    = os.getenv("MANAGER_TG", "")
# Нормалізуємо до повного URL (приймаємо і username, і https://t.me/...)
if _mgr_raw.startswith("http"):
    MANAGER_TG_URL = _mgr_raw
elif _mgr_raw:
    MANAGER_TG_URL = f"https://t.me/{_mgr_raw.lstrip('@')}"
else:
    MANAGER_TG_URL = ""
LOGO        = "https://ocenka24.com.ua/img/ocenka24-logo.png"

assert BOT_TOKEN, "BOT_TOKEN відсутній у .env"

# ── Стани ─────────────────────────────────────────────────
MENU, UPLOAD, LOC, VIDEOLOC, PHOTOGPS, PHONE, ADMIN, COMMENT, DELIVERY = range(9)

# ══════════════════════════════════════════════════════════
# ІНІЦІАЛІЗАЦІЯ БАЗИ
# ══════════════════════════════════════════════════════════
async def init_db():
    if not DATABASE_URL:
        logger.warning("DATABASE_URL не задано — БД не ініціалізована")
        return
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS requests (
            id SERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(user_id),
            request_type TEXT NOT NULL,
            status TEXT DEFAULT 'new',
            address TEXT,
            lat DOUBLE PRECISION,
            lon DOUBLE PRECISION,
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS request_files (
            id SERIAL PRIMARY KEY,
            request_id INTEGER REFERENCES requests(id),
            file_id TEXT NOT NULL,
            file_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    await _db_release(conn)
    logger.info("✅ База даних ініціалізована")


async def _save_user(user_id: int, username: str, full_name: str, phone: str):
    if not DATABASE_URL:
        return
    conn = None
    try:
        conn = await _db_connect()
        await conn.execute('''
            INSERT INTO users (user_id, username, full_name, phone)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE SET phone=$4, username=$2, full_name=$3
        ''', user_id, username, full_name, phone)
    except Exception as e:
        logger.warning(f"DB save_user error: {e}")
    finally:
        if conn:
            await _db_release(conn)


# ── Об'єкти оцінки ────────────────────────────────────────
OBJECTS = {
    "car": ("🚗","Оцінка транспортного засобу",[
        "📋 Технічний паспорт (свідоцтво про реєстрацію)",
        "🪪 Документ що посвідчує особу",
        "📸 Фото ТЗ ззовні з 4 кутів",
        "📸 Фото салону, пробігу та VIN-коду",
    ]),
    "flat": ("🏠","Оцінка квартири",[
        "📜 Правовстановлюючий документ",
        "📋 Технічний паспорт",
        "🪪 Документ що посвідчує особу",
        "📸 Фото кімнат, кухні, санвузлу",
    ]),
    "house": ("🏡","Оцінка житлового будинку",[
        "📜 Правовстановлюючий документ на будинок",
        "📋 Технічний паспорт",
        "📜 Правовстановлюючий документ на землю",
        "🪪 Документ що посвідчує особу",
        "📸 Фото будинку ззовні з 4 кутів та всередині",
    ]),
    "land": ("🌿","Оцінка земельної ділянки",[
        "📜 Правовстановлюючий документ на землю",
        "🪪 Документ що посвідчує особу",
        "📸 Фото ділянки (4-6 штук)",
    ]),
    "nonres": ("🏭","Оцінка нежитлової будівлі/споруди",[
        "📜 Правовстановлюючий документ",
        "📋 Технічний паспорт",
        "🪪 Документ що посвідчує особу / юридичну особу",
        "📸 Фото будівлі ззовні з 4 кутів та всередині",
    ]),
}


# ══════════════════════════════════════════════════════════
#  КЛАВІАТУРИ
# ══════════════════════════════════════════════════════════

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚗 Оцінка авто",          callback_data="obj_car")],
        [InlineKeyboardButton("🏠 Оцінка квартири",      callback_data="obj_flat")],
        [InlineKeyboardButton("🏡 Оцінка будинку",       callback_data="obj_house")],
        [InlineKeyboardButton("🌿 Оцінка землі",         callback_data="obj_land")],
        [InlineKeyboardButton("🏭 Нежитлова нерухомість",callback_data="obj_nonres")],
        [InlineKeyboardButton("📍 Геолокація",           callback_data="location")],
        [InlineKeyboardButton("ℹ️ Про компанію", callback_data="about"),
         InlineKeyboardButton("📞 Контакти",     callback_data="contact")],
    ])

def object_kb():
    """Клавіатура всередині заявки на оцінку."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Фото з GPS",            callback_data="obj_photogps")],
        [InlineKeyboardButton("📹 Відеоогляд",            callback_data="obj_video")],
        [InlineKeyboardButton("✅ Завершити надсилання документів", callback_data="done")],
        [InlineKeyboardButton("🏠 Головне меню",          callback_data="home")],
    ])

def upload_kb():
    return object_kb()

def gps_kb(label="📍 Поділитися геолокацією"):
    return ReplyKeyboardMarkup(
        [[KeyboardButton(label, request_location=True)]],
        one_time_keyboard=True, resize_keyboard=True)

def home_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Головне меню", callback_data="home")]])

def admin_main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Активні заявки",  callback_data="adm|active")],
        [InlineKeyboardButton("📊 Всі заявки",      callback_data="adm|all")],
        [InlineKeyboardButton("📈 Статистика",      callback_data="adm|stats")],
        [InlineKeyboardButton("👥 Клієнти",         callback_data="adm|clients")],
        [InlineKeyboardButton("🏠 Головне меню",    callback_data="home")],
    ])

# залишаємо alias для сумісності
def admin_kb():
    return admin_main_kb()


_STATUS_LABELS = {
    "new":         "🆕 Нова",
    "in_progress": "🔄 В роботі",
    "done":        "✅ Виконано",
    "rejected":    "❌ Відхилено",
}

def _status_kb(req_id: int, back: str = "adm|active") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Виконано",    callback_data=f"st|{req_id}|done")],
        [InlineKeyboardButton("🔄 В роботі",   callback_data=f"st|{req_id}|in_progress")],
        [InlineKeyboardButton("❌ Відхилено",   callback_data=f"st|{req_id}|rejected")],
        [InlineKeyboardButton("🆕 Нова",        callback_data=f"st|{req_id}|new")],
        [InlineKeyboardButton("← Назад",        callback_data=back)],
    ])



# ══════════════════════════════════════════════════════════
#  РОЗСИЛКА
# ══════════════════════════════════════════════════════════

def _targets():
    t = list(ADMIN_IDS)
    if CHANNEL_ID: t.append(CHANNEL_ID)
    return list(set(t))

async def notify(ctx, text):
    for tid in _targets():
        try: await ctx.bot.send_message(tid, text, parse_mode="Markdown")
        except Exception as e: logger.warning(f"notify {tid}: {e}")

async def notify_photo(ctx, data, caption):
    for tid in _targets():
        try:
            if isinstance(data, BytesIO): data.seek(0)
            await ctx.bot.send_photo(tid, data, caption=caption, parse_mode="Markdown")
        except Exception as e: logger.warning(f"notify_photo {tid}: {e}")

async def notify_doc(ctx, fid, caption):
    for tid in _targets():
        try: await ctx.bot.send_document(tid, fid, caption=caption, parse_mode="Markdown")
        except Exception as e: logger.warning(f"notify_doc {tid}: {e}")

async def notify_loc(ctx, lat, lon):
    for tid in _targets():
        try: await ctx.bot.send_location(tid, lat, lon)
        except Exception as e: logger.warning(f"notify_loc {tid}: {e}")


# ══════════════════════════════════════════════════════════
#  ФОТО+GPS: ОБРОБКА ЗОБРАЖЕНЬ
# ══════════════════════════════════════════════════════════

_FONT_CACHE: dict[int, ImageFont.FreeTypeFont] = {}
_FONT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "font_cyr.ttf")

# Шляхи до шрифтів з підтримкою кирилиці
_FONT_PATHS = [
    _FONT_FILE,
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "arial.ttf",
]


def _ensure_cyrillic_font_sync():
    """Завантажує шрифт з підтримкою кирилиці якщо жоден не знайдено (синхронно)."""
    for p in _FONT_PATHS[1:]:
        if os.path.exists(p):
            logger.info(f"Шрифт знайдено: {p}")
            return
    if os.path.exists(_FONT_FILE):
        return
    logger.info("Завантажую шрифт з підтримкою кирилиці...")
    import urllib.request
    urls = [
        "https://github.com/liberationfonts/liberation-fonts/raw/main/Liberation-fonts-ttf-2.1.5/LiberationSans-Bold.ttf",
        "https://github.com/notofonts/noto-fonts/raw/main/hinted/ttf/NotoSans/NotoSans-Bold.ttf",
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Otsinka24Bot/5.3"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            with open(_FONT_FILE, "wb") as f:
                f.write(data)
            logger.info(f"✅ Шрифт завантажено: {url.split('/')[-1]}")
            return
        except Exception as e:
            logger.warning(f"Шрифт {url.split('/')[-1]}: {e}")
    logger.warning("⚠️ Шрифт не завантажено — кирилиця може не відображатися")


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    for p in _FONT_PATHS:
        try:
            f = ImageFont.truetype(p, size)
            _FONT_CACHE[size] = f
            return f
        except Exception:
            pass
    return ImageFont.load_default()


def _text_h(draw: ImageDraw.Draw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]


def _text_w(draw: ImageDraw.Draw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


def _wrap_text(draw: ImageDraw.Draw, text: str, font, max_width: int) -> list[str]:
    """Розбиває текст на рядки щоб вмістити у max_width пікселів."""
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        if _text_w(draw, test, font) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


async def _fetch_async(url: str) -> bytes | None:
    loop = asyncio.get_running_loop()
    def _sync():
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Otsinka24Bot/5.3"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read()
    try:
        return await loop.run_in_executor(None, _sync)
    except Exception as e:
        logger.warning(f"Fetch {url[:60]}: {e}")
        return None


def _get_exif_gps(image: Image.Image):
    """Витягує GPS з EXIF через сучасний API Pillow."""
    try:
        from PIL.ExifTags import TAGS, GPSTAGS
        exif = image.getexif()
        if not exif:
            return None, None
        gps_ifd = exif.get_ifd(0x8825)   # GPSInfo IFD
        if not gps_ifd:
            return None, None
        gps = {GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}
        if "GPSLatitude" not in gps:
            return None, None
        def to_deg(v): return float(v[0]) + float(v[1])/60 + float(v[2])/3600
        lat = to_deg(gps["GPSLatitude"])
        lon = to_deg(gps["GPSLongitude"])
        if gps.get("GPSLatitudeRef")  == "S": lat = -lat
        if gps.get("GPSLongitudeRef") == "W": lon = -lon
        return round(lat, 7), round(lon, 7)
    except Exception as e:
        logger.warning(f"EXIF GPS: {e}")
        return None, None


async def _get_address(lat: float, lon: float) -> str:
    """Пріоритет: Google Maps → Nominatim → BigDataCloud."""
    import json

    # 1. Google Maps
    if gmaps:
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, lambda: gmaps.reverse_geocode((lat, lon), language="uk")
            )
            if result:
                addr = result[0]["formatted_address"]
                logger.info(f"Google Maps address: {addr}")
                return addr
        except Exception as e:
            logger.warning(f"Google Maps geocoding error: {e}")

    # 2. Nominatim (найточніший безкоштовний)
    url = (f"https://nominatim.openstreetmap.org/reverse"
           f"?lat={lat}&lon={lon}&format=json&accept-language=uk&zoom=18"
           f"&addressdetails=1")
    data = await _fetch_async(url)
    if data:
        try:
            j = json.loads(data)
            addr = j.get("display_name", "")
            if addr:
                logger.info(f"Nominatim address: {addr[:80]}")
                return addr
        except Exception as e:
            logger.warning(f"Nominatim parse error: {e}")

    # 3. BigDataCloud (запасний)
    url = (f"https://api.bigdatacloud.net/data/reverse-geocode-client"
           f"?latitude={lat}&longitude={lon}&localityLanguage=uk")
    data = await _fetch_async(url)
    if data:
        try:
            j = json.loads(data)
            parts = filter(None, [
                j.get("principalSubdivision", ""),
                j.get("city", "") or j.get("locality", ""),
                j.get("countryName", ""),
            ])
            addr = ", ".join(parts)
            if addr.strip(", "):
                logger.info(f"BigDataCloud address: {addr}")
                return addr
        except Exception as e:
            logger.warning(f"BigDataCloud parse error: {e}")

    logger.warning(f"All geocoding failed for {lat},{lon}")
    return ""


async def _get_map(lat: float, lon: float, px: int = 450) -> Image.Image | None:
    """Статична карта з OpenStreetMap з маркером."""
    for zoom in (17, 16, 15):
        url = (
            f"https://staticmap.openstreetmap.de/staticmap.php"
            f"?center={lat},{lon}&zoom={zoom}&size={px}x{px}"
            f"&markers={lat},{lon},red-pushpin"
        )
        data = await _fetch_async(url)
        if data:
            try:
                return Image.open(BytesIO(data)).convert("RGB")
            except Exception:
                continue
    return None


def _draw_text_shadow(draw, pos, text, font, fill, shadow=(0, 0, 0, 200), offset=3):
    """Малює текст з тінню для кращої читабельності."""
    x, y = pos
    draw.text((x + offset, y + offset), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


async def build_geotagged_photo(
    photo_bytes: bytes,
    lat: float | None = None,
    lon: float | None = None,
    address: str = "",
    ts: str = "",
) -> BytesIO:
    """
    Накладає на фото:
      • Карта розташування (ліворуч вгорі, ~40% ширини)
      • Темна панель знизу: координати GPS + адреса
      • ОЦІНКА24 (золотий, правий верхній кут)
      Дата/час НЕ відображається.
      Шрифти зменшені на 40% відносно попередньої версії.
    """
    img = Image.open(BytesIO(photo_bytes))
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGBA")
    W, H = img.size

    # Розміри шрифтів ~5% від розміру фото (зменшено на 50%)
    base = max(int(H * 0.013), 8)
    pad  = max(4, base // 3)

    f_brand = _load_font(base * 2)
    f_label = _load_font(int(base * 1.2))
    f_gps   = _load_font(int(base * 1.6))
    f_addr  = _load_font(int(base * 1.4))
    f_site  = _load_font(base)

    tmp_draw = ImageDraw.Draw(img)

    # ── Карта ─────────────────────────────────────────────
    map_img = None
    map_px = min(int(W * 0.38), 400)
    if lat and lon:
        map_img = await _get_map(lat, lon, map_px)

    # ── Висота нижньої панелі ─────────────────────────────
    lh_label = _text_h(tmp_draw, "К", f_label) + 4
    lh_gps   = _text_h(tmp_draw, "0", f_gps)   + 8
    lh_addr  = _text_h(tmp_draw, "А", f_addr)  + 6
    lh_site  = _text_h(tmp_draw, "0", f_site)  + 4

    text_max_w = W - pad * 2
    addr_display = address if address else ""
    addr_lines = _wrap_text(tmp_draw, addr_display, f_addr, text_max_w) if addr_display else []

    panel_h = (pad
               + lh_label + lh_gps + pad // 2
               + (lh_label + lh_addr * len(addr_lines) + pad // 2 if addr_lines else 0)
               + lh_site + pad)

    # ── Напівпрозора панель знизу ─────────────────────────
    panel_top = H - panel_h
    overlay = Image.new("RGBA", (W, panel_h), (8, 12, 35, 220))
    img.paste(overlay, (0, panel_top), overlay)
    sep = Image.new("RGBA", (W, 4), (255, 215, 0, 210))
    img.paste(sep, (0, panel_top), sep)

    draw = ImageDraw.Draw(img)

    # ── ОЦІНКА24 — правий верхній кут ────────────────────
    brand = "ОЦІНКА24"
    bw = _text_w(draw, brand, f_brand)
    _draw_text_shadow(draw, (W - bw - pad, pad), brand, f_brand,
                      fill=(255, 215, 0, 255), shadow=(0, 0, 0, 200), offset=3)

    # ── Карта — лівий верхній кут (під написом бренду) ───
    brand_h = _text_h(draw, brand, f_brand)
    if map_img:
        mw, mh = map_img.size
        border = 5
        frame = Image.new("RGBA", (mw + border * 2, mh + border * 2), (255, 215, 0, 255))
        frame.paste(map_img, (border, border))
        map_y = pad + brand_h + pad
        if map_y + mh + border * 2 < panel_top - pad:
            img.paste(frame, (pad, map_y))

    # ── Вміст панелі ──────────────────────────────────────
    tx = pad
    y = panel_top + pad

    # Координати
    _draw_text_shadow(draw, (tx, y), "КООРДИНАТИ:", f_label,
                      fill=(255, 215, 0, 190), shadow=(0, 0, 0, 140))
    y += lh_label
    gps_text = f"{lat:.6f},  {lon:.6f}" if (lat and lon) else "—"
    _draw_text_shadow(draw, (tx, y), gps_text, f_gps,
                      fill=(255, 255, 255, 255), shadow=(0, 0, 0, 200), offset=2)
    y += lh_gps + pad // 2

    # Адреса
    if addr_lines:
        _draw_text_shadow(draw, (tx, y), "АДРЕСА:", f_label,
                          fill=(255, 215, 0, 190), shadow=(0, 0, 0, 140))
        y += lh_label
        for line in addr_lines:
            _draw_text_shadow(draw, (tx, y), line, f_addr,
                              fill=(160, 220, 255, 255), shadow=(0, 0, 0, 200), offset=2)
            y += lh_addr
        y += pad // 2

    # Сайт — справа внизу панелі
    site_w = _text_w(draw, WEBSITE, f_site)
    _draw_text_shadow(draw, (W - site_w - pad, y), WEBSITE, f_site,
                      fill=(180, 180, 255, 200), shadow=(0, 0, 0, 150))

    # ── Зберігаємо ────────────────────────────────────────
    out = BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=94, optimize=True)
    out.seek(0)
    return out


# ══════════════════════════════════════════════════════════
#  /start  та  /cancel
# ══════════════════════════════════════════════════════════

def _start_kb() -> InlineKeyboardMarkup:
    """Стартова клавіатура — до надання телефону."""
    rows = [
        [InlineKeyboardButton("📋 Замовити оцінку",       callback_data="pre_order")],
        [InlineKeyboardButton("ℹ️ Умови та вартість",     callback_data="pre_info")],
        [InlineKeyboardButton("💬 Написати менеджеру",    callback_data="pre_write")],
    ]
    if MANAGER_TG_URL:
        rows.append([InlineKeyboardButton("📲 Написати в Telegram", url=MANAGER_TG_URL)])
    rows.append([InlineKeyboardButton("ℹ️ Про компанію", callback_data="about"),
                 InlineKeyboardButton("📞 Контакти",     callback_data="contact")])
    return InlineKeyboardMarkup(rows)


async def cmd_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    u = upd.effective_user
    name = u.first_name or "Друже"

    text = (
        f"👋 Вітаємо, {name}!\n\n"
        "🏢 ОЦІНКА24 — професійна оцінка майна по всій Україні.\n\n"
        "✅ Сертифіковані оцінювачі\n"
        "✅ Досвід понад 10 років\n"
        "✅ Звіти для банків, нотаріусів, судів\n\n"
        f"📞 {PHONE1}\n"
        f"📱 {PHONE2}\n"
        f"🌐 {WEBSITE}"
    )
    kb = _start_kb()

    sent = False
    try:
        await upd.message.reply_photo(photo=LOGO, caption=text, reply_markup=kb)
        sent = True
    except Exception as e:
        logger.warning(f"reply_photo failed: {e}")

    if not sent:
        try:
            await upd.message.reply_text(text, reply_markup=kb)
            sent = True
        except Exception as e:
            logger.warning(f"reply_text failed: {e}")

    if not sent:
        # абсолютний фолбек — просто текст без клавіатури
        try:
            await upd.message.reply_text("👋 Вітаємо! Бот ОЦІНКА24 готовий до роботи.")
        except Exception:
            pass

    return MENU


async def cmd_cancel(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await upd.message.reply_text("❌ Скасовано.", reply_markup=ReplyKeyboardRemove())
    await upd.message.reply_text("🏠 Головне меню:", reply_markup=main_kb())
    return MENU


# ══════════════════════════════════════════════════════════
#  ГОЛОВНЕ МЕНЮ
# ══════════════════════════════════════════════════════════

async def handle_phone(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Обробка номера телефону + відправка адмінам."""
    msg = upd.message
    u   = msg.from_user
    ts  = datetime.now().strftime("%d.%m.%Y %H:%M")

    if msg.contact:
        phone = msg.contact.phone_number
        if not phone.startswith("+"): phone = "+" + phone
    elif msg.text and (msg.text.startswith("+") or msg.text.startswith("0")):
        phone = msg.text.strip()
    else:
        await msg.reply_text("⚠️ Введіть номер у форматі +380XXXXXXXXX")
        return PHONE

    ctx.user_data["phone"] = phone
    logger.info(f"Телефон клієнта {u.id}: {phone}")

    await _save_user(u.id, u.username, u.full_name, phone)

    # Сповіщаємо адміністраторів
    await notify(ctx,
        f"📱 *НОВИЙ КЛІЄНТ*\n"
        f"{'─'*28}\n"
        f"👤 *{u.full_name}*\n"
        f"🆔 `{u.id}` | @{u.username or '—'}\n"
        f"📱 `{phone}`\n"
        f"🕐 {ts}")

    await msg.reply_text(
        f"✅ Дякуємо, *{u.first_name}*! Номер збережено.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove())

    # Якщо клієнт вибирав об'єкт до надання телефону — відкриваємо його одразу
    pending = ctx.user_data.pop("pending_obj", None)
    if pending and pending in OBJECTS:
        icon, name, docs = OBJECTS[pending]
        ctx.user_data.update({"obj_key": pending, "obj_name": f"{icon} {name}", "files": []})
        doc_list = "\n".join(f"  {i+1}. {d}" for i, d in enumerate(docs))
        await ctx.bot.send_message(
            upd.effective_chat.id,
            f"{icon} *{name}*\n\n"
            f"📋 *Необхідні документи:*\n{doc_list}\n\n"
            "Надсилайте фото та документи по одному.\n"
            "Після завершення натисніть *«✅ Завершити надсилання документів»*.",
            parse_mode="Markdown", reply_markup=object_kb())
        return UPLOAD

    await ctx.bot.send_message(upd.effective_chat.id,
        "🏠 Головне меню:", reply_markup=main_kb())
    return MENU


async def on_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = upd.callback_query
    await q.answer()
    d = q.data

    async def send(text, kb=None, md=True):
        try: await q.edit_message_reply_markup(reply_markup=None)
        except: pass
        kw = {"parse_mode":"Markdown"} if md else {}
        await ctx.bot.send_message(upd.effective_chat.id, text, reply_markup=kb, **kw)

    if d == "home":
        # Якщо телефон вже є — головне меню, інакше стартовий екран
        if ctx.user_data.get("phone"):
            await send("🏠 Головне меню:", main_kb())
        else:
            await send(
                f"🏢 *ОЦІНКА24*\n\n☎️ {PHONE1}\n📱 {PHONE2}\n🌐 {WEBSITE}",
                _start_kb())
        return MENU

    # ── Стартовий екран — дії без телефону ───────────────
    if d == "pre_order":
        # Вимагаємо телефон перед замовленням
        try: await q.edit_message_reply_markup(reply_markup=None)
        except: pass
        await ctx.bot.send_message(
            upd.effective_chat.id,
            "📱 *Для оформлення замовлення* вкажіть номер телефону.\n\n"
            "Натисніть кнопку або введіть вручну (+380...):",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📱 Поділитися номером телефону", request_contact=True)]],
                one_time_keyboard=True, resize_keyboard=True))
        return PHONE

    if d == "pre_info":
        await send(
            "📋 *Умови та вартість оцінки*\n\n"
            "🚗 *Оцінка авто* — від 500 грн, термін 1 день\n"
            "🏠 *Оцінка квартири* — від 700 грн, термін 1–2 дні\n"
            "🏡 *Оцінка будинку* — від 1200 грн, термін 2–3 дні\n"
            "🌿 *Оцінка землі* — від 800 грн, термін 2–3 дні\n"
            "🏭 *Нежитлова нерухомість* — від 1500 грн, за домовленістю\n\n"
            "📦 Звіт надсилається Новою Поштою або електронно\n"
            "🎯 Оцінка для банку, нотаріуса, суду, страховки\n\n"
            f"📞 Уточнити вартість: {PHONE2}\n"
            f"🌐 {WEBSITE}",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Замовити оцінку", callback_data="pre_order")],
                [InlineKeyboardButton("◀️ Назад",           callback_data="home")],
            ]))
        return MENU

    if d == "pre_write":
        u2 = upd.effective_user
        ts = datetime.now().strftime("%d.%m.%Y %H:%M")
        # Надсилаємо запит адміну
        for tid in ADMIN_IDS:
            try:
                await ctx.bot.send_message(
                    tid,
                    f"💬 *ЗАПИТ НА ЗВ'ЯЗОК*\n{'─'*24}\n"
                    f"👤 *{u2.full_name}*\n"
                    f"🆔 `{u2.id}` | @{u2.username or '—'}\n"
                    f"🕐 {ts}\n\n"
                    f"[✉️ Написати](tg://user?id={u2.id})",
                    parse_mode="Markdown")
            except Exception:
                pass
        manager_link = f"[менеджеру]({MANAGER_TG_URL})" if MANAGER_TG_URL else "менеджеру"
        await send(
            f"✅ Ваш запит надіслано!\n\n"
            f"Менеджер зв'яжеться з вами найближчим часом.\n\n"
            f"Також можете написати {manager_link} або зателефонувати:\n"
            f"📞 {PHONE2}",
            InlineKeyboardMarkup([
                *([[InlineKeyboardButton("💬 Написати в Telegram",
                    url=MANAGER_TG_URL)]] if MANAGER_TG_URL else []),
                [InlineKeyboardButton("📋 Замовити оцінку", callback_data="pre_order")],
                [InlineKeyboardButton("◀️ Назад",           callback_data="home")],
            ]))
        return MENU

    if d == "about":
        await send(
            "🏢 *ОЦІНКА24*\n\n"
            "✅ Сертифіковані оцінювачі (ЗУ «Про оцінку майна»)\n"
            "✅ Досвід роботи понад 10 років\n"
            "✅ Оцінка нерухомості, авто, бізнесу, збитків\n"
            "✅ Звіти для банків, нотаріусів, судів\n"
            "✅ Відповідність МСО та НСО України",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("📞 Контакти",     callback_data="contact")],
                [InlineKeyboardButton("🏠 Головне меню", callback_data="home")],
            ]))
        return MENU

    if d == "contact":
        await send(
            f"📞 *Контакти ОЦІНКА24*\n\n"
            f"☎️ {PHONE1}\n📱 {PHONE2}\n"
            f"📧 `{EMAIL}`\n🌐 {WEBSITE}\n\n"
            "🕐 *Графік:*\nПн–Пт: 09:00–18:00\n"
            "Сб: 09:00–14:00 (за записом)\nНд: вихідний",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("ℹ️ Про компанію",   callback_data="about")],
                [InlineKeyboardButton("🏠 Головне меню",   callback_data="home")],
            ]))
        return MENU

    if d == "location":
        await send(
            "📍 *Геолокація об'єкта оцінки*\n\n"
            "Перебуваючи біля об'єкта натисніть кнопку нижче\nабо введіть адресу текстом.",
            home_kb())
        await ctx.bot.send_message(upd.effective_chat.id,
            "👇 Надішліть геолокацію:", reply_markup=gps_kb())
        return LOC

    if d in ("photogps", "obj_photogps"):
        try: await q.edit_message_reply_markup(reply_markup=None)
        except: pass
        await ctx.bot.send_message(
            upd.effective_chat.id,
            "📸 Фото з GPS\n\n"
            "Крок 1 — натисніть кнопку нижче та поділіться геолокацією.\n"
            "Крок 2 — зробіть НОВЕ фото камерою (не з галереї!).\n\n"
            "⚠️ Фото з галереї не містять GPS-координат.")
        await ctx.bot.send_message(
            upd.effective_chat.id,
            "📍 НАДІСЛАТИ ГЕОЛОКАЦІЮ ОБ'ЄКТА",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📍 НАДІСЛАТИ ГЕОЛОКАЦІЮ ОБ'ЄКТА", request_location=True)]],
                one_time_keyboard=True, resize_keyboard=True))
        return PHOTOGPS

    if d in ("video", "obj_video"):
        return await start_video(upd, ctx)

    if d == "done":
        return await finish_upload(upd, ctx)

    key = d.replace("obj_", "")
    if key in OBJECTS:
        # Вимагаємо телефон якщо ще не надано
        if not ctx.user_data.get("phone"):
            try: await q.edit_message_reply_markup(reply_markup=None)
            except: pass
            await ctx.bot.send_message(
                upd.effective_chat.id,
                "📱 *Для замовлення оцінки* необхідно вказати номер телефону.\n\n"
                "Натисніть кнопку або введіть вручну (+380...):",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("📱 Поділитися номером телефону", request_contact=True)]],
                    one_time_keyboard=True, resize_keyboard=True))
            ctx.user_data["pending_obj"] = key
            return PHONE
        return await show_object(upd, ctx, key)

    return MENU


# ══════════════════════════════════════════════════════════
#  ДОКУМЕНТИ
# ══════════════════════════════════════════════════════════

async def show_object(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, key: str) -> int:
    icon, name, docs = OBJECTS[key]
    ctx.user_data.update({"obj_key": key, "obj_name": f"{icon} {name}", "files": []})
    doc_list = "\n".join(f"  {i+1}. {d}" for i, d in enumerate(docs))
    text = (
        f"{icon} *{name}*\n\n"
        f"📋 *Необхідні документи:*\n{doc_list}\n\n"
        "Надсилайте фото та документи по одному.\n"
        "Ви також можете додати *фото з GPS* або провести *відеоогляд*.\n"
        "Після завершення натисніть *«✅ Завершити надсилання документів»*."
    )
    try: await upd.callback_query.edit_message_reply_markup(reply_markup=None)
    except: pass
    await ctx.bot.send_message(upd.effective_chat.id, text,
                               parse_mode="Markdown", reply_markup=object_kb())
    return UPLOAD


async def handle_file(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    msg   = upd.message
    u     = msg.from_user
    name  = ctx.user_data.get("obj_name", "Документ")
    files = ctx.user_data.setdefault("files", [])
    phone = ctx.user_data.get("phone", "—")
    caption = f"{name}\n👤 {u.full_name} | 🆔 `{u.id}`\n📱 @{u.username or '—'} | ☎️ {phone}"

    if msg.photo:
        await notify_photo(ctx, msg.photo[-1].file_id, caption)
        files.append(msg.photo[-1].file_id)
    elif msg.document:
        await notify_doc(ctx, msg.document.file_id, caption)
        files.append(msg.document.file_id)
    else:
        await msg.reply_text("⚠️ Надішліть фото або PDF документа.")
        return UPLOAD

    ack_text = f"✅ Файлів отримано: {len(files)}"
    ack_id   = ctx.user_data.get("ack_msg_id")

    if ack_id:
        try:
            await ctx.bot.edit_message_text(
                ack_text, chat_id=msg.chat_id,
                message_id=ack_id, reply_markup=object_kb())
            return UPLOAD
        except Exception:
            pass  # якщо редагування не вдалося — надсилаємо нове

    sent = await msg.reply_text(ack_text, reply_markup=object_kb())
    ctx.user_data["ack_msg_id"] = sent.message_id
    return UPLOAD


_COMMENT_EXAMPLES = {
    "car":    "Toyota Camry 2019, 2.5 бензин, пробіг 85 000 км, АКПП, стан добрий",
    "flat":   "3-кімнатна квартира, 5 поверх із 9, 72 м², після ремонту, є балкон",
    "house":  "2-поверховий будинок, 120 м², цегла, 2015 р.п., ділянка 8 соток",
    "land":   "Ділянка 12 соток, призначення — сільськогосподарське, є під'їзд",
    "nonres": "Офісне приміщення 200 м², 1 поверх, окремий вхід, у центрі міста",
}

async def finish_upload(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q     = upd.callback_query
    files = ctx.user_data.get("files", [])
    if not files:
        await q.answer("⚠️ Надішліть хоча б один файл!", show_alert=True)
        return UPLOAD
    try: await q.edit_message_reply_markup(reply_markup=None)
    except: pass

    obj_key = ctx.user_data.get("obj_key", "")
    example = _COMMENT_EXAMPLES.get(obj_key, "стан об'єкта, площа, рік побудови або інші важливі деталі")

    await ctx.bot.send_message(
        upd.effective_chat.id,
        "✅ *Файли отримано!*\n\n"
        "📝 Додайте короткий *опис об'єкта* оцінки:\n"
        f"_(наприклад: {example})_\n\n"
        "Або пропустіть цей крок:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ Пропустити опис", callback_data="comment_skip")],
        ]))
    return COMMENT


async def handle_comment(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    msg = upd.message
    ctx.user_data["comment"] = msg.text.strip() if msg and msg.text else ""
    return await _ask_delivery(upd, ctx)


async def skip_comment(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = upd.callback_query
    await q.answer()
    ctx.user_data["comment"] = ""
    try: await q.edit_message_reply_markup(reply_markup=None)
    except: pass
    return await _ask_delivery(upd, ctx)


async def _ask_delivery(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Питає дані отримувача звіту (Нова Пошта)."""
    await ctx.bot.send_message(
        upd.effective_chat.id,
        "📦 *Дані для відправлення звіту (Нова Пошта)*\n\n"
        "Надішліть одним повідомленням:\n"
        "• Місто та номер відділення НП\n"
        "• Прізвище та ім'я отримувача\n"
        "• Номер телефону отримувача\n\n"
        "_Приклад:_\n"
        "`Київ, відділення №12\nІваненко Іван\n+380671234567`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ Пропустити", callback_data="delivery_skip")],
        ]))
    return DELIVERY


async def handle_delivery(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    msg = upd.message
    ctx.user_data["delivery"] = msg.text.strip() if msg and msg.text else ""
    await _complete_request(upd, ctx)
    return MENU


async def skip_delivery(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = upd.callback_query
    await q.answer()
    ctx.user_data["delivery"] = ""
    try: await q.edit_message_reply_markup(reply_markup=None)
    except: pass
    await _complete_request(upd, ctx)
    return MENU


async def _complete_request(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Зберігає заявку в БД, сповіщає адміна і підтверджує клієнту."""
    u        = upd.effective_user
    name     = ctx.user_data.get("obj_name", "—")
    obj_key  = ctx.user_data.get("obj_key", "")
    files    = ctx.user_data.get("files", [])
    comment  = ctx.user_data.get("comment", "")
    delivery = ctx.user_data.get("delivery", "")
    phone    = ctx.user_data.get("phone", "—")
    ts       = datetime.now().strftime("%d.%m.%Y %H:%M")

    # ── Зберігаємо в БД ──────────────────────────────────
    req_id = None
    if DATABASE_URL:
        try:
            conn = await asyncpg.connect(DATABASE_URL)
            # Гарантуємо що користувач є в таблиці users
            await conn.execute("""
                INSERT INTO users (user_id, username, full_name, phone)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id) DO UPDATE
                  SET username=$2, full_name=$3, phone=COALESCE($4, users.phone)
            """, u.id, u.username, u.full_name, phone if phone != "—" else None)

            full_comment = comment
            if delivery:
                full_comment += ("\n\n" if full_comment else "") + f"Нова Пошта: {delivery}"
            req_id = await conn.fetchval("""
                INSERT INTO requests (user_id, request_type, status, comment)
                VALUES ($1, $2, 'new', $3) RETURNING id
            """, u.id, name, full_comment or None)
            await _db_release(conn)
            logger.info(f"Заявку #{req_id} збережено в БД для user {u.id}")
        except Exception as e:
            logger.warning(f"DB insert request error: {e}")

    # ── Сповіщення адміну ─────────────────────────────────
    adm_text = (
        f"📋 *НОВА ЗАЯВКА #{req_id or '—'}*\n{'─'*28}\n"
        f"👤 *{u.full_name}*\n"
        f"🆔 `{u.id}` | @{u.username or '—'}\n"
        f"📱 {phone}\n"
        f"🕐 {ts}\n\n"
        f"*{name}*\n"
        f"📎 Файлів: *{len(files)}*"
    )
    if comment:
        adm_text += f"\n\n📝 *Опис:* {comment}"
    if delivery:
        adm_text += f"\n\n📦 *Нова Пошта:*\n{delivery}"
    adm_text += f"\n\n[✉️ Написати клієнту](tg://user?id={u.id})"
    await notify(ctx, adm_text)

    # ── Підтвердження клієнту ─────────────────────────────
    client_text = (
        f"✅ *Заявку на {name} прийнято в роботу!*\n\n"
        "Представники компанії *ОЦІНКА24* зв'яжуться з вами найближчим часом.\n\n"
    )
    if delivery:
        client_text += f"📦 *Дані для доставки звіту:*\n_{delivery}_\n\n"
    client_text += (
        f"☎️ {PHONE1}\n"
        f"🌐 {WEBSITE}"
    )
    await ctx.bot.send_message(
        upd.effective_chat.id, client_text,
        parse_mode="Markdown", reply_markup=main_kb())


# ══════════════════════════════════════════════════════════
#  ГЕОЛОКАЦІЯ
# ══════════════════════════════════════════════════════════

async def handle_location(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    msg = upd.message
    u   = msg.from_user
    ts  = datetime.now().strftime("%d.%m.%Y %H:%M")

    if msg.location:
        lat, lon = msg.location.latitude, msg.location.longitude
        maps = f"https://maps.google.com/?q={lat},{lon}"
        await notify(ctx,
            f"📍 *ГЕОЛОКАЦІЯ ОБ'ЄКТА*\n"
            f"👤 *{u.full_name}* | 🆔 `{u.id}`\n"
            f"📱 @{u.username or '—'}\n🕐 {ts}\n\n"
            f"📌 `{lat:.6f}, {lon:.6f}`\n🗺 [Google Maps]({maps})\n\n"
            f"[✉️ Написати клієнту](tg://user?id={u.id})")
        await notify_loc(ctx, lat, lon)
        await msg.reply_text(
            f"✅ *Геолокацію зафіксовано!*\n\n"
            f"📌 `{lat:.5f}, {lon:.5f}`\n🗺 [Google Maps]({maps})",
            parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    elif msg.text and not msg.text.startswith("/"):
        await notify(ctx,
            f"📍 *АДРЕСА ОБ'ЄКТА*\n"
            f"👤 *{u.full_name}* | 🕐 {ts}\n"
            f"📬 {msg.text.strip()}\n\n"
            f"[✉️ Написати клієнту](tg://user?id={u.id})")
        await msg.reply_text(
            f"✅ *Адресу зафіксовано!*\n📬 {msg.text.strip()}",
            parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    else:
        await msg.reply_text("⚠️ Поділіться геолокацією або введіть адресу.")
        return LOC

    await ctx.bot.send_message(msg.chat.id,
        "Дякуємо! Оцінювач отримав місцезнаходження.", reply_markup=main_kb())
    return MENU


# ══════════════════════════════════════════════════════════
#  ФОТО+GPS
# ══════════════════════════════════════════════════════════

async def handle_photogps(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    msg = upd.message
    u   = msg.from_user
    ts  = datetime.now().strftime("%d.%m.%Y %H:%M")

    # ── Отримали геолокацію ───────────────────────────────
    if msg.location:
        ctx.user_data["pgps_lat"] = msg.location.latitude
        ctx.user_data["pgps_lon"] = msg.location.longitude

        # Якщо є збережене фото — обробляємо одразу
        if "pgps_photo" in ctx.user_data:
            photo_bytes = ctx.user_data.pop("pgps_photo")
            lat = msg.location.latitude
            lon = msg.location.longitude
            await _process_and_send_photo(msg, u, ctx, photo_bytes, lat, lon, ts)
            return PHOTOGPS

        await msg.reply_text(
            "✅ *GPS збережено!*\n\nТепер надішліть фото об'єкта.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove())
        return PHOTOGPS

    # ── Отримали фото ─────────────────────────────────────
    if msg.photo:
        lat = ctx.user_data.get("pgps_lat")
        lon = ctx.user_data.get("pgps_lon")

        pfile = await msg.photo[-1].get_file()
        photo_bytes = bytes(await pfile.download_as_bytearray())

        # GPS ще не надіслано — зберігаємо фото і просимо геолокацію
        if lat is None:
            ctx.user_data["pgps_photo"] = photo_bytes
            await msg.reply_text(
                "📍 *Фото отримано!*\n\n"
                "Тепер натисніть кнопку нижче щоб передати своє місцезнаходження — "
                "бот додасть координати, адресу та карту на фото.",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("📍 Надіслати геолокацію об'єкта", request_location=True)]],
                    one_time_keyboard=True, resize_keyboard=True))
            return PHOTOGPS

        await _process_and_send_photo(msg, u, ctx, photo_bytes, lat, lon, ts)
        return PHOTOGPS

    await msg.reply_text(
        "⚠️ Надішліть *фото* або спочатку *геолокацію* кнопкою.",
        parse_mode="Markdown")
    return PHOTOGPS


async def _process_and_send_photo(msg, u, ctx, photo_bytes: bytes,
                                   lat: float, lon: float, ts: str):
    """Обробляє фото з GPS, надсилає клієнту і адмінам."""
    await msg.reply_text("⏳ Обробляю фото, визначаю адресу...")

    address = await _get_address(lat, lon)

    processed = await build_geotagged_photo(photo_bytes, lat, lon, address, ts)

    processed.seek(0)
    await msg.reply_photo(
        processed,
        caption=f"✅ Фото з геотегом *ОЦІНКА24*\n📍 `{lat:.6f}, {lon:.6f}`",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove())

    maps = f"https://maps.google.com/?q={lat},{lon}"
    caption = (
        f"📸 *ФОТО+GPS ОБ'ЄКТА*\n{'─'*28}\n"
        f"👤 *{u.full_name}*\n🆔 `{u.id}` | @{u.username or '—'}\n"
        f"🕐 {ts}\n"
        f"📌 `{lat:.6f}, {lon:.6f}`\n🗺 [Google Maps]({maps})"
    )
    if address:
        caption += f"\n📬 {address[:120]}"
    caption += f"\n\n[✉️ Написати клієнту](tg://user?id={u.id})"

    processed.seek(0)
    await notify_photo(ctx, processed, caption)

    ctx.user_data.pop("pgps_lat", None)
    ctx.user_data.pop("pgps_lon", None)
    ctx.user_data.pop("pgps_photo", None)

    await ctx.bot.send_message(
        msg.chat.id,
        "Надішліть ще фото або поверніться в меню:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📸 Ще одне фото", callback_data="photogps")],
            [InlineKeyboardButton("🏠 Головне меню", callback_data="home")],
        ]))


# ══════════════════════════════════════════════════════════
#  ВІДЕООГЛЯД (Jitsi)
# ══════════════════════════════════════════════════════════

async def start_video(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    u    = upd.effective_user
    ts   = datetime.now().strftime("%d.%m.%Y %H:%M")
    room = f"Otsinka24-{uuid.uuid4().hex[:12].upper()}"
    url  = f"https://meet.jit.si/{room}"
    ctx.user_data["jitsi"] = url

    phone = ctx.user_data.get("phone","—")
    await notify(ctx,
        f"📹 *ВІДЕООГЛЯД — ОНЛАЙН*\n{'─'*28}\n"
        f"👤 *{u.full_name}* | 🆔 `{u.id}`\n"
        f"📱 @{u.username or '—'} | ☎️ {phone}\n🕐 {ts}\n\n"
        f"🔗 `{room}`\n"
        f"[📹 Приєднатися]({url})\n\n"
        f"⚡️ Клієнт підключається!\n"
        f"[✉️ Написати](tg://user?id={u.id})")

    try: await upd.callback_query.edit_message_reply_markup(reply_markup=None)
    except: pass

    await ctx.bot.send_message(upd.effective_chat.id,
        "📹 *Відеоогляд розпочато!*\nОцінювач отримав сповіщення і незабаром приєднається.",
        parse_mode="Markdown")
    await ctx.bot.send_message(upd.effective_chat.id,
        "📍 Поділіться геолокацією об'єкта:",
        reply_markup=gps_kb("📍 Надіслати геолокацію"))
    await ctx.bot.send_message(upd.effective_chat.id,
        "👇 Натисніть щоб увійти у відеодзвінок:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📹 Увійти у відеодзвінок", url=url)
        ]]))
    return VIDEOLOC


async def handle_video_loc(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    msg   = upd.message
    u     = msg.from_user
    ts    = datetime.now().strftime("%d.%m.%Y %H:%M")
    jitsi = ctx.user_data.get("jitsi","")

    if msg.location:
        lat, lon = msg.location.latitude, msg.location.longitude
        maps = f"https://maps.google.com/?q={lat},{lon}"
        await notify(ctx,
            f"📍 *GPS (відеоогляд)*\n"
            f"👤 {u.full_name} | 🕐 {ts}\n"
            f"📌 `{lat:.6f}, {lon:.6f}`\n🗺 [Google Maps]({maps})")
        await notify_loc(ctx, lat, lon)
        await msg.reply_text(
            f"✅ *GPS зафіксовано!*\n📌 `{lat:.5f}, {lon:.5f}`",
            parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    elif msg.text and not msg.text.startswith("/"):
        await notify(ctx, f"📍 *АДРЕСА (відеоогляд)*\n👤 {u.full_name}\n📬 {msg.text.strip()}")
        await msg.reply_text(f"✅ Адресу зафіксовано!\n📬 {msg.text.strip()}",
            reply_markup=ReplyKeyboardRemove())
    else:
        await msg.reply_text("⚠️ Поділіться геолокацією або введіть адресу.")
        return VIDEOLOC

    if jitsi:
        await ctx.bot.send_message(msg.chat.id, "👇 Увійдіть у відеодзвінок:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📹 Увійти у відеодзвінок", url=jitsi)
            ]]))
    return MENU


# ══════════════════════════════════════════════════════════
#  АДМІН-ПАНЕЛЬ
# ══════════════════════════════════════════════════════════

_db_pool = None

async def _get_pool():
    global _db_pool
    if _db_pool is None and DATABASE_URL:
        _db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    return _db_pool

async def _db_connect():
    pool = await _get_pool()
    if pool:
        return await pool.acquire()
    return await asyncpg.connect(DATABASE_URL)

async def _db_release(conn):
    pool = await _get_pool()
    if pool and conn:
        await pool.release(conn)
    elif conn:
        await _db_release(conn)


async def admin_panel(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    u = upd.effective_user
    if u.id not in ADMIN_IDS:
        await upd.message.reply_text("⛔ Доступ заборонено.")
        return MENU
    await _show_admin_home(upd.message, ctx)
    return ADMIN


async def _show_admin_home(msg_or_query, ctx):
    db_ok = bool(DATABASE_URL)
    users_cnt = active_cnt = work_cnt = done_cnt = all_cnt = "—"

    if db_ok:
        try:
            conn = await _db_connect()
            users_cnt  = await conn.fetchval("SELECT COUNT(*) FROM users")
            active_cnt = await conn.fetchval("SELECT COUNT(*) FROM requests WHERE status='new'")
            work_cnt   = await conn.fetchval("SELECT COUNT(*) FROM requests WHERE status='in_progress'")
            done_cnt   = await conn.fetchval("SELECT COUNT(*) FROM requests WHERE status='done'")
            all_cnt    = await conn.fetchval("SELECT COUNT(*) FROM requests")
            await _db_release(conn)
            db_ok = True
        except Exception as e:
            logger.warning(f"Admin home DB error: {e}")
            db_ok = False

    db_status = "✅ підключена" if db_ok else "⚠️ не налаштована"
    gmaps_status = "✅ активний" if gmaps else "⚠️ не налаштований"

    text = (
        "🔐 *Адмін-панель ОЦІНКА24*\n"
        f"{'─' * 28}\n"
        f"🗄 БД: {db_status}\n"
        f"🗺 Google Maps: {gmaps_status}\n"
        f"{'─' * 28}\n"
        f"👥 Клієнтів: *{users_cnt}*\n"
        f"📋 Нових заявок: *{active_cnt}*\n"
        f"🔄 В роботі: *{work_cnt}*\n"
        f"✅ Виконано: *{done_cnt}*\n"
        f"📊 Всього: *{all_cnt}*"
    )

    kb = admin_main_kb() if DATABASE_URL else InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Головне меню", callback_data="home")],
    ])

    if hasattr(msg_or_query, "edit_message_text"):
        await msg_or_query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await msg_or_query.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def admin_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Обробник усіх adm|* та st|* колбеків."""
    q = upd.callback_query
    await q.answer()

    if q.from_user.id not in ADMIN_IDS:
        await q.answer("⛔ Доступ заборонено", show_alert=True)
        return ADMIN

    data = q.data

    # ── Головна адмін-сторінка ────────────────────────────
    if data == "adm|home":
        await _show_admin_home(q, ctx)
        return ADMIN

    # ── Активні заявки ────────────────────────────────────
    if data == "adm|active":
        return await _admin_list(q, "new", "📋 Нові заявки")

    # ── Всі заявки ────────────────────────────────────────
    if data == "adm|all":
        return await _admin_list(q, None, "📊 Всі заявки")

    # ── Статистика ────────────────────────────────────────
    if data == "adm|stats":
        return await _admin_stats(q)

    # ── Клієнти ───────────────────────────────────────────
    if data == "adm|clients":
        return await _admin_clients(q)

    # ── Деталі заявки view|{id} ───────────────────────────
    if data.startswith("adm|view|"):
        req_id = int(data.split("|")[2])
        return await _admin_view_request(q, req_id)

    # ── Зміна статусу st|{id}|{status} ───────────────────
    if data.startswith("st|"):
        _, req_id, new_status = data.split("|")
        return await _admin_set_status(q, ctx, int(req_id), new_status)

    return ADMIN


async def _admin_list(q, status_filter: str | None, title: str) -> int:
    if not DATABASE_URL:
        await q.edit_message_text("⚠️ БД не налаштована.")
        return ADMIN
    try:
        conn = await _db_connect()
        if status_filter:
            rows = await conn.fetch("""
                SELECT r.id, r.request_type, r.status, r.created_at,
                       u.full_name, u.phone
                FROM requests r JOIN users u ON r.user_id = u.user_id
                WHERE r.status = $1
                ORDER BY r.created_at DESC LIMIT 20
            """, status_filter)
        else:
            rows = await conn.fetch("""
                SELECT r.id, r.request_type, r.status, r.created_at,
                       u.full_name, u.phone
                FROM requests r JOIN users u ON r.user_id = u.user_id
                ORDER BY r.created_at DESC LIMIT 20
            """)
        await _db_release(conn)
    except Exception as e:
        await q.edit_message_text(f"⚠️ Помилка БД: {e}")
        return ADMIN

    if not rows:
        await q.edit_message_text(
            f"{title}\n\nЗаявок не знайдено.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Назад", callback_data="adm|home")
            ]]))
        return ADMIN

    text = f"*{title}* ({len(rows)}):\n\n"
    keyboard = []
    for r in rows:
        status_icon = {"new": "🆕", "in_progress": "🔄", "done": "✅", "rejected": "❌"}.get(r["status"], "❓")
        dt = r["created_at"].strftime("%d.%m %H:%M") if r["created_at"] else "—"
        label = f"{status_icon} #{r['id']} {r['request_type']} | {r['full_name']} | {dt}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"adm|view|{r['id']}")])

    keyboard.append([InlineKeyboardButton("← Назад", callback_data="adm|home")])
    await q.edit_message_text(text, parse_mode="Markdown",
                               reply_markup=InlineKeyboardMarkup(keyboard))
    return ADMIN


async def _admin_view_request(q, req_id: int) -> int:
    try:
        conn = await _db_connect()
        req = await conn.fetchrow("""
            SELECT r.*, u.full_name, u.phone, u.username
            FROM requests r JOIN users u ON r.user_id = u.user_id
            WHERE r.id = $1
        """, req_id)
        await _db_release(conn)
    except Exception as e:
        await q.edit_message_text(f"⚠️ Помилка БД: {e}")
        return ADMIN

    if not req:
        await q.edit_message_text("⚠️ Заявку не знайдено.")
        return ADMIN

    status_label = _STATUS_LABELS.get(req["status"], req["status"])
    maps_link = ""
    if req["lat"] and req["lon"]:
        maps_link = f"\n🗺 [Google Maps](https://maps.google.com/?q={req['lat']},{req['lon']})"

    text = (
        f"📋 *Заявка #{req['id']}*\n"
        f"{'─' * 24}\n"
        f"📌 Тип: *{req['request_type']}*\n"
        f"🏷 Статус: *{status_label}*\n"
        f"👤 Клієнт: {req['full_name']}\n"
        f"📱 Телефон: `{req['phone'] or '—'}`\n"
        f"🆔 @{req['username'] or '—'} | `{req['user_id']}`\n"
        f"📬 Адреса: {req['address'] or '—'}\n"
        f"📍 GPS: `{req['lat']}, {req['lon']}`{maps_link}\n"
        f"🕐 {req['created_at'].strftime('%d.%m.%Y %H:%M') if req['created_at'] else '—'}\n\n"
        f"[✉️ Написати клієнту](tg://user?id={req['user_id']})"
    )
    await q.edit_message_text(text, parse_mode="Markdown",
                               reply_markup=_status_kb(req_id),
                               disable_web_page_preview=True)
    return ADMIN


async def _admin_set_status(q, ctx, req_id: int, new_status: str) -> int:
    try:
        conn = await _db_connect()
        req = await conn.fetchrow("""
            SELECT r.*, u.full_name, u.user_id
            FROM requests r JOIN users u ON r.user_id = u.user_id
            WHERE r.id = $1
        """, req_id)
        await conn.execute("UPDATE requests SET status=$1 WHERE id=$2", new_status, req_id)
        await _db_release(conn)
    except Exception as e:
        await q.edit_message_text(f"⚠️ Помилка БД: {e}")
        return ADMIN

    label = _STATUS_LABELS.get(new_status, new_status)
    await q.edit_message_text(
        f"✅ Заявка *#{req_id}* → {label}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("← До заявки",    callback_data=f"adm|view|{req_id}")],
            [InlineKeyboardButton("📋 До списку",   callback_data="adm|active")],
            [InlineKeyboardButton("🏠 Адмін-меню",  callback_data="adm|home")],
        ]))

    # Сповіщення клієнту
    status_msgs = {
        "done":        "✅ Ваша заявка *виконана*! Оцінювач завершив роботу.",
        "in_progress": "🔄 Ваша заявка *прийнята в роботу*. Очікуйте на зв'язок.",
        "rejected":    "❌ Ваша заявка *відхилена*. Зверніться до підтримки.",
        "new":         "🆕 Ваша заявка *повернута* в чергу.",
    }
    if req and new_status in status_msgs:
        try:
            await ctx.bot.send_message(
                req["user_id"],
                f"{status_msgs[new_status]}\n\n"
                f"Заявка: *#{req_id}* — {req['request_type']}",
                parse_mode="Markdown")
        except Exception:
            pass
    return ADMIN


async def _admin_stats(q) -> int:
    try:
        conn = await _db_connect()
        total_users    = await conn.fetchval("SELECT COUNT(*) FROM users")
        today_users    = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at::date = CURRENT_DATE")
        total_requests = await conn.fetchval("SELECT COUNT(*) FROM requests")
        today_requests = await conn.fetchval(
            "SELECT COUNT(*) FROM requests WHERE created_at::date = CURRENT_DATE")
        by_type = await conn.fetch(
            "SELECT request_type, COUNT(*) as cnt FROM requests GROUP BY request_type ORDER BY cnt DESC")
        by_status = await conn.fetch(
            "SELECT status, COUNT(*) as cnt FROM requests GROUP BY status")
        await _db_release(conn)
    except Exception as e:
        await q.edit_message_text(f"⚠️ Помилка БД: {e}")
        return ADMIN

    type_lines = "\n".join(f"  • {r['request_type']}: *{r['cnt']}*" for r in by_type)
    status_lines = "\n".join(
        f"  • {_STATUS_LABELS.get(r['status'], r['status'])}: *{r['cnt']}*"
        for r in by_status)

    text = (
        "📈 *Статистика ОЦІНКА24*\n"
        f"{'─' * 28}\n"
        f"👥 Клієнтів всього: *{total_users}* (сьогодні: *{today_users}*)\n"
        f"📋 Заявок всього: *{total_requests}* (сьогодні: *{today_requests}*)\n\n"
        f"*По типах:*\n{type_lines or '  —'}\n\n"
        f"*По статусах:*\n{status_lines or '  —'}"
    )
    await q.edit_message_text(text, parse_mode="Markdown",
                               reply_markup=InlineKeyboardMarkup([[
                                   InlineKeyboardButton("← Назад", callback_data="adm|home")
                               ]]))
    return ADMIN


async def _admin_clients(q) -> int:
    try:
        conn = await _db_connect()
        clients = await conn.fetch("""
            SELECT u.user_id, u.full_name, u.phone, u.username, u.created_at,
                   COUNT(r.id) as req_cnt
            FROM users u
            LEFT JOIN requests r ON r.user_id = u.user_id
            GROUP BY u.user_id, u.full_name, u.phone, u.username, u.created_at
            ORDER BY u.created_at DESC LIMIT 20
        """)
        await _db_release(conn)
    except Exception as e:
        await q.edit_message_text(f"⚠️ Помилка БД: {e}")
        return ADMIN

    if not clients:
        await q.edit_message_text(
            "👥 Клієнтів ще немає.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Назад", callback_data="adm|home")
            ]]))
        return ADMIN

    lines = []
    for c in clients:
        dt = c["created_at"].strftime("%d.%m.%Y") if c["created_at"] else "—"
        lines.append(
            f"👤 *{c['full_name']}* | `{c['phone'] or '—'}`\n"
            f"   @{c['username'] or '—'} | Заявок: {c['req_cnt']} | {dt}"
        )
    text = f"👥 *Клієнти* (останні {len(clients)}):\n\n" + "\n\n".join(lines)

    await q.edit_message_text(text, parse_mode="Markdown",
                               reply_markup=InlineKeyboardMarkup([[
                                   InlineKeyboardButton("← Назад", callback_data="adm|home")
                               ]]))
    return ADMIN


# ══════════════════════════════════════════════════════════
#  ЗБІРКА
# ══════════════════════════════════════════════════════════

async def err_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Помилка: {ctx.error}", exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Виникла помилка. Спробуйте /start")
        except Exception:
            pass


async def _post_init(app):
    if DATABASE_URL:
        await _get_pool()   # ініціалізуємо пул з'єднань
        await init_db()


def build():
    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            PHONE:    [MessageHandler(
                (filters.CONTACT | filters.TEXT) & ~filters.COMMAND,
                handle_phone
            )],
            MENU:     [CallbackQueryHandler(on_menu)],
            UPLOAD:   [
                MessageHandler(filters.PHOTO | filters.Document.ALL, handle_file),
                CallbackQueryHandler(on_menu),
            ],
            LOC:      [
                MessageHandler((filters.LOCATION | filters.TEXT) & ~filters.COMMAND, handle_location),
                CallbackQueryHandler(on_menu, pattern="^home$"),
            ],
            VIDEOLOC: [
                MessageHandler((filters.LOCATION | filters.TEXT) & ~filters.COMMAND, handle_video_loc),
                CallbackQueryHandler(on_menu, pattern="^home$"),
            ],
            PHOTOGPS: [
                MessageHandler(filters.PHOTO | filters.LOCATION, handle_photogps),
                CallbackQueryHandler(on_menu),
            ],
            COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_comment),
                CallbackQueryHandler(skip_comment, pattern="^comment_skip$"),
            ],
            DELIVERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_delivery),
                CallbackQueryHandler(skip_delivery, pattern="^delivery_skip$"),
            ],
            ADMIN: [
                CallbackQueryHandler(admin_callback, pattern=r"^(adm\||st\|)"),
                CallbackQueryHandler(on_menu, pattern="^home$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start",  cmd_start),
            CommandHandler("admin",  admin_panel),
        ],
        allow_reentry=True,
    )
    app.add_handler(conv)
    # /admin і всі adm|*/st|* колбеки — поза станами розмови
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^(adm\||st\|)"))
    app.add_error_handler(err_handler)
    return app


def main():
    _ensure_cyrillic_font_sync()
    logger.info("🚀 ОЦІНКА24 Bot v5.3 | Google Maps + Адмін-панель + БД")
    logger.info(f"   Адмінів:  {len(ADMIN_IDS)}")
    logger.info(f"   Канал:    {'✅' if CHANNEL_ID else '—'}")
    logger.info(f"   БД:       {'✅' if DATABASE_URL else '—'}")
    logger.info(f"   Google Maps: {'✅' if gmaps else '—'}")
    build().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()