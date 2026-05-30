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

WEBSITE = "https://ocenka24.com.ua/"
EMAIL   = "info@ocenka24.com.ua"
PHONE1  = "0 800 502-977"
PHONE2  = "+38 (050) 3000-173"
LOGO    = "https://ocenka24.com.ua/img/ocenka24-logo.png"

assert BOT_TOKEN, "BOT_TOKEN відсутній у .env"

# ── Стани ─────────────────────────────────────────────────
MENU, UPLOAD, LOC, VIDEOLOC, PHOTOGPS, PHONE, ADMIN = range(7)

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
    await conn.close()
    logger.info("✅ База даних ініціалізована")


async def _save_user(user_id: int, username: str, full_name: str, phone: str):
    if not DATABASE_URL:
        return
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute('''
            INSERT INTO users (user_id, username, full_name, phone)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE SET phone=$4, username=$2, full_name=$3
        ''', user_id, username, full_name, phone)
        await conn.close()
    except Exception as e:
        logger.warning(f"DB save_user error: {e}")


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
        [InlineKeyboardButton("🚗 Оцінка авто",                       callback_data="obj_car")],
        [InlineKeyboardButton("🏠 Оцінка квартири",                   callback_data="obj_flat")],
        [InlineKeyboardButton("🏡 Оцінка будинку",                    callback_data="obj_house")],
        [InlineKeyboardButton("🌿 Оцінка землі",                      callback_data="obj_land")],
        [InlineKeyboardButton("🏭 Нежитлова нерухомість",             callback_data="obj_nonres")],
        [InlineKeyboardButton("📹 Онлайн відеоогляд",                 callback_data="video")],
        [InlineKeyboardButton("📍 Геолокація",                        callback_data="location")],
        [InlineKeyboardButton("📸 Фото+GPS",                          callback_data="photogps")],
        [InlineKeyboardButton("ℹ️ Про компанію", callback_data="about"),
         InlineKeyboardButton("📞 Контакти",     callback_data="contact")],
    ])

def upload_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Завершити надсилання", callback_data="done")],
        [InlineKeyboardButton("🏠 Головне меню",         callback_data="home")],
    ])

def gps_kb(label="📍 Поділитися геолокацією"):
    return ReplyKeyboardMarkup(
        [[KeyboardButton(label, request_location=True)]],
        one_time_keyboard=True, resize_keyboard=True)

def home_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Головне меню", callback_data="home")]])

def admin_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Активні заявки", callback_data="admin_active")],
        [InlineKeyboardButton("📊 Всі заявки",     callback_data="admin_all")],
        [InlineKeyboardButton("🏠 Головне меню",   callback_data="home")],
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


async def _get_address_google(lat: float, lon: float) -> str | None:
    """Геокодування через Google Maps (якщо налаштовано)."""
    if not gmaps:
        return None
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: gmaps.reverse_geocode((lat, lon), language="uk")
        )
        if result:
            return result[0]["formatted_address"]
    except Exception as e:
        logger.warning(f"Google Maps error: {e}")
    return None


async def _get_address(lat: float, lon: float) -> str:
    """Google Maps → Nominatim → BigDataCloud."""
    google_addr = await _get_address_google(lat, lon)
    if google_addr:
        return google_addr

    urls = [
        f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&accept-language=uk&zoom=18",
        f"https://api.bigdatacloud.net/data/reverse-geocode-client?latitude={lat}&longitude={lon}&localityLanguage=uk",
    ]
    import json
    for url in urls:
        data = await _fetch_async(url)
        if not data:
            continue
        try:
            j = json.loads(data)
            addr = j.get("display_name") or ", ".join(filter(None, [
                j.get("city", ""), j.get("principalSubdivision", ""), j.get("countryName", "")
            ]))
            if addr:
                return addr
        except Exception:
            pass
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
    Накладає на фото великим читабельним текстом (кирилиця):
      • Карта розташування (ліворуч, ~45% ширини)
      • Темна панель знизу:
          - Координати GPS (великий білий текст)
          - Адреса (великий блакитний, перенос рядків)
          - Дата/час і сайт (менший зелений)
      • ОЦІНКА24 (золотий, правий верхній кут)
    """
    img = Image.open(BytesIO(photo_bytes))
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGBA")
    W, H = img.size

    pad = max(20, W // 50)

    # ── Шрифти — великі, читабельні, з кирилицею ─────────
    f_brand = _load_font(max(64, W // 14))    # ОЦІНКА24
    f_label = _load_font(max(38, W // 22))    # підписи "КООРДИНАТИ:" "АДРЕСА:"
    f_gps   = _load_font(max(52, W // 17))    # координати
    f_addr  = _load_font(max(44, W // 19))    # адреса
    f_time  = _load_font(max(32, W // 30))    # дата і сайт

    tmp_draw = ImageDraw.Draw(img)

    # ── Завантажуємо карту ────────────────────────────────
    map_img = None
    map_px = min(int(W * 0.44), 520)
    if lat and lon:
        map_img = await _get_map(lat, lon, map_px)

    # ── Розраховуємо висоту нижньої панелі ───────────────
    lh_label = _text_h(tmp_draw, "К", f_label) + 6
    lh_gps   = _text_h(tmp_draw, "0", f_gps)   + 12
    lh_addr  = _text_h(tmp_draw, "А", f_addr)  + 10
    lh_time  = _text_h(tmp_draw, "0", f_time)  + 8

    # Ширина тексту в панелі (без карти якщо є)
    text_x = pad
    text_max_w = W - pad * 2

    addr_display = address if address else "Адресу не визначено"
    addr_lines = _wrap_text(tmp_draw, addr_display, f_addr, text_max_w)

    panel_h = (pad
               + lh_label + lh_gps          # GPS-блок
               + pad // 2
               + lh_label + lh_addr * len(addr_lines)  # адреса-блок
               + pad // 2
               + lh_time                    # дата/сайт
               + pad)

    # ── Напівпрозора панель знизу ─────────────────────────
    panel_top = H - panel_h
    overlay = Image.new("RGBA", (W, panel_h), (8, 12, 35, 225))
    img.paste(overlay, (0, panel_top), overlay)

    # Золота лінія-роздільник зверху панелі
    sep = Image.new("RGBA", (W, 5), (255, 215, 0, 220))
    img.paste(sep, (0, panel_top), sep)

    draw = ImageDraw.Draw(img)

    # ── ОЦІНКА24 — правий верхній кут ────────────────────
    brand = "ОЦІНКА24"
    bw = _text_w(draw, brand, f_brand)
    _draw_text_shadow(draw, (W - bw - pad, pad), brand, f_brand,
                      fill=(255, 215, 0, 255), shadow=(0, 0, 0, 200), offset=4)

    # ── Карта — лівий верхній кут ─────────────────────────
    if map_img:
        mw, mh = map_img.size
        border = 6
        # золота рамка
        frame = Image.new("RGBA", (mw + border * 2, mh + border * 2), (255, 215, 0, 255))
        frame.paste(map_img, (border, border))
        img.paste(frame, (pad, pad + _text_h(draw, brand, f_brand) + pad))

        # підпис "КАРТА МІСЦЯ"
        cap_y = pad + _text_h(draw, brand, f_brand) + pad + mh + border * 2 + 6
        if cap_y + lh_time < panel_top:
            _draw_text_shadow(draw, (pad, cap_y), "КАРТА МІСЦЯ", f_time,
                              fill=(255, 215, 0, 255), shadow=(0, 0, 0, 200))

    # ── Вміст панелі ──────────────────────────────────────
    y = panel_top + pad

    # GPS координати
    _draw_text_shadow(draw, (text_x, y), "КООРДИНАТИ:", f_label,
                      fill=(255, 215, 0, 200), shadow=(0, 0, 0, 150))
    y += lh_label

    if lat and lon:
        gps_text = f"{lat:.6f},  {lon:.6f}"
    else:
        gps_text = "GPS не визначено"
    _draw_text_shadow(draw, (text_x, y), gps_text, f_gps,
                      fill=(255, 255, 255, 255), shadow=(0, 0, 0, 220), offset=3)
    y += lh_gps + pad // 2

    # Адреса
    _draw_text_shadow(draw, (text_x, y), "АДРЕСА:", f_label,
                      fill=(255, 215, 0, 200), shadow=(0, 0, 0, 150))
    y += lh_label

    for line in addr_lines:
        _draw_text_shadow(draw, (text_x, y), line, f_addr,
                          fill=(160, 220, 255, 255), shadow=(0, 0, 0, 220), offset=3)
        y += lh_addr

    y += pad // 2

    # Дата — зліва
    _draw_text_shadow(draw, (text_x, y), ts, f_time,
                      fill=(140, 255, 140, 255), shadow=(0, 0, 0, 180))
    # Сайт — справа
    site_w = _text_w(draw, WEBSITE, f_time)
    _draw_text_shadow(draw, (W - site_w - pad, y), WEBSITE, f_time,
                      fill=(180, 180, 255, 255), shadow=(0, 0, 0, 180))

    # ── Зберігаємо ────────────────────────────────────────
    out = BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=94, optimize=True)
    out.seek(0)
    return out


# ══════════════════════════════════════════════════════════
#  /start  та  /cancel
# ══════════════════════════════════════════════════════════

async def cmd_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    u = upd.effective_user
    text = (
        f"👋 Вітаємо, *{u.first_name}*!\n\n"
        "🏢 *ОЦІНКА24* — професійна оцінка майна по всій Україні.\n\n"
        "Для проведення оцінки оберіть тип об'єкта і надішліть "
        "документи або проведіть онлайн відеоогляд.\n\n"
        f"☎️ {PHONE1}\n"
        f"📱 {PHONE2}\n"
        f"📧 {EMAIL}\n"
        f"🌐 {WEBSITE}"
    )
    try:
        await upd.message.reply_photo(photo=LOGO, caption=text,
                                      parse_mode="Markdown", reply_markup=main_kb())
    except Exception:
        await upd.message.reply_text(text, parse_mode="Markdown", reply_markup=main_kb())
    await upd.message.reply_text(
        "📱 *Для зв'язку з вами* вкажіть номер телефону:\n\n"
        "Натисніть кнопку або введіть вручну (+380...)",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("📱 Поділитися номером телефону", request_contact=True)]],
            one_time_keyboard=True, resize_keyboard=True
        )
    )
    return PHONE


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
        f"✅ Дякуємо, *{u.first_name}*!\nНомер телефону збережено.\n\n"
        "👇 Оберіть дію:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
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
        await send("🏠 Головне меню:", main_kb())
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

    if d == "photogps":
        await send(
            "📸 *Фото+GPS об'єкта*\n\n"
            "Надішліть фото об'єкта — бот автоматично:\n"
            "• Визначить адресу за GPS\n"
            "• Накладе міні-карту розташування\n"
            "• Додасть напис *ОЦІНКА24*, координати і дату\n\n"
            "💡 Якщо GPS у фото відсутній — спочатку надішліть "
            "геолокацію кнопкою нижче.",
            home_kb())
        await ctx.bot.send_message(upd.effective_chat.id,
            "👇 Надішліть фото або геолокацію:",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📍 Надіслати геолокацію об'єкта", request_location=True)]],
                one_time_keyboard=True, resize_keyboard=True))
        return PHOTOGPS

    if d == "video":
        return await start_video(upd, ctx)

    if d == "done":
        return await finish_upload(upd, ctx)

    key = d.replace("obj_","")
    if key in OBJECTS:
        return await show_object(upd, ctx, key)

    return MENU


# ══════════════════════════════════════════════════════════
#  ДОКУМЕНТИ
# ══════════════════════════════════════════════════════════

async def show_object(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, key: str) -> int:
    icon, name, docs = OBJECTS[key]
    ctx.user_data.update({"obj_key":key, "obj_name":f"{icon} {name}", "files":[]})
    doc_list = "\n".join(f"  {i+1}. {d}" for i,d in enumerate(docs))
    text = (
        f"{icon} *{name}*\n\n"
        f"📋 *Необхідні документи:*\n{doc_list}\n\n"
        "Надсилайте фото та документи по одному.\n"
        "Після завершення натисніть *«✅ Завершити»*."
    )
    try: await upd.callback_query.edit_message_reply_markup(reply_markup=None)
    except: pass
    await ctx.bot.send_message(upd.effective_chat.id, text,
                               parse_mode="Markdown", reply_markup=upload_kb())
    return UPLOAD


async def handle_file(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    msg   = upd.message
    u     = msg.from_user
    name  = ctx.user_data.get("obj_name","Документ")
    files = ctx.user_data.setdefault("files",[])
    phone   = ctx.user_data.get("phone","—")
    caption = f"{name}\n👤 {u.full_name} | 🆔 `{u.id}`\n📱 @{u.username or '—'} | ☎️ {phone}"

    if msg.photo:
        # Для документів — без геотегу, просто пересилаємо
        await notify_photo(ctx, msg.photo[-1].file_id, caption)
        files.append(msg.photo[-1].file_id)
    elif msg.document:
        await notify_doc(ctx, msg.document.file_id, caption)
        files.append(msg.document.file_id)
    else:
        await msg.reply_text("⚠️ Надішліть фото або PDF документа.")
        return UPLOAD

    await msg.reply_text(
        f"✅ Файл прийнято! Всього: *{len(files)}*",
        parse_mode="Markdown", reply_markup=upload_kb())
    return UPLOAD


async def finish_upload(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q     = upd.callback_query
    u     = upd.effective_user
    name  = ctx.user_data.get("obj_name","—")
    files = ctx.user_data.get("files",[])
    if not files:
        await q.answer("⚠️ Надішліть хоча б один файл!", show_alert=True)
        return UPLOAD
    await notify(ctx,
        f"📋 *ДОКУМЕНТИ ОТРИМАНО*\n{'─'*28}\n"
        f"👤 *{u.full_name}*\n🆔 `{u.id}` | @{u.username or '—'}\n"
        f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        f"{name}\nФайлів: *{len(files)}*\n\n"
        f"[✉️ Написати клієнту](tg://user?id={u.id})")
    try: await q.edit_message_reply_markup(reply_markup=None)
    except: pass
    await ctx.bot.send_message(upd.effective_chat.id,
        "✅ *Документи надіслано!*\nОцінювач перевірить і зв'яжеться з вами.",
        parse_mode="Markdown", reply_markup=main_kb())
    return MENU


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

    if msg.location:
        ctx.user_data["pgps_lat"] = msg.location.latitude
        ctx.user_data["pgps_lon"] = msg.location.longitude
        await msg.reply_text(
            "✅ *GPS збережено!*\n\nТепер надішліть фото об'єкта.",
            parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        return PHOTOGPS

    if msg.photo:
        await msg.reply_text("⏳ Обробляю фото з геотегом...")

        pfile       = await msg.photo[-1].get_file()
        photo_bytes = bytes(await pfile.download_as_bytearray())

        img_tmp     = Image.open(BytesIO(photo_bytes))
        lat, lon    = _get_exif_gps(img_tmp)

        if lat is None:
            lat = ctx.user_data.get("pgps_lat")
            lon = ctx.user_data.get("pgps_lon")

        address = await _get_address(lat, lon) if lat and lon else ""

        processed = await build_geotagged_photo(photo_bytes, lat, lon, address, ts)

        # Клієнту
        processed.seek(0)
        await msg.reply_photo(processed, caption="✅ Фото з геотегом *ОЦІНКА24*",
                              parse_mode="Markdown")

        # Адміну / каналу
        caption = (
            f"📸 *ФОТО+GPS ОБ'ЄКТА*\n"
            f"{'─'*28}\n"
            f"👤 *{u.full_name}*\n🆔 `{u.id}` | @{u.username or '—'}\n"
            f"🕐 {ts}"
        )
        if lat and lon:
            maps = f"https://maps.google.com/?q={lat},{lon}"
            caption += f"\n📌 `{lat:.6f}, {lon:.6f}`\n🗺 [Google Maps]({maps})"
        if address:
            caption += f"\n📬 {address[:120]}"
        caption += f"\n\n[✉️ Написати клієнту](tg://user?id={u.id})"

        processed.seek(0)
        await notify_photo(ctx, processed, caption)
        if lat and lon:
            await notify_loc(ctx, lat, lon)

        ctx.user_data.pop("pgps_lat", None)
        ctx.user_data.pop("pgps_lon", None)

        await ctx.bot.send_message(msg.chat.id,
            "Надішліть ще фото або поверніться в меню:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📸 Ще одне фото",  callback_data="photogps")],
                [InlineKeyboardButton("🏠 Головне меню",  callback_data="home")],
            ]))
        return PHOTOGPS

    await msg.reply_text(
        "⚠️ Надішліть *фото* або спочатку *геолокацію* кнопкою.",
        parse_mode="Markdown")
    return PHOTOGPS


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

async def admin_panel(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    u = upd.effective_user
    if u.id not in ADMIN_IDS:
        await upd.message.reply_text("⛔ Доступ заборонено.")
        return MENU

    if not DATABASE_URL:
        await upd.message.reply_text("⚠️ База даних не налаштована.")
        return MENU

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        users_count = await conn.fetchval("SELECT COUNT(*) FROM users")
        active_count = await conn.fetchval("SELECT COUNT(*) FROM requests WHERE status='new'")
        all_count = await conn.fetchval("SELECT COUNT(*) FROM requests")
        await conn.close()
    except Exception as e:
        await upd.message.reply_text(f"⚠️ Помилка БД: {e}")
        return MENU

    await upd.message.reply_text(
        f"🔐 *Адмін-панель ОЦІНКА24*\n\n"
        f"👥 Клієнтів: *{users_count}*\n"
        f"📋 Активних заявок: *{active_count}*\n"
        f"📊 Всього заявок: *{all_count}*",
        parse_mode="Markdown",
        reply_markup=admin_kb()
    )
    return ADMIN


# ══════════════════════════════════════════════════════════
#  ЗБІРКА
# ══════════════════════════════════════════════════════════

async def err_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Помилка: {ctx.error}", exc_info=ctx.error)


async def _post_init(app):
    if DATABASE_URL:
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
            ADMIN: [CallbackQueryHandler(on_menu, pattern="^home$")],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start",  cmd_start),
            CommandHandler("admin",  admin_panel),
        ],
        allow_reentry=True,
    )
    app.add_handler(conv)
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