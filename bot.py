#!/usr/bin/env python3
"""ОЦІНКА24 — Telegram Bot v6.0 | AI-консультант + 2FA + Захист від злому"""

import asyncio
import logging
import os
import random
import time
import uuid
from collections import defaultdict
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

try:
    import google.generativeai as genai
    _gemini_available = True
except ImportError:
    _gemini_available = False

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
BOT_TOKEN           = os.getenv("BOT_TOKEN", "")
ADMIN_IDS           = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
CHANNEL_ID          = int(os.getenv("CHANNEL_ID", "0"))
DATABASE_URL        = os.getenv("DATABASE_URL")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
GEMINI_API_KEY      = os.getenv("GEMINI_API_KEY", "")

gmaps = None
if _gmaps_available and GOOGLE_MAPS_API_KEY and GOOGLE_MAPS_API_KEY.startswith("AIza"):
    try:
        gmaps = _googlemaps.Client(key=GOOGLE_MAPS_API_KEY)
    except Exception as _e:
        logging.warning(f"Google Maps init failed: {_e}")

gemini_model = None  # initialized later in _init_gemini() after _AI_SYSTEM_PROMPT is defined

WEBSITE      = "https://ocenka24.com.ua/"
EMAIL        = "info@ocenka24.com.ua"
PHONE1       = "0 800 502-977"
PHONE2       = "+38 (050) 3000-173"
PHONE2_RAW   = "+380503000173"
_mgr_raw     = os.getenv("MANAGER_TG", "")
MANAGER_TG_URL = (
    _mgr_raw if _mgr_raw.startswith("http")
    else f"https://t.me/{_mgr_raw.lstrip('@')}" if _mgr_raw
    else ""
)
LOGO = "https://ocenka24.com.ua/img/ocenka24-logo.png"

assert BOT_TOKEN, "BOT_TOKEN відсутній у .env"

# ── Стани ─────────────────────────────────────────────────
(MENU, UPLOAD, LOC, VIDEOLOC, PHOTOGPS, PHONE,
 ADMIN, COMMENT, DELIVERY, AI_CHAT, ADMIN_2FA) = range(11)

# ══════════════════════════════════════════════════════════
#  БЕЗПЕКА: Rate limiting, flood, ban, 2FA
# ══════════════════════════════════════════════════════════

_rate_buckets: dict[int, list[float]] = defaultdict(list)
_flood_warned: set[int] = set()
_admin_2fa_store: dict[int, tuple[str, float]] = {}
_banned_users: set[int] = set()

RATE_LIMIT_MSGS   = 25   # повідомлень
RATE_LIMIT_WINDOW = 60   # за секунд
FLOOD_BAN_MSGS    = 50   # тимчасовий блок після N повідомлень


def _rate_check(user_id: int) -> bool:
    """True якщо користувач перевищив ліміт (заблокувати)."""
    now = time.time()
    bucket = _rate_buckets[user_id]
    bucket[:] = [t for t in bucket if now - t < RATE_LIMIT_WINDOW]
    bucket.append(now)
    if len(bucket) >= FLOOD_BAN_MSGS:
        _banned_users.add(user_id)
        logger.warning(f"FLOOD BAN: user {user_id} ({len(bucket)} msg/{RATE_LIMIT_WINDOW}s)")
        return True
    return len(bucket) > RATE_LIMIT_MSGS


def _is_banned(user_id: int) -> bool:
    return user_id in _banned_users


def _gen_2fa(user_id: int) -> str:
    code = f"{random.SystemRandom().randint(0, 999999):06d}"
    _admin_2fa_store[user_id] = (code, time.time())
    return code


def _verify_2fa(user_id: int, code: str) -> bool:
    if user_id not in _admin_2fa_store:
        return False
    stored, ts = _admin_2fa_store[user_id]
    if time.time() - ts > 300:
        _admin_2fa_store.pop(user_id, None)
        return False
    if stored == code.strip():
        _admin_2fa_store.pop(user_id, None)
        return True
    return False


# ══════════════════════════════════════════════════════════
#  БАЗА ДАНИХ
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
        await conn.close()


async def init_db():
    if not DATABASE_URL:
        logger.warning("DATABASE_URL не задано — БД не ініціалізована")
        return
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id    BIGINT PRIMARY KEY,
                username   TEXT,
                full_name  TEXT,
                phone      TEXT,
                is_banned  BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS requests (
                id           SERIAL PRIMARY KEY,
                user_id      BIGINT REFERENCES users(user_id),
                request_type TEXT NOT NULL,
                status       TEXT DEFAULT 'new',
                address      TEXT,
                lat          DOUBLE PRECISION,
                lon          DOUBLE PRECISION,
                comment      TEXT,
                delivery     TEXT,
                admin_notes  TEXT,
                report_ready BOOLEAN DEFAULT FALSE,
                deadline     TEXT,
                ai_summary   TEXT,
                files_count  INTEGER DEFAULT 0,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS request_files (
                id         SERIAL PRIMARY KEY,
                request_id INTEGER REFERENCES requests(id),
                file_id    TEXT NOT NULL,
                file_type  TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS request_messages (
                id         SERIAL PRIMARY KEY,
                request_id INTEGER REFERENCES requests(id),
                sender     TEXT NOT NULL,
                message    TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS security_log (
                id         SERIAL PRIMARY KEY,
                user_id    BIGINT,
                event      TEXT,
                details    TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        # Міграція — додаємо нові колонки якщо їх ще немає
        for col, definition in [
            ("delivery",     "TEXT"),
            ("admin_notes",  "TEXT"),
            ("report_ready", "BOOLEAN DEFAULT FALSE"),
            ("deadline",     "TEXT"),
            ("files_count",  "INTEGER DEFAULT 0"),
            ("updated_at",   "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ]:
            try:
                await conn.execute(
                    f"ALTER TABLE requests ADD COLUMN IF NOT EXISTS {col} {definition}")
            except Exception:
                pass
        logger.info("✅ База даних ініціалізована")
    except Exception as e:
        logger.error(f"init_db error: {e}")
    finally:
        if conn:
            await conn.close()


async def _save_user(user_id: int, username: str, full_name: str, phone: str):
    if not DATABASE_URL:
        return
    conn = None
    try:
        conn = await _db_connect()
        await conn.execute('''
            INSERT INTO users (user_id, username, full_name, phone)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE
              SET phone=$4, username=$2, full_name=$3
        ''', user_id, username, full_name, phone)
    except Exception as e:
        logger.warning(f"DB save_user: {e}")
    finally:
        if conn:
            await _db_release(conn)


async def _get_saved_phone(user_id: int) -> str | None:
    """Повертає збережений телефон клієнта з БД (щоб не питати повторно)."""
    if not DATABASE_URL:
        return None
    conn = None
    try:
        conn = await _db_connect()
        return await conn.fetchval("SELECT phone FROM users WHERE user_id=$1", user_id)
    except Exception:
        return None
    finally:
        if conn:
            await _db_release(conn)


async def _log_security(user_id: int, event: str, details: str = ""):
    if not DATABASE_URL:
        return
    conn = None
    try:
        conn = await _db_connect()
        await conn.execute(
            "INSERT INTO security_log (user_id, event, details) VALUES ($1,$2,$3)",
            user_id, event, details[:500])
    except Exception:
        pass
    finally:
        if conn:
            await _db_release(conn)


# ══════════════════════════════════════════════════════════
#  ОБ'ЄКТИ ОЦІНКИ
# ══════════════════════════════════════════════════════════

OBJECTS = {
    "car": ("🚗", "Оцінка транспортного засобу", [
        "📋 Технічний паспорт (свідоцтво про реєстрацію)",
        "🪪 Документ що посвідчує особу",
        "📸 Фото ТЗ ззовні з 4 кутів",
        "📸 Фото салону, пробігу та VIN-коду",
    ], 1500, "1 день"),
    "flat": ("🏠", "Оцінка квартири", [
        "📜 Правовстановлюючий документ",
        "📋 Технічний паспорт",
        "🪪 Документ що посвідчує особу",
        "📸 Фото кімнат, кухні, санвузлу",
    ], 1600, "1 день"),
    "house": ("🏡", "Оцінка житлового будинку", [
        "📜 Правовстановлюючий документ на будинок",
        "📋 Технічний паспорт",
        "📜 Правовстановлюючий документ на землю",
        "🪪 Документ що посвідчує особу",
        "📸 Фото будинку ззовні з 4 кутів та всередині",
    ], 1600, "1 день"),
    "land": ("🌿", "Оцінка земельної ділянки", [
        "📜 Правовстановлюючий документ на землю",
        "🪪 Документ що посвідчує особу",
        "📸 Фото ділянки (4-6 штук)",
    ], 1500, "1 день"),
    "nonres": ("🏭", "Оцінка нежитлової будівлі/споруди", [
        "📜 Правовстановлюючий документ",
        "📋 Технічний паспорт",
        "🪪 Документ що посвідчує особу / юридичну особу",
        "📸 Фото будівлі ззовні з 4 кутів та всередині",
    ], 2500, "за домовленістю"),
}

# ── Ціни залежно від мети ─────────────────────────────────
_PRICES: dict[tuple[str, str], tuple[str, str]] = {
    # (об'єкт, мета): (ціна, строк)
    ("car",    "sale"):  ("1 500 грн",        "1 день"),
    ("car",    "court"): ("2 000–3 000 грн",  "1 день"),
    ("car",    "bank"):  ("2 500–3 500 грн",  "1 день"),
    ("car",    "other"): ("1 500 грн",        "1 день"),
    ("flat",   "sale"):  ("1 600 грн",        "1 день"),
    ("flat",   "court"): ("2 500 грн",        "1 день"),
    ("flat",   "bank"):  ("3 000 грн",        "1 день"),
    ("flat",   "other"): ("1 600 грн",        "1 день"),
    ("house",  "sale"):  ("1 600 грн",        "1 день"),
    ("house",  "court"): ("2 500 грн",        "1 день"),
    ("house",  "bank"):  ("3 000 грн",        "1 день"),
    ("house",  "other"): ("1 600 грн",        "1 день"),
    ("land",   "sale"):  ("1 500 грн",        "1 день"),
    ("land",   "court"): ("2 500 грн",        "1 день"),
    ("land",   "bank"):  ("3 000 грн",        "1 день"),
    ("land",   "other"): ("1 500 грн",        "1 день"),
    ("nonres", "sale"):  ("від 2 500 грн",    "за домовленістю"),
    ("nonres", "court"): ("від 4 000 грн",    "2–3 дні"),
    ("nonres", "bank"):  ("від 4 500 грн",    "2–3 дні"),
    ("nonres", "other"): ("від 2 500 грн",    "за домовленістю"),
}

_PURPOSE_LABELS: dict[str, str] = {
    "sale":  "🏷 Купівля-продаж",
    "court": "⚖️ Для суду",
    "bank":  "🏦 Для банку / іпотеки",
    "other": "📋 Інша мета",
}


def _get_price(obj_key: str, purpose: str) -> tuple[str, str]:
    return _PRICES.get((obj_key, purpose), _PRICES.get((obj_key, "sale"), ("—", "—")))


def _purpose_kb(obj_key: str) -> InlineKeyboardMarkup:
    """Клавіатура вибору мети оцінки."""
    rows = [
        [InlineKeyboardButton("🏷 Купівля-продаж",    callback_data=f"purpose|sale|{obj_key}")],
        [InlineKeyboardButton("⚖️ Для суду",          callback_data=f"purpose|court|{obj_key}")],
        [InlineKeyboardButton("🏦 Для банку / іпотеки", callback_data=f"purpose|bank|{obj_key}")],
        [InlineKeyboardButton("📋 Інша мета",         callback_data=f"purpose|other|{obj_key}")],
        [InlineKeyboardButton("◀️ Назад",             callback_data="home")],
    ]
    return InlineKeyboardMarkup(rows)


_PRICES_TEXT = (
    "💰 *Вартість оцінки ОЦІНКА24*\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "🏷 *Купівля-продаж:*\n"
    "🚗 Авто — *1 500 грн* / 1 день\n"
    "🏠 Квартира — *1 600 грн* / 1 день\n"
    "🏡 Будинок — *1 600 грн* / 1 день\n"
    "🌿 Земля — *1 500 грн* / 1 день\n"
    "🏭 Нежитлова — *від 2 500 грн*\n\n"
    "🏦 *Для банку / іпотеки:*\n"
    "🚗 Авто — *2 500–3 500 грн* / 1 день\n"
    "🏠 Квартира — *3 000 грн* / 1 день\n"
    "🏡 Будинок — *3 000 грн* / 1 день\n"
    "🌿 Земля — *3 000 грн* / 1 день\n"
    "🏭 Нежитлова — *від 4 500 грн* / 2–3 дні\n\n"
    "⚖️ *Для суду:*\n"
    "🚗 Авто — *2 000–3 000 грн* / 1 день\n"
    "🏠 Квартира — *2 500 грн* / 1 день\n"
    "🏡 Будинок — *2 500 грн* / 1 день\n"
    "🌿 Земля — *2 500 грн* / 1 день\n"
    "🏭 Нежитлова — *від 4 000 грн* / 2–3 дні\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "📦 Звіт надсилається Новою Поштою або електронно\n\n"
    f"📞 Уточнити: {'+38 (050) 3000-173'}\n"
    f"🌐 ocenka24.com.ua"
)

# ══════════════════════════════════════════════════════════
#  КЛАВІАТУРИ
# ══════════════════════════════════════════════════════════

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 AI-консультант",        callback_data="ai_chat")],
        [InlineKeyboardButton("🚗 Оцінка авто",           callback_data="obj_car"),
         InlineKeyboardButton("🏠 Оцінка квартири",       callback_data="obj_flat")],
        [InlineKeyboardButton("🏡 Оцінка будинку",        callback_data="obj_house"),
         InlineKeyboardButton("🌿 Оцінка землі",          callback_data="obj_land")],
        [InlineKeyboardButton("🏭 Нежитлова нерухомість", callback_data="obj_nonres")],
        [InlineKeyboardButton("💰 Ціни на оцінку",        callback_data="pre_info")],
        [InlineKeyboardButton("ℹ️ Про компанію",  callback_data="about"),
         InlineKeyboardButton("📞 Контакти",      callback_data="contact")],
    ])


def object_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Додати фото",                   callback_data="add_photo")],
        [InlineKeyboardButton("📸 Фото з GPS",                   callback_data="obj_photogps")],
        [InlineKeyboardButton("📹 Відеоогляд",                   callback_data="obj_video")],
        [InlineKeyboardButton("✅ Завершити надсилання документів", callback_data="done")],
        [InlineKeyboardButton("🏠 Головне меню",                  callback_data="home")],
    ])


def gps_kb(label="📍 Поділитися геолокацією"):
    return ReplyKeyboardMarkup(
        [[KeyboardButton(label, request_location=True)]],
        one_time_keyboard=True, resize_keyboard=True)


def home_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Головне меню", callback_data="home")]])


def _start_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🤖 AI-консультант",        callback_data="ai_chat")],
        [InlineKeyboardButton("📋 Замовити оцінку",       callback_data="pre_order")],
        [InlineKeyboardButton("💰 Ціни на оцінку",        callback_data="pre_info")],
        [InlineKeyboardButton("💬 Написати менеджеру",    callback_data="pre_write")],
    ]
    if MANAGER_TG_URL:
        rows.append([InlineKeyboardButton("📲 Написати в Telegram", url=MANAGER_TG_URL)])
    rows.append([InlineKeyboardButton("ℹ️ Про компанію", callback_data="about"),
                 InlineKeyboardButton("📞 Контакти",     callback_data="contact")])
    return InlineKeyboardMarkup(rows)


def admin_main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Активні заявки",  callback_data="adm|active")],
        [InlineKeyboardButton("📊 Всі заявки",      callback_data="adm|all")],
        [InlineKeyboardButton("📈 Статистика",      callback_data="adm|stats")],
        [InlineKeyboardButton("👥 Клієнти",         callback_data="adm|clients")],
        [InlineKeyboardButton("🚫 Бан-список",      callback_data="adm|bans")],
        [InlineKeyboardButton("🏠 Головне меню",    callback_data="home")],
    ])


_STATUS_LABELS = {
    "new":         "🆕 Нова",
    "in_progress": "🔄 В роботі",
    "done":        "✅ Виконано",
    "rejected":    "❌ Відхилено",
}


def _status_kb(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Виконано",   callback_data=f"st|{req_id}|done")],
        [InlineKeyboardButton("🔄 В роботі",  callback_data=f"st|{req_id}|in_progress")],
        [InlineKeyboardButton("❌ Відхилено",  callback_data=f"st|{req_id}|rejected")],
        [InlineKeyboardButton("← Назад",      callback_data="adm|active")],
    ])


# ══════════════════════════════════════════════════════════
#  РОЗСИЛКА
# ══════════════════════════════════════════════════════════

def _targets():
    t = list(ADMIN_IDS)
    if CHANNEL_ID:
        t.append(CHANNEL_ID)
    return list(set(t))


async def notify(ctx, text: str, kb: InlineKeyboardMarkup | None = None):
    for tid in _targets():
        try:
            kw: dict = {"parse_mode": "Markdown"}
            # Inline keyboards work only in private/group chats, not channels
            if kb and tid > 0:
                kw["reply_markup"] = kb
            await ctx.bot.send_message(tid, text, **kw)
        except Exception as e:
            logger.warning(f"notify md {tid}: {e}")
            try:
                plain = text.replace("*", "").replace("`", "").replace("_", "")
                await ctx.bot.send_message(tid, plain)
            except Exception as e2:
                logger.error(f"notify plain {tid}: {e2}")


async def notify_photo(ctx, data, caption: str):
    for tid in _targets():
        try:
            if isinstance(data, BytesIO):
                data.seek(0)
            await ctx.bot.send_photo(tid, data, caption=caption, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"notify_photo {tid}: {e}")


async def notify_doc(ctx, fid: str, caption: str):
    for tid in _targets():
        try:
            await ctx.bot.send_document(tid, fid, caption=caption, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"notify_doc {tid}: {e}")


async def notify_loc(ctx, lat: float, lon: float):
    for tid in _targets():
        try:
            await ctx.bot.send_location(tid, lat, lon)
        except Exception as e:
            logger.warning(f"notify_loc {tid}: {e}")


# ══════════════════════════════════════════════════════════
#  AI-КОНСУЛЬТАНТ (Gemini)
# ══════════════════════════════════════════════════════════

_AI_SYSTEM_PROMPT = """Ти — AI-консультант компанії ОЦІНКА24, яка надає послуги незалежної оцінки майна в Україні.

ВАЖЛИВО: Ти ПОВИНЕН відповідати на питання клієнта самостійно. Переводити до менеджера ТІЛЬКИ у крайньому випадку.

ЩО ТИ ВМІЄШ (відповідай на ВСЕ це сам):
— Пояснити навіщо потрібна оцінка (для банку, нотаріуса, продажу, страховки, суду, спадщини, розлучення, органів опіки, митниці)
— Розповісти які документи потрібні для кожного виду оцінки
— Назвати вартість та строки виконання (ОБОВ'ЯЗКОВО запитай мету перш ніж називати ціну)
— Пояснити як відбувається процес оцінки
— Допомогти визначити який вид оцінки потрібен
— Відповісти на будь-які питання про оцінку майна

ВАЖЛИВО ПРО ЦІНИ: Якщо клієнт питає ціну — СПОЧАТКУ запитай мету оцінки (продаж, суд, банк, страховка тощо), бо від цього залежить вартість. Оцінка для суду дорожча ніж для продажу.

ПРАЙС-ЛИСТ (ціни ЗАЛЕЖАТЬ ВІД МЕТИ — завжди питай мету перш ніж називати ціну):

Для КУПІВЛІ-ПРОДАЖУ:
🚗 Авто — 1 500 грн / 1 день
🏠 Квартира — 1 600 грн / 1 день
🏡 Будинок — 1 600 грн / 1 день
🌿 Земля — 1 500 грн / 1 день
🏭 Нежитлова нерухомість — від 2 500 грн

Для БАНКУ / ІПОТЕКИ:
🚗 Авто — 2 500–3 500 грн / 1 день
🏠 Квартира — 3 000 грн / 1 день
🏡 Будинок — 3 000 грн / 1 день
🌿 Земля — 3 000 грн / 1 день
🏭 Нежитлова нерухомість — від 4 500 грн / 2–3 дні

Для СУДУ:
🚗 Авто — 2 000–3 000 грн / 1 день
🏠 Квартира — 2 500 грн / 1 день
🏡 Будинок — 2 500 грн / 1 день
🌿 Земля — 2 500 грн / 1 день
🏭 Нежитлова нерухомість — від 4 000 грн / 2–3 дні

ДОКУМЕНТИ ДЛЯ ОЦІНКИ АВТО: техпаспорт, паспорт власника, фото авто з 4 сторін, фото салону та VIN-коду.
ДОКУМЕНТИ ДЛЯ КВАРТИРИ: правовстановлюючий документ, технічний паспорт, паспорт, фото кімнат.
ДОКУМЕНТИ ДЛЯ БУДИНКУ: документи на будинок і землю, технічний паспорт, паспорт, фото.
ДОКУМЕНТИ ДЛЯ ЗЕМЛІ: документ на землю, паспорт, фото ділянки.
ДОКУМЕНТИ ДЛЯ НЕЖИТЛОВОЇ: правовстановлюючий документ, техпаспорт, документи юрособи/паспорт, фото.

КОНТАКТИ ОЦІНКА24:
Телефон: +38 (050) 3000-173 (Пн-Пт 09:00-18:00, Сб 09:00-14:00)
Сайт: ocenka24.com.ua

КОЛИ ПИСАТИ "ESCALATE" (ТІЛЬКИ у таких випадках):
— Клієнт явно незадоволений і просить людину
— Питання про конкретну судову справу з деталями
— Клієнт питає про послугу якої немає в прайсі (оцінка бізнесу, збитки від війни тощо)

КОЛИ ПИСАТИ "ORDER:тип" (після того як клієнт сказав що хоче замовити):
— ORDER:car — оцінка авто
— ORDER:flat — оцінка квартири
— ORDER:house — оцінка будинку
— ORDER:land — оцінка землі
— ORDER:nonres — нежитлова нерухомість

ПРАВИЛА ВІДПОВІДЕЙ:
— Мова: ТІЛЬКИ українська
— Довжина: 3-5 речень, конкретно і по суті
— НЕ починай відповідь з "ESCALATE" якщо можеш відповісти сам
— Якщо не знаєш точну відповідь — дай загальну інформацію та запропонуй уточнити по телефону
— Будь доброзичливим та корисним"""


def _init_gemini():
    global gemini_model
    if not _gemini_available or not GEMINI_API_KEY:
        return
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel(
            "gemini-1.5-flash",
            system_instruction=_AI_SYSTEM_PROMPT,
        )
        logger.info("✅ Gemini AI підключено")
    except Exception as _e:
        logger.warning(f"Gemini init failed: {_e}")


_init_gemini()


async def _ask_gemini(history: list[dict], user_msg: str) -> str:
    if not gemini_model:
        logger.warning("Gemini недоступний — GEMINI_API_KEY або ліцензія")
        return "Вибачте, AI-консультант тимчасово недоступний. Зверніться до менеджера або зателефонуйте нам."
    try:
        loop = asyncio.get_running_loop()
        # generate_content з повною історією — надійніше ніж start_chat+send_message
        contents = history + [{"role": "user", "parts": [user_msg]}]

        def _sync():
            return gemini_model.generate_content(contents)

        response = await loop.run_in_executor(None, _sync)
        text = response.text.strip()
        logger.info(f"Gemini OK ({len(text)} chars): {text[:120]}")
        return text
    except Exception as e:
        logger.error(f"Gemini error [{type(e).__name__}]: {e}", exc_info=True)
        return f"__GEMINI_ERROR__: {type(e).__name__}: {str(e)[:300]}"


async def handle_ai_chat(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    msg = upd.message
    if not msg or not msg.text:
        return AI_CHAT

    u = msg.from_user
    if _is_banned(u.id):
        await msg.reply_text("⛔ Доступ обмежено. Зверніться до підтримки.")
        return MENU
    if _rate_check(u.id):
        await msg.reply_text("⏳ Ви надсилаєте повідомлення надто швидко. Зачекайте хвилину.")
        return AI_CHAT

    history = ctx.user_data.setdefault("ai_history", [])
    user_text = msg.text.strip()

    await ctx.bot.send_chat_action(msg.chat_id, "typing")

    try:
        reply = await _ask_gemini(history, user_text)
    except Exception as e:
        logger.error(f"_ask_gemini unexpected error: {e}", exc_info=True)
        await msg.reply_text("Сталася технічна помилка. Спробуйте ще раз.")
        return AI_CHAT

    # Діагностика помилки Gemini — клієнт бачить нейтральне, адмін — деталі
    if reply.startswith("__GEMINI_ERROR__:"):
        err_detail = reply[len("__GEMINI_ERROR__:"):]
        for aid in ADMIN_IDS:
            try:
                await ctx.bot.send_message(
                    aid,
                    f"⚠️ Gemini API помилка:\n`{err_detail}`",
                    parse_mode="Markdown")
            except Exception:
                pass
        await msg.reply_text(
            "AI-консультант тимчасово не відповідає. "
            "Спробуйте пізніше або зверніться до менеджера.",
            reply_markup=InlineKeyboardMarkup([
                *([[InlineKeyboardButton("💬 Написати менеджеру", url=MANAGER_TG_URL)]] if MANAGER_TG_URL else []),
                [InlineKeyboardButton("🏠 Головне меню", callback_data="home")],
            ]))
        return MENU

    # Додаємо в історію
    history.append({"role": "user", "parts": [user_text]})
    history.append({"role": "model", "parts": [reply]})

    # Обрізаємо історію (не більше 20 повідомлень)
    if len(history) > 20:
        history[:2] = []

    # Перевіряємо команди від AI
    first_word = reply.split()[0].upper() if reply.split() else ""
    if first_word == "ESCALATE":
        clean = reply[8:].strip(": \n")
        if clean:
            try:
                await msg.reply_text(clean)
            except Exception:
                pass

        ts = datetime.now().strftime("%d.%m.%Y %H:%M")
        phone = ctx.user_data.get("phone", "—")
        ai_summary = "\n".join(
            f"{'Клієнт' if m['role']=='user' else 'AI'}: {m['parts'][0][:200]}"
            for m in history
            if m["role"] in ("user", "model")
        )[-1000:]

        client_link = InlineKeyboardMarkup([[
            InlineKeyboardButton("✉️ Написати клієнту", url=f"tg://user?id={u.id}"),
        ]])
        await notify(ctx,
            f"🤖 *AI ПЕРЕДАЄ СПЕЦІАЛІСТУ*\n"
            f"{'─'*28}\n"
            f"👤 *{u.full_name}*\n"
            f"🆔 `{u.id}` | @{u.username or '—'}\n"
            f"📱 {phone}\n"
            f"🕐 {ts}\n\n"
            f"📋 *Контекст:*\n{ai_summary[:800]}",
            kb=client_link)

        kb = InlineKeyboardMarkup([
            *([[InlineKeyboardButton("💬 Написати менеджеру", url=MANAGER_TG_URL)]] if MANAGER_TG_URL else []),
            [InlineKeyboardButton("📋 Замовити оцінку", callback_data="pre_order")],
            [InlineKeyboardButton("🏠 Головне меню",    callback_data="home")],
        ])
        await msg.reply_text(
            "Ваш запит передано спеціалісту ОЦІНКА24. "
            "Менеджер зв'яжеться з вами найближчим часом.\n\n"
            f"📞 Або зателефонуйте: {PHONE2}",
            reply_markup=kb)
        ctx.user_data.pop("ai_history", None)
        return MENU

    if reply.upper().startswith("ORDER:"):
        obj_key = reply.split(":")[1].strip().lower().split()[0]
        ctx.user_data.pop("ai_history", None)
        if obj_key in OBJECTS:
            if not ctx.user_data.get("phone"):
                ctx.user_data["pending_obj"] = obj_key
                await msg.reply_text(
                    "📱 Для оформлення замовлення вкажіть номер телефону:",
                    reply_markup=ReplyKeyboardMarkup(
                        [[KeyboardButton("📱 Поділитися номером", request_contact=True)]],
                        one_time_keyboard=True, resize_keyboard=True))
                return PHONE
            return await _open_object_by_key(upd, ctx, obj_key)
        else:
            await msg.reply_text(reply.split(":", 1)[1] if ":" in reply else reply,
                                 reply_markup=home_kb())
            return MENU

    # Звичайна відповідь
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Замовити оцінку", callback_data="pre_order")],
        [InlineKeyboardButton("💬 Менеджер",        callback_data="pre_write"),
         InlineKeyboardButton("🏠 Меню",            callback_data="home")],
    ])
    try:
        await msg.reply_text(reply[:4000], reply_markup=kb)
    except Exception as e:
        logger.warning(f"AI reply send error: {e}")
        await msg.reply_text(reply[:4000].encode("utf-8", "replace").decode("utf-8"), reply_markup=kb)
    return AI_CHAT


async def _open_object_by_key(upd: Update, ctx: ContextTypes.DEFAULT_TYPE,
                              key: str, purpose: str = "") -> int:
    icon, name, docs, _base_price, _base_term = OBJECTS[key]
    purpose = purpose or ctx.user_data.get("eval_purpose", "sale")
    price_str, term = _get_price(key, purpose)
    purpose_label = _PURPOSE_LABELS.get(purpose, "")

    ctx.user_data.update({
        "obj_key":      key,
        "obj_name":     f"{icon} {name}",
        "eval_purpose": purpose,
        "files":        [],
    })
    doc_list = "\n".join(f"  {i+1}. {d}" for i, d in enumerate(docs))
    await ctx.bot.send_message(
        upd.effective_chat.id,
        f"{icon} *{name}*\n"
        f"🎯 Мета: {purpose_label}\n"
        f"💰 Вартість: *{price_str}* | Строк: *{term}*\n\n"
        f"📋 *Необхідні документи:*\n{doc_list}\n\n"
        "Надсилайте фото та документи по одному.\n"
        "Після завершення натисніть *«✅ Завершити»*.",
        parse_mode="Markdown", reply_markup=object_kb())
    return UPLOAD


# ══════════════════════════════════════════════════════════
#  /start  та  /cancel
# ══════════════════════════════════════════════════════════

async def cmd_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    u = upd.effective_user
    logger.info(f"/start від {u.id}")

    if _is_banned(u.id):
        await (upd.message or upd.callback_query.message).reply_text(
            "⛔ Ваш акаунт заблоковано.")
        return MENU

    # Відновлюємо телефон з БД якщо в пам'яті немає
    if not ctx.user_data.get("phone"):
        saved = await _get_saved_phone(u.id)
        if saved:
            ctx.user_data["phone"] = saved
            logger.info(f"Телефон відновлено з БД для {u.id}: {saved}")

    name = (u.first_name or "").strip() or "Друже"
    has_phone = bool(ctx.user_data.get("phone"))

    if has_phone:
        text = f"З поверненням, {name}! Оберіть тип оцінки:"
        kb   = main_kb()
    else:
        text = (
            f"Вітаємо, {name}!\n\n"
            "ОЦІНКА24 — професійна незалежна оцінка майна по всій Україні.\n\n"
            f"Тел: {PHONE1}\nМоб: {PHONE2}\nСайт: {WEBSITE}"
        )
        kb = _start_kb()

    msg = upd.message or (upd.callback_query.message if upd.callback_query else None)
    if not msg:
        return MENU

    try:
        await msg.reply_text(text, reply_markup=kb)
    except Exception as e:
        logger.error(f"cmd_start: {e}")

    try:
        await msg.reply_photo(
            photo=LOGO,
            caption="🏢 ОЦІНКА24 — офіційний бот компанії\nocenka24.com.ua")
    except Exception:
        pass

    return MENU


async def cmd_cancel(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await upd.message.reply_text("❌ Скасовано.", reply_markup=ReplyKeyboardRemove())
    await upd.message.reply_text("🏠 Головне меню:", reply_markup=main_kb())
    return MENU


# ══════════════════════════════════════════════════════════
#  ТЕЛЕФОН
# ══════════════════════════════════════════════════════════

async def handle_phone(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    msg = upd.message
    u   = msg.from_user
    ts  = datetime.now().strftime("%d.%m.%Y %H:%M")

    if msg.contact:
        phone = msg.contact.phone_number
        if not phone.startswith("+"):
            phone = "+" + phone
    elif msg.text and (msg.text.startswith("+") or msg.text.startswith("0")):
        phone = msg.text.strip()
    else:
        await msg.reply_text("⚠️ Введіть номер у форматі +380XXXXXXXXX або 0XXXXXXXXX")
        return PHONE

    ctx.user_data["phone"] = phone
    await _save_user(u.id, u.username, u.full_name, phone)

    client_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✉️ Написати клієнту", url=f"tg://user?id={u.id}"),
    ]])
    await notify(ctx,
        f"📱 *НОВИЙ КЛІЄНТ*\n{'─'*28}\n"
        f"👤 {u.full_name}\n"
        f"🆔 `{u.id}` | @{u.username or '—'}\n"
        f"📱 `{phone}`\n🕐 {ts}",
        kb=client_kb)

    await msg.reply_text(
        f"✅ Дякуємо! Номер збережено.",
        reply_markup=ReplyKeyboardRemove())

    pending = ctx.user_data.pop("pending_obj", None)
    pending_purpose = ctx.user_data.pop("pending_purpose", "sale")
    if pending and pending in OBJECTS:
        return await _open_object_by_key(upd, ctx, pending, pending_purpose)

    await ctx.bot.send_message(upd.effective_chat.id, "🏠 Головне меню:", reply_markup=main_kb())
    return MENU


# ══════════════════════════════════════════════════════════
#  ГОЛОВНЕ МЕНЮ (callback)
# ══════════════════════════════════════════════════════════

async def on_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = upd.callback_query
    await q.answer()
    d = q.data
    u = q.from_user

    if _is_banned(u.id):
        await q.answer("⛔ Доступ обмежено.", show_alert=True)
        return MENU

    # Відновлюємо телефон з БД якщо немає в пам'яті
    if not ctx.user_data.get("phone"):
        saved = await _get_saved_phone(u.id)
        if saved:
            ctx.user_data["phone"] = saved
            logger.info(f"Телефон відновлено в on_menu для {u.id}: {saved}")

    async def send(text, kb=None, md=True):
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        kw = {"parse_mode": "Markdown"} if md else {}
        await ctx.bot.send_message(upd.effective_chat.id, text, reply_markup=kb, **kw)

    # AI-консультант
    if d == "ai_chat":
        ctx.user_data["ai_history"] = []
        await send(
            "🤖 *AI-консультант ОЦІНКА24*\n\n"
            "Вітаю! Я допоможу визначити тип оцінки, розрахувати вартість та оформити замовлення.\n\n"
            "Розкажіть, яке майно потрібно оцінити і для якої мети?",
            InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="home")]]))
        return AI_CHAT

    if d == "home":
        if ctx.user_data.get("phone"):
            await send("🏠 Головне меню:", main_kb())
        else:
            await send(
                f"🏢 *ОЦІНКА24*\n\n☎️ {PHONE1}\n📱 {PHONE2}\n🌐 {WEBSITE}",
                _start_kb())
        ctx.user_data.pop("ai_history", None)
        return MENU

    if d == "pre_order":
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        if ctx.user_data.get("phone"):
            await ctx.bot.send_message(
                upd.effective_chat.id,
                "🏠 Оберіть тип майна для оцінки:",
                reply_markup=main_kb())
            return MENU
        await ctx.bot.send_message(
            upd.effective_chat.id,
            "📱 *Для оформлення замовлення* вкажіть номер телефону:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📱 Поділитися номером телефону", request_contact=True)]],
                one_time_keyboard=True, resize_keyboard=True))
        return PHONE

    if d == "pre_info":
        await send(_PRICES_TEXT, InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Замовити оцінку", callback_data="pre_order")],
            [InlineKeyboardButton("◀️ Назад",           callback_data="home")],
        ]))
        return MENU

    if d == "pre_write":
        u2 = upd.effective_user
        ts = datetime.now().strftime("%d.%m.%Y %H:%M")
        for tid in ADMIN_IDS:
            try:
                await ctx.bot.send_message(
                    tid,
                    f"💬 *ЗАПИТ НА ЗВ'ЯЗОК*\n{'─'*24}\n"
                    f"👤 {u2.full_name}\n"
                    f"🆔 `{u2.id}` | @{u2.username or '—'}\n"
                    f"🕐 {ts}\n\n"
                    f"[✉️ Написати](tg://user?id={u2.id})",
                    parse_mode="Markdown")
            except Exception:
                pass
        manager_link = f"[менеджеру]({MANAGER_TG_URL})" if MANAGER_TG_URL else "менеджеру"
        await send(
            f"✅ Запит надіслано!\n\nМенеджер зв'яжеться найближчим часом.\n\n"
            f"Також можете написати {manager_link} або зателефонувати:\n📞 {PHONE2}",
            InlineKeyboardMarkup([
                *([[InlineKeyboardButton("💬 Написати в Telegram", url=MANAGER_TG_URL)]] if MANAGER_TG_URL else []),
                [InlineKeyboardButton("📋 Замовити оцінку", callback_data="pre_order")],
                [InlineKeyboardButton("◀️ Назад",           callback_data="home")],
            ]))
        return MENU

    if d == "about":
        await send(
            "🏢 *ОЦІНКА24*\n\n"
            "✅ Сертифіковані оцінювачі (ЗУ «Про оцінку майна»)\n"
            "✅ Досвід роботи понад 15 років\n"
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
            "🕐 *Графік:*\nПн–Пт: 09:00–18:00\nСб: 09:00–14:00\nНд: вихідний",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("ℹ️ Про компанію",  callback_data="about")],
                [InlineKeyboardButton("🏠 Головне меню",  callback_data="home")],
            ]))
        return MENU

    if d == "location":
        await send(
            "📍 *Геолокація об'єкта оцінки*\n\nПоділіться геолокацією або введіть адресу.",
            home_kb())
        await ctx.bot.send_message(upd.effective_chat.id,
            "👇 Надішліть геолокацію:", reply_markup=gps_kb())
        return LOC

    if d in ("photogps", "obj_photogps"):
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await ctx.bot.send_message(
            upd.effective_chat.id,
            "📸 *Фото з GPS*\n\n"
            "1️⃣ Натисніть кнопку нижче — надішліть геолокацію\n"
            "2️⃣ Зробіть НОВЕ фото камерою (не з галереї!)\n\n"
            "⚠️ Фото з галереї не містять GPS-координат.",
            parse_mode="Markdown")
        await ctx.bot.send_message(
            upd.effective_chat.id,
            "📍 НАДІСЛАТИ ГЕОЛОКАЦІЮ ОБ'ЄКТА",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📍 НАДІСЛАТИ ГЕОЛОКАЦІЮ ОБ'ЄКТА", request_location=True)]],
                one_time_keyboard=True, resize_keyboard=True))
        return PHOTOGPS

    if d in ("video", "obj_video"):
        return await start_video(upd, ctx)

    if d == "pgps_docs":
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await ctx.bot.send_message(
            upd.effective_chat.id,
            "📎 Надсилайте документи або фото.\n"
            "Після завершення натисніть «Завершити».",
            reply_markup=object_kb())
        return UPLOAD

    if d == "add_photo":
        files_count = len(ctx.user_data.get("files", []))
        cnt_text = f" (вже додано: {files_count})" if files_count else ""
        await send(
            f"📷 Надішліть фото{cnt_text}.\nМожна надіслати кілька — по одному.",
            object_kb())
        return UPLOAD

    if d == "done":
        return await finish_upload(upd, ctx)

    # Вибір мети оцінки
    if d.startswith("purpose|"):
        _, purpose, obj_key = d.split("|", 2)
        if not ctx.user_data.get("phone"):
            ctx.user_data["pending_obj"]     = obj_key
            ctx.user_data["pending_purpose"] = purpose
            try:
                await q.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            await ctx.bot.send_message(
                upd.effective_chat.id,
                "📱 *Для замовлення оцінки* вкажіть номер телефону:",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("📱 Поділитися номером", request_contact=True)]],
                    one_time_keyboard=True, resize_keyboard=True))
            return PHONE
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return await _open_object_by_key(upd, ctx, obj_key, purpose)

    key = d.replace("obj_", "")
    if key in OBJECTS:
        if not ctx.user_data.get("phone"):
            try:
                await q.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            await ctx.bot.send_message(
                upd.effective_chat.id,
                "📱 *Для замовлення оцінки* вкажіть номер телефону:",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("📱 Поділитися номером телефону", request_contact=True)]],
                    one_time_keyboard=True, resize_keyboard=True))
            ctx.user_data["pending_obj"] = key
            return PHONE
        # Спочатку запитуємо мету оцінки
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        icon, name, _, _, _ = OBJECTS[key]
        await ctx.bot.send_message(
            upd.effective_chat.id,
            f"{icon} *{name}*\n\n"
            "🎯 *Вкажіть мету оцінки*\n"
            "_(від мети залежить вартість та вид звіту)_",
            parse_mode="Markdown",
            reply_markup=_purpose_kb(key))
        return MENU

    return MENU


# ══════════════════════════════════════════════════════════
#  ДОКУМЕНТИ / ФАЙЛИ
# ══════════════════════════════════════════════════════════

async def show_object(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, key: str) -> int:
    return await _open_object_by_key(upd, ctx, key)


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
        await msg.reply_text("⚠️ Надішліть фото або документ.")
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
            pass

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
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    obj_key = ctx.user_data.get("obj_key", "")
    example = _COMMENT_EXAMPLES.get(obj_key, "стан об'єкта, площа, рік побудови")

    await ctx.bot.send_message(
        upd.effective_chat.id,
        "✅ *Файли отримано!*\n\n"
        "📝 Додайте короткий *опис об'єкта*:\n"
        f"_(наприклад: {example})_\n\n"
        "Або пропустіть цей крок:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ Пропустити опис", callback_data="comment_skip")],
        ]))
    return COMMENT


async def handle_comment(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["comment"] = upd.message.text.strip() if upd.message and upd.message.text else ""
    return await _ask_delivery(upd, ctx)


async def skip_comment(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = upd.callback_query
    await q.answer()
    ctx.user_data["comment"] = ""
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    return await _ask_delivery(upd, ctx)


async def _ask_delivery(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await ctx.bot.send_message(
        upd.effective_chat.id,
        "📦 *Дані для доставки звіту (Нова Пошта)*\n\n"
        "Надішліть одним повідомленням:\n"
        "• Місто та номер відділення НП\n"
        "• Прізвище та ім'я отримувача\n"
        "• Номер телефону отримувача\n\n"
        "_Приклад:_\n"
        "`Київ, відділення Нової Пошти №12\nМиколайчук Степан\n+380671234567`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ Пропустити", callback_data="delivery_skip")],
        ]))
    return DELIVERY


async def handle_delivery(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["delivery"] = upd.message.text.strip() if upd.message and upd.message.text else ""
    await _complete_request(upd, ctx)
    return MENU


async def skip_delivery(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = upd.callback_query
    await q.answer()
    ctx.user_data["delivery"] = ""
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _complete_request(upd, ctx)
    return MENU


async def _complete_request(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u        = upd.effective_user
    name     = ctx.user_data.get("obj_name", "—")
    obj_key  = ctx.user_data.get("obj_key", "")
    files    = ctx.user_data.get("files", [])
    comment  = ctx.user_data.get("comment", "")
    delivery = ctx.user_data.get("delivery", "")
    phone    = ctx.user_data.get("phone", "—")
    ai_sum   = ctx.user_data.get("ai_summary", "")
    ts       = datetime.now().strftime("%d.%m.%Y %H:%M")

    req_id = None
    if DATABASE_URL:
        conn = None
        try:
            conn = await _db_connect()
            await conn.execute("""
                INSERT INTO users (user_id, username, full_name, phone)
                VALUES ($1,$2,$3,$4)
                ON CONFLICT (user_id) DO UPDATE
                  SET username=$2, full_name=$3, phone=COALESCE($4, users.phone)
            """, u.id, u.username, u.full_name, phone if phone != "—" else None)
            req_id = await conn.fetchval("""
                INSERT INTO requests
                  (user_id, request_type, status, comment, delivery, ai_summary, files_count)
                VALUES ($1,$2,'new',$3,$4,$5,$6) RETURNING id
            """, u.id, name, comment or None, delivery or None,
                ai_sum or None, len(files))
            # Зберігаємо file_ids
            for fid in files:
                await conn.execute(
                    "INSERT INTO request_files (request_id, file_id, file_type) VALUES ($1,$2,$3)",
                    req_id, fid, "photo")
        except Exception as e:
            logger.warning(f"DB insert request: {e}")
        finally:
            if conn:
                await _db_release(conn)

    adm_text = (
        f"📋 *НОВА ЗАЯВКА #{req_id or '—'}*\n{'─'*28}\n"
        f"👤 {u.full_name}\n"
        f"🆔 `{u.id}` | @{u.username or '—'}\n"
        f"📱 {phone}\n🕐 {ts}\n\n"
        f"*{name}*\n📎 Файлів: *{len(files)}*"
    )
    if comment:
        adm_text += f"\n\n📝 *Опис:* {comment}"
    if delivery:
        adm_text += f"\n\n📦 *Нова Пошта:*\n{delivery}"
    if ai_sum:
        adm_text += f"\n\n🤖 *AI-резюме:* {ai_sum[:300]}"
    adm_kb_rows: list[list[InlineKeyboardButton]] = []
    if req_id:
        adm_kb_rows.append([
            InlineKeyboardButton("🔄 В роботі", callback_data=f"st|{req_id}|in_progress"),
            InlineKeyboardButton("✅ Виконано",  callback_data=f"st|{req_id}|done"),
        ])
        adm_kb_rows.append([
            InlineKeyboardButton("📝 Нотатка",    callback_data=f"adm|note|{req_id}"),
            InlineKeyboardButton("📅 Дедлайн",   callback_data=f"adm|deadline|{req_id}"),
        ])
        adm_kb_rows.append([
            InlineKeyboardButton("📨 Написати клієнту", callback_data=f"adm|msg|{req_id}"),
        ])
    else:
        adm_kb_rows.append([
            InlineKeyboardButton("✉️ Написати клієнту", url=f"tg://user?id={u.id}"),
        ])
    adm_kb = InlineKeyboardMarkup(adm_kb_rows)
    await notify(ctx, adm_text, kb=adm_kb)

    _obj_names = {
        "car":    "транспортного засобу",
        "flat":   "квартири",
        "house":  "житлового будинку",
        "land":   "земельної ділянки",
        "nonres": "нежитлової нерухомості",
    }
    obj_label = _obj_names.get(obj_key, "об'єкта")

    client_text = (
        f"✅ Документи на оцінку {obj_label} надіслані!\n\n"
        "Оцінювач розгляне вашу заявку та зв'яжеться найближчим часом.\n\n"
    )
    if delivery:
        client_text += f"📦 Доставка звіту:\n{delivery}\n\n"
    client_text += f"Зв'язатися з оцінювачем:\n📞 {PHONE2}"

    kb_rows = []
    if MANAGER_TG_URL:
        kb_rows.append([InlineKeyboardButton("💬 Написати оцінювачу", url=MANAGER_TG_URL)])
    kb_rows.append([InlineKeyboardButton("🏠 Головне меню", callback_data="home")])

    await ctx.bot.send_message(upd.effective_chat.id, client_text,
                               reply_markup=InlineKeyboardMarkup(kb_rows))


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
            f"👤 {u.full_name} | 🆔 `{u.id}`\n"
            f"🕐 {ts}\n📌 `{lat:.6f}, {lon:.6f}`\n"
            f"🗺 [Google Maps]({maps})\n[✉️ Написати](tg://user?id={u.id})")
        await notify_loc(ctx, lat, lon)
        await msg.reply_text(
            f"✅ *Геолокацію зафіксовано!*\n📌 `{lat:.5f}, {lon:.5f}`\n🗺 [Google Maps]({maps})",
            parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    elif msg.text and not msg.text.startswith("/"):
        await notify(ctx,
            f"📍 *АДРЕСА ОБ'ЄКТА*\n👤 {u.full_name} | 🕐 {ts}\n"
            f"📬 {msg.text.strip()}\n[✉️ Написати](tg://user?id={u.id})")
        await msg.reply_text(
            f"✅ *Адресу зафіксовано!*\n📬 {msg.text.strip()}",
            parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    else:
        await msg.reply_text("⚠️ Поділіться геолокацією або введіть адресу.")
        return LOC

    await ctx.bot.send_message(msg.chat.id, "Дякуємо!", reply_markup=main_kb())
    return MENU


# ══════════════════════════════════════════════════════════
#  ФОТО + GPS
# ══════════════════════════════════════════════════════════

_FONT_CACHE: dict = {}
_FONT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "font_cyr.ttf")
_LOGO_CACHE: Image.Image | None = None

_FONT_PATHS = [
    _FONT_FILE,
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
]


def _load_logo_sync() -> "Image.Image | None":
    global _LOGO_CACHE
    if _LOGO_CACHE is not None:
        return _LOGO_CACHE
    import urllib.request
    try:
        req = urllib.request.Request(LOGO, headers={"User-Agent": "Otsinka24Bot/6.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read()
        _LOGO_CACHE = Image.open(BytesIO(data)).convert("RGBA")
        return _LOGO_CACHE
    except Exception as e:
        logger.warning(f"Логотип не завантажено: {e}")
        return None


def _ensure_cyrillic_font_sync():
    for p in _FONT_PATHS[1:]:
        if os.path.exists(p):
            return
    if os.path.exists(_FONT_FILE):
        return
    import urllib.request
    urls = [
        "https://github.com/liberationfonts/liberation-fonts/raw/main/Liberation-fonts-ttf-2.1.5/LiberationSans-Bold.ttf",
        "https://github.com/notofonts/noto-fonts/raw/main/hinted/ttf/NotoSans/NotoSans-Bold.ttf",
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Otsinka24Bot/6.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            with open(_FONT_FILE, "wb") as f:
                f.write(data)
            logger.info(f"✅ Шрифт завантажено")
            return
        except Exception as e:
            logger.warning(f"Шрифт: {e}")


def _load_font(size: int):
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


def _text_h(draw, text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]


def _text_w(draw, text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


def _wrap_text(draw, text, font, max_width):
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
        req = urllib.request.Request(url, headers={"User-Agent": "Otsinka24Bot/6.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read()
    try:
        return await loop.run_in_executor(None, _sync)
    except Exception as e:
        logger.warning(f"Fetch {url[:60]}: {e}")
        return None


async def _get_address(lat: float, lon: float) -> str:
    import json
    if gmaps:
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, lambda: gmaps.reverse_geocode((lat, lon), language="uk"))
            if result:
                return result[0]["formatted_address"]
        except Exception as e:
            logger.warning(f"Google Maps: {e}")

    url = (f"https://nominatim.openstreetmap.org/reverse"
           f"?lat={lat}&lon={lon}&format=json&accept-language=uk&zoom=18")
    data = await _fetch_async(url)
    if data:
        try:
            j = json.loads(data)
            addr = j.get("display_name", "")
            if addr:
                return addr
        except Exception:
            pass

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
            return ", ".join(parts)
        except Exception:
            pass
    return ""


async def _get_map(lat: float, lon: float, px: int = 450) -> "Image.Image | None":
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


def _draw_shadow(draw, pos, text, font, fill, shadow=(0, 0, 0, 200), offset=3):
    x, y = pos
    draw.text((x + offset, y + offset), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


async def build_geotagged_photo(photo_bytes: bytes, lat=None, lon=None,
                                address="", ts="") -> BytesIO:
    img = Image.open(BytesIO(photo_bytes))
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGBA")
    W, H = img.size

    base = max(int(H * 0.013), 8)
    pad  = max(4, base // 3)
    f_label = _load_font(int(base * 1.2))
    f_gps   = _load_font(int(base * 1.6))
    f_addr  = _load_font(int(base * 1.4))
    f_site  = _load_font(base)

    tmp_draw = ImageDraw.Draw(img)

    map_img = None
    map_px  = min(int(W * 0.38), 400)
    if lat and lon:
        map_img = await _get_map(lat, lon, map_px)

    lh_label = _text_h(tmp_draw, "К", f_label) + 4
    lh_gps   = _text_h(tmp_draw, "0", f_gps)   + 8
    lh_addr  = _text_h(tmp_draw, "А", f_addr)  + 6
    lh_site  = _text_h(tmp_draw, "0", f_site)  + 4

    addr_lines = _wrap_text(tmp_draw, address, f_addr, W - pad * 2) if address else []
    panel_h = (pad + lh_label + lh_gps + pad // 2
               + (lh_label + lh_addr * len(addr_lines) + pad // 2 if addr_lines else 0)
               + lh_site + pad)

    panel_top = H - panel_h
    overlay = Image.new("RGBA", (W, panel_h), (8, 12, 35, 220))
    img.paste(overlay, (0, panel_top), overlay)
    sep = Image.new("RGBA", (W, 4), (255, 215, 0, 210))
    img.paste(sep, (0, panel_top), sep)

    draw = ImageDraw.Draw(img)

    # Логотип
    logo_h = 0
    logo_src = _load_logo_sync()
    if logo_src:
        logo_w = max(int(W * 0.18), 80)
        lw, lh = logo_src.size
        logo_h_px = int(lh * logo_w / lw)
        logo_res = logo_src.resize((logo_w, logo_h_px), Image.LANCZOS)
        bg = Image.new("RGBA", (logo_w + pad * 2, logo_h_px + pad * 2), (0, 0, 0, 140))
        img.paste(bg, (W - logo_w - pad * 3, pad // 2), bg)
        img.paste(logo_res, (W - logo_w - pad * 2, pad), logo_res)
        logo_h = logo_h_px + pad * 2

    # Карта
    if map_img:
        mw, mh = map_img.size
        border = 5
        frame = Image.new("RGBA", (mw + border * 2, mh + border * 2), (255, 215, 0, 255))
        frame.paste(map_img, (border, border))
        map_y = pad + logo_h + pad
        if map_y + mh + border * 2 < panel_top - pad:
            img.paste(frame, (pad, map_y))

    # Панель
    tx, y = pad, panel_top + pad
    _draw_shadow(draw, (tx, y), "КООРДИНАТИ:", f_label, fill=(255, 215, 0, 190))
    y += lh_label
    gps_text = f"{lat:.6f},  {lon:.6f}" if (lat and lon) else "—"
    _draw_shadow(draw, (tx, y), gps_text, f_gps, fill=(255, 255, 255, 255), offset=2)
    y += lh_gps + pad // 2

    if addr_lines:
        _draw_shadow(draw, (tx, y), "АДРЕСА:", f_label, fill=(255, 215, 0, 190))
        y += lh_label
        for line in addr_lines:
            _draw_shadow(draw, (tx, y), line, f_addr, fill=(160, 220, 255, 255), offset=2)
            y += lh_addr

    site_w = _text_w(draw, WEBSITE, f_site)
    _draw_shadow(draw, (W - site_w - pad, y), WEBSITE, f_site, fill=(180, 180, 255, 200))

    out = BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=94, optimize=True)
    out.seek(0)
    return out


def _after_gps_photo_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Ще фото з GPS",              callback_data="obj_photogps")],
        [InlineKeyboardButton("📎 Додати документи",           callback_data="pgps_docs")],
        [InlineKeyboardButton("✅ Завершити замовлення оцінки", callback_data="done")],
    ])


async def handle_photogps(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    msg = upd.message
    u   = msg.from_user
    ts  = datetime.now().strftime("%d.%m.%Y %H:%M")

    if msg.location:
        ctx.user_data["pgps_lat"] = msg.location.latitude
        ctx.user_data["pgps_lon"] = msg.location.longitude
        if "pgps_photo" in ctx.user_data:
            photo_bytes = ctx.user_data.pop("pgps_photo")
            await _process_gps_photo(msg, u, ctx, photo_bytes,
                                     msg.location.latitude, msg.location.longitude, ts)
        else:
            await msg.reply_text("GPS збережено! Тепер зробіть фото камерою.",
                                 reply_markup=ReplyKeyboardRemove())
        return PHOTOGPS

    if msg.photo:
        lat = ctx.user_data.get("pgps_lat")
        lon = ctx.user_data.get("pgps_lon")
        pfile = await msg.photo[-1].get_file()
        photo_bytes = bytes(await pfile.download_as_bytearray())

        if lat is None:
            files = ctx.user_data.setdefault("files", [])
            files.append(msg.photo[-1].file_id)
            await notify_photo(ctx, msg.photo[-1].file_id,
                f"{ctx.user_data.get('obj_name','Фото')}\n"
                f"👤 {u.full_name} | ☎️ {ctx.user_data.get('phone','—')}\n(без геолокації)")
            await msg.reply_text("📎 Фото збережено без геотегу.",
                                 reply_markup=_after_gps_photo_kb())
            return PHOTOGPS

        await _process_gps_photo(msg, u, ctx, photo_bytes, lat, lon, ts)
        return PHOTOGPS

    await msg.reply_text("Надішліть фото або геолокацію.")
    return PHOTOGPS


async def _process_gps_photo(msg, u, ctx, photo_bytes: bytes, lat, lon, ts):
    await msg.reply_text("Обробляю фото...", reply_markup=ReplyKeyboardRemove())
    address   = await _get_address(lat, lon)
    processed = await build_geotagged_photo(photo_bytes, lat, lon, address, ts)

    processed.seek(0)
    sent = await msg.reply_photo(processed,
        caption=f"Фото з геотегом ОЦІНКА24\nGPS: {lat:.6f}, {lon:.6f}")
    if sent and sent.photo:
        ctx.user_data.setdefault("files", []).append(sent.photo[-1].file_id)

    maps = f"https://maps.google.com/?q={lat},{lon}"
    adm_caption = (
        f"📸 *ФОТО+GPS*\n{'─'*24}\n"
        f"👤 {u.full_name} | `{u.id}`\n🕐 {ts}\n"
        f"📌 `{lat:.6f}, {lon:.6f}`\n🗺 [Google Maps]({maps})"
    )
    if address:
        adm_caption += f"\n📬 {address[:120]}"
    adm_caption += f"\n[✉️ Написати](tg://user?id={u.id})"

    processed.seek(0)
    await notify_photo(ctx, processed, adm_caption)

    for k in ("pgps_lat", "pgps_lon", "pgps_photo"):
        ctx.user_data.pop(k, None)

    await ctx.bot.send_message(msg.chat.id, "Що робимо далі?",
                               reply_markup=_after_gps_photo_kb())


# ══════════════════════════════════════════════════════════
#  ВІДЕООГЛЯД
# ══════════════════════════════════════════════════════════

async def start_video(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    u    = upd.effective_user
    ts   = datetime.now().strftime("%d.%m.%Y %H:%M")
    room = f"Otsinka24-{uuid.uuid4().hex[:12].upper()}"
    url  = f"https://meet.jit.si/{room}"
    ctx.user_data["jitsi"] = url

    phone = ctx.user_data.get("phone", "—")
    await notify(ctx,
        f"📹 *ВІДЕООГЛЯД*\n{'─'*28}\n"
        f"👤 {u.full_name} | 🆔 `{u.id}`\n"
        f"📱 @{u.username or '—'} | ☎️ {phone}\n🕐 {ts}\n\n"
        f"🔗 `{room}`\n[📹 Приєднатися]({url})\n"
        f"[✉️ Написати](tg://user?id={u.id})")

    try:
        await upd.callback_query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await ctx.bot.send_message(upd.effective_chat.id,
        "📹 *Відеоогляд розпочато!*\nОцінювач незабаром приєднається.",
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
    jitsi = ctx.user_data.get("jitsi", "")

    if msg.location:
        lat, lon = msg.location.latitude, msg.location.longitude
        maps = f"https://maps.google.com/?q={lat},{lon}"
        await notify(ctx,
            f"📍 *GPS (відеоогляд)*\n👤 {u.full_name} | 🕐 {ts}\n"
            f"📌 `{lat:.6f}, {lon:.6f}`\n🗺 [Google Maps]({maps})")
        await notify_loc(ctx, lat, lon)
        await msg.reply_text(f"GPS зафіксовано: {lat:.5f}, {lon:.5f}",
                             reply_markup=ReplyKeyboardRemove())
    elif msg.text and not msg.text.startswith("/"):
        await notify(ctx, f"📍 *АДРЕСА (відеоогляд)*\n👤 {u.full_name}\n📬 {msg.text.strip()}")
        await msg.reply_text(f"Адресу зафіксовано: {msg.text.strip()}",
                             reply_markup=ReplyKeyboardRemove())
    else:
        await msg.reply_text("Поділіться геолокацією або введіть адресу.")
        return VIDEOLOC

    video_kb = InlineKeyboardMarkup([
        *([[InlineKeyboardButton("📹 Увійти у відеодзвінок", url=jitsi)]] if jitsi else []),
        [InlineKeyboardButton("📸 Ще фото з GPS",              callback_data="obj_photogps")],
        [InlineKeyboardButton("📎 Додати документи",           callback_data="pgps_docs")],
        [InlineKeyboardButton("✅ Завершити замовлення оцінки", callback_data="done")],
    ])
    await ctx.bot.send_message(msg.chat.id, "Що робимо далі?", reply_markup=video_kb)
    return UPLOAD


# ══════════════════════════════════════════════════════════
#  АДМІН-ПАНЕЛЬ + 2FA
# ══════════════════════════════════════════════════════════

async def admin_panel(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    u = upd.effective_user
    if u.id not in ADMIN_IDS:
        await upd.message.reply_text("⛔ Доступ заборонено.")
        await _log_security(u.id, "ADMIN_DENIED", f"@{u.username}")
        return MENU

    code = _gen_2fa(u.id)
    ts   = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    await upd.message.reply_text(
        f"🔐 *Підтвердження входу — ОЦІНКА24 Адмін*\n\n"
        f"👤 User ID: `{u.id}`\n"
        f"🕐 Час запиту: `{ts}`\n\n"
        f"Натисніть кнопку щоб увійти.\n"
        f"⏱ Кнопка дійсна 5 хвилин.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"✅ Підтвердити вхід (ID: {u.id})",
                callback_data=f"2fa|{u.id}|{code}")
        ]]))
    await _log_security(u.id, "ADMIN_2FA_SENT", f"ts={ts}")
    return ADMIN_2FA


async def handle_admin_2fa_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = upd.callback_query
    await q.answer()
    u = q.from_user

    parts = q.data.split("|")          # ["2fa", user_id, code]
    if len(parts) != 3:
        await q.edit_message_text("⚠️ Невірний формат запиту. Введіть /admin знову.")
        return MENU

    claimed_uid = int(parts[1])
    code        = parts[2]

    # Перевірка: той хто натискає = той хто запросив = адмін
    if u.id not in ADMIN_IDS or u.id != claimed_uid:
        await _log_security(u.id, "ADMIN_2FA_WRONG_USER",
                            f"claimed={claimed_uid}, actual={u.id}")
        await q.answer("⛔ Доступ заборонено — невідповідність User ID", show_alert=True)
        return MENU

    if _verify_2fa(u.id, code):
        await _log_security(u.id, "ADMIN_2FA_OK", "")
        await _show_admin_home(q, ctx)
        return ADMIN
    else:
        await _log_security(u.id, "ADMIN_2FA_EXPIRED", "")
        await q.edit_message_text(
            "⏱ Час дії кнопки минув.\n\nВведіть /admin щоб отримати новий запит.")
        return MENU


async def _show_admin_home(msg_or_query, ctx):
    db_ok = bool(DATABASE_URL)
    users_cnt = active_cnt = work_cnt = done_cnt = all_cnt = "—"

    if db_ok:
        conn = None
        try:
            conn = await _db_connect()
            users_cnt  = await conn.fetchval("SELECT COUNT(*) FROM users")
            active_cnt = await conn.fetchval("SELECT COUNT(*) FROM requests WHERE status='new'")
            work_cnt   = await conn.fetchval("SELECT COUNT(*) FROM requests WHERE status='in_progress'")
            done_cnt   = await conn.fetchval("SELECT COUNT(*) FROM requests WHERE status='done'")
            all_cnt    = await conn.fetchval("SELECT COUNT(*) FROM requests")
            db_ok = True
        except Exception as e:
            logger.warning(f"Admin home DB: {e}")
            db_ok = False
        finally:
            if conn:
                await _db_release(conn)

    db_status    = "✅ підключена" if db_ok else "⚠️ не налаштована"
    gmaps_status = "✅ активний"   if gmaps else "⚠️ не налаштований"
    ai_status    = "✅ активний"   if gemini_model else "⚠️ не налаштований"

    text = (
        "🔐 *Адмін-панель ОЦІНКА24 v6.0*\n"
        f"{'─' * 28}\n"
        f"🗄 БД: {db_status}\n"
        f"🗺 Google Maps: {gmaps_status}\n"
        f"🤖 Gemini AI: {ai_status}\n"
        f"🛡 Заблоковано: *{len(_banned_users)}* користувачів\n"
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
    q = upd.callback_query
    await q.answer()

    if q.from_user.id not in ADMIN_IDS:
        await q.answer("⛔ Доступ заборонено", show_alert=True)
        return ADMIN

    data = q.data

    # 2FA підтвердження
    if data.startswith("2fa|"):
        return await handle_admin_2fa_callback(upd, ctx)

    if data == "adm|home":
        await _show_admin_home(q, ctx)
        return ADMIN
    if data == "adm|active":
        return await _admin_list(q, "new", "📋 Нові заявки")
    if data == "adm|all":
        return await _admin_list(q, None, "📊 Всі заявки")
    if data == "adm|stats":
        return await _admin_stats(q)
    if data == "adm|clients":
        return await _admin_clients(q)
    if data == "adm|bans":
        return await _admin_bans(q, ctx)
    if data.startswith("adm|view|"):
        req_id = int(data.split("|")[2])
        return await _admin_view_request(q, req_id)
    if data.startswith("adm|unban|"):
        uid = int(data.split("|")[2])
        _banned_users.discard(uid)
        await q.edit_message_text(f"✅ Користувача `{uid}` розблоковано.",
                                  parse_mode="Markdown",
                                  reply_markup=InlineKeyboardMarkup([[
                                      InlineKeyboardButton("← Назад", callback_data="adm|bans")
                                  ]]))
        return ADMIN
    if data.startswith("adm|report|"):
        req_id = int(data.split("|")[2])
        return await _admin_toggle_report(q, req_id)
    if data.startswith("adm|note|"):
        req_id = int(data.split("|")[2])
        ctx.user_data["crm_note_req_id"] = req_id
        await q.edit_message_text(
            f"📝 *Нотатка до заявки #{req_id}*\n\n"
            "Введіть нотатку (наприклад: «Клієнт передзвонить у вівторок»):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Скасувати", callback_data=f"adm|view|{req_id}")
            ]]))
        return ADMIN
    if data.startswith("adm|deadline|"):
        req_id = int(data.split("|")[2])
        ctx.user_data["crm_deadline_req_id"] = req_id
        await q.edit_message_text(
            f"📅 *Дедлайн для заявки #{req_id}*\n\n"
            "Введіть дату виконання (наприклад: «25.07.2026» або «до п'ятниці»):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Скасувати", callback_data=f"adm|view|{req_id}")
            ]]))
        return ADMIN
    if data.startswith("adm|msg|"):
        req_id = int(data.split("|")[2])
        ctx.user_data["crm_msg_req_id"] = req_id
        await q.edit_message_text(
            f"📨 *Повідомлення клієнту (заявка #{req_id})*\n\n"
            "Введіть текст повідомлення:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Скасувати", callback_data=f"adm|view|{req_id}")
            ]]))
        return ADMIN
    if data.startswith("st|"):
        _, req_id, new_status = data.split("|")
        return await _admin_set_status(q, ctx, int(req_id), new_status)

    return ADMIN


async def _admin_list(q, status_filter, title):
    if not DATABASE_URL:
        await q.edit_message_text("⚠️ БД не налаштована.")
        return ADMIN
    conn = None
    rows = []
    try:
        conn = await _db_connect()
        if status_filter:
            rows = await conn.fetch("""
                SELECT r.id, r.request_type, r.status, r.created_at,
                       u.full_name, u.phone
                FROM requests r JOIN users u ON r.user_id = u.user_id
                WHERE r.status=$1
                ORDER BY r.created_at DESC LIMIT 20
            """, status_filter)
        else:
            rows = await conn.fetch("""
                SELECT r.id, r.request_type, r.status, r.created_at,
                       u.full_name, u.phone
                FROM requests r JOIN users u ON r.user_id = u.user_id
                ORDER BY r.created_at DESC LIMIT 20
            """)
    except Exception as e:
        await q.edit_message_text(f"⚠️ Помилка БД: {e}")
        return ADMIN
    finally:
        if conn:
            await _db_release(conn)

    if not rows:
        await q.edit_message_text(
            f"{title}\n\nЗаявок не знайдено.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Назад", callback_data="adm|home")
            ]]))
        return ADMIN

    icons = {"new": "🆕", "in_progress": "🔄", "done": "✅", "rejected": "❌"}
    text = f"*{title}* ({len(rows)}):\n\n"
    keyboard = []
    for r in rows:
        icon = icons.get(r["status"], "❓")
        dt   = r["created_at"].strftime("%d.%m %H:%M") if r["created_at"] else "—"
        label = f"{icon} #{r['id']} {r['request_type'][:20]} | {r['full_name'][:15]} | {dt}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"adm|view|{r['id']}")])

    keyboard.append([InlineKeyboardButton("← Назад", callback_data="adm|home")])
    await q.edit_message_text(text, parse_mode="Markdown",
                              reply_markup=InlineKeyboardMarkup(keyboard))
    return ADMIN


def _crm_kb(req_id: int) -> InlineKeyboardMarkup:
    """Повна CRM-клавіатура для заявки."""
    return InlineKeyboardMarkup([
        # Статуси
        [InlineKeyboardButton("🆕 Нова",       callback_data=f"st|{req_id}|new"),
         InlineKeyboardButton("🔄 В роботі",   callback_data=f"st|{req_id}|in_progress")],
        [InlineKeyboardButton("✅ Виконано",    callback_data=f"st|{req_id}|done"),
         InlineKeyboardButton("❌ Відхилено",   callback_data=f"st|{req_id}|rejected")],
        # Дії
        [InlineKeyboardButton("📝 Додати нотатку", callback_data=f"adm|note|{req_id}"),
         InlineKeyboardButton("📅 Дедлайн",        callback_data=f"adm|deadline|{req_id}")],
        [InlineKeyboardButton("✅ Звіт готовий",    callback_data=f"adm|report|{req_id}"),
         InlineKeyboardButton("📨 Написати клієнту", callback_data=f"adm|msg|{req_id}")],
        # Навігація
        [InlineKeyboardButton("← До списку",    callback_data="adm|active"),
         InlineKeyboardButton("🏠 Адмін-меню",  callback_data="adm|home")],
    ])


async def _admin_view_request(q, req_id: int) -> int:
    conn = None
    req  = None
    files_count = 0
    messages = []
    try:
        conn = await _db_connect()
        req = await conn.fetchrow("""
            SELECT r.*, u.full_name, u.phone, u.username
            FROM requests r JOIN users u ON r.user_id = u.user_id
            WHERE r.id=$1
        """, req_id)
        if req:
            files_count = await conn.fetchval(
                "SELECT COUNT(*) FROM request_files WHERE request_id=$1", req_id) or 0
            messages = await conn.fetch("""
                SELECT sender, message, created_at
                FROM request_messages WHERE request_id=$1
                ORDER BY created_at DESC LIMIT 5
            """, req_id)
    except Exception as e:
        await q.edit_message_text(f"⚠️ Помилка БД: {e}")
        return ADMIN
    finally:
        if conn:
            await _db_release(conn)

    if not req:
        await q.edit_message_text("⚠️ Заявку не знайдено.")
        return ADMIN

    status_label = _STATUS_LABELS.get(req["status"], req["status"])
    report_icon  = "✅ ГОТОВИЙ" if req.get("report_ready") else "⏳ в роботі"
    maps_link    = ""
    if req.get("lat") and req.get("lon"):
        maps_link = f" [🗺 Карта](https://maps.google.com/?q={req['lat']},{req['lon']})"

    # Основна інформація
    text = (
        f"📋 *ЗАЯВКА #{req['id']}*\n"
        f"{'━' * 28}\n"
        f"📌 *{req['request_type']}*\n"
        f"🏷 Статус: {status_label}\n"
        f"📑 Звіт: *{report_icon}*\n"
    )
    if req.get("deadline"):
        text += f"📅 Дедлайн: *{req['deadline']}*\n"
    text += (
        f"{'─' * 28}\n"
        f"👤 *Клієнт:* {req['full_name']}\n"
        f"📱 `{req['phone'] or '—'}`\n"
        f"🆔 @{req['username'] or '—'} | `{req['user_id']}`\n"
    )
    if req.get("address"):
        text += f"📬 {req['address'][:100]}{maps_link}\n"
    elif maps_link:
        text += f"📍 GPS{maps_link}\n"
    text += (
        f"{'─' * 28}\n"
        f"📎 Файлів: *{files_count}*\n"
        f"🕐 Створено: {req['created_at'].strftime('%d.%m.%Y %H:%M') if req.get('created_at') else '—'}\n"
    )
    if req.get("updated_at") and req.get("updated_at") != req.get("created_at"):
        text += f"🔄 Оновлено: {req['updated_at'].strftime('%d.%m.%Y %H:%M')}\n"

    # Опис від клієнта
    if req.get("comment"):
        text += f"{'─' * 28}\n📝 *Опис:*\n{req['comment'][:300]}\n"

    # Нова Пошта
    if req.get("delivery"):
        text += f"{'─' * 28}\n📦 *Нова Пошта:*\n{req['delivery'][:200]}\n"

    # Нотатки адміна
    if req.get("admin_notes"):
        text += f"{'─' * 28}\n🔖 *Нотатки адміна:*\n{req['admin_notes'][:300]}\n"

    # AI-резюме
    if req.get("ai_summary"):
        text += f"{'─' * 28}\n🤖 *AI-резюме:*\n{req['ai_summary'][:250]}\n"

    # Останні повідомлення
    if messages:
        text += f"{'─' * 28}\n💬 *Останні повідомлення:*\n"
        for m in reversed(messages):
            sender_icon = "👨‍💼" if m["sender"] == "admin" else "👤"
            dt = m["created_at"].strftime("%d.%m %H:%M")
            text += f"{sender_icon} [{dt}] {m['message'][:80]}\n"

    text += f"\n[✉️ Написати клієнту](tg://user?id={req['user_id']})"

    await q.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=_crm_kb(req_id),
        disable_web_page_preview=True)
    return ADMIN


async def _admin_set_status(q, ctx, req_id: int, new_status: str) -> int:
    conn = None
    req  = None
    try:
        conn = await _db_connect()
        req = await conn.fetchrow("""
            SELECT r.*, u.full_name, u.user_id
            FROM requests r JOIN users u ON r.user_id = u.user_id
            WHERE r.id=$1
        """, req_id)
        await conn.execute(
            "UPDATE requests SET status=$1, updated_at=NOW() WHERE id=$2",
            new_status, req_id)
        await conn.execute(
            "INSERT INTO request_messages (request_id, sender, message) VALUES ($1,$2,$3)",
            req_id, "system", f"Статус змінено → {_STATUS_LABELS.get(new_status, new_status)}")
    except Exception as e:
        await q.edit_message_text(f"⚠️ Помилка БД: {e}")
        return ADMIN
    finally:
        if conn:
            await _db_release(conn)

    label = _STATUS_LABELS.get(new_status, new_status)
    await q.answer(f"✅ Статус → {label}")

    # Оновлюємо відображення картки заразом
    return await _admin_view_request(q, req_id)


async def _admin_toggle_report(q, req_id: int) -> int:
    conn = None
    try:
        conn = await _db_connect()
        current = await conn.fetchval("SELECT report_ready FROM requests WHERE id=$1", req_id)
        new_val = not current
        await conn.execute(
            "UPDATE requests SET report_ready=$1, updated_at=NOW() WHERE id=$2",
            new_val, req_id)
        if new_val:
            req = await conn.fetchrow(
                "SELECT r.user_id, r.request_type FROM requests r WHERE r.id=$1", req_id)
            await conn.execute(
                "INSERT INTO request_messages (request_id, sender, message) VALUES ($1,$2,$3)",
                req_id, "system", "Звіт готовий — клієнту надіслано сповіщення")
    except Exception as e:
        await q.answer(f"Помилка: {e}", show_alert=True)
        return ADMIN
    finally:
        if conn:
            await _db_release(conn)

    if new_val and req:
        try:
            await q._bot.send_message(
                req["user_id"],
                f"📄 *Звіт про оцінку готовий!*\n\n"
                f"Замовлення: *{req['request_type']}*\n\n"
                f"Для отримання звіту зв'яжіться з нами:\n📞 {PHONE2}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    *([[InlineKeyboardButton("💬 Написати менеджеру", url=MANAGER_TG_URL)]]
                      if MANAGER_TG_URL else [])
                ]))
        except Exception:
            pass

    await q.answer("✅ Звіт готовий — клієнту надіслано" if new_val else "⏳ Знято позначку")
    return await _admin_view_request(q, req_id)


async def _admin_stats(q) -> int:
    conn = None
    try:
        conn = await _db_connect()
        total_u = await conn.fetchval("SELECT COUNT(*) FROM users")
        today_u = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at::date = CURRENT_DATE")
        total_r = await conn.fetchval("SELECT COUNT(*) FROM requests")
        today_r = await conn.fetchval(
            "SELECT COUNT(*) FROM requests WHERE created_at::date = CURRENT_DATE")
        by_type   = await conn.fetch(
            "SELECT request_type, COUNT(*) cnt FROM requests GROUP BY request_type ORDER BY cnt DESC")
        by_status = await conn.fetch(
            "SELECT status, COUNT(*) cnt FROM requests GROUP BY status")
        ai_cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM requests WHERE ai_summary IS NOT NULL")
    except Exception as e:
        await q.edit_message_text(f"⚠️ Помилка БД: {e}")
        return ADMIN
    finally:
        if conn:
            await _db_release(conn)

    type_lines   = "\n".join(f"  • {r['request_type']}: *{r['cnt']}*" for r in by_type)
    status_lines = "\n".join(
        f"  • {_STATUS_LABELS.get(r['status'], r['status'])}: *{r['cnt']}*"
        for r in by_status)
    text = (
        "📈 *Статистика ОЦІНКА24*\n"
        f"{'─' * 28}\n"
        f"👥 Клієнтів: *{total_u}* (сьогодні: *{today_u}*)\n"
        f"📋 Заявок: *{total_r}* (сьогодні: *{today_r}*)\n"
        f"🤖 Через AI-консультант: *{ai_cnt}*\n\n"
        f"*По типах:*\n{type_lines or '—'}\n\n"
        f"*По статусах:*\n{status_lines or '—'}"
    )
    await q.edit_message_text(text, parse_mode="Markdown",
                              reply_markup=InlineKeyboardMarkup([[
                                  InlineKeyboardButton("← Назад", callback_data="adm|home")
                              ]]))
    return ADMIN


async def _admin_clients(q) -> int:
    conn = None
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
    except Exception as e:
        await q.edit_message_text(f"⚠️ Помилка БД: {e}")
        return ADMIN
    finally:
        if conn:
            await _db_release(conn)

    if not clients:
        await q.edit_message_text("👥 Клієнтів ще немає.",
                                  reply_markup=InlineKeyboardMarkup([[
                                      InlineKeyboardButton("← Назад", callback_data="adm|home")
                                  ]]))
        return ADMIN

    lines = []
    for c in clients:
        dt = c["created_at"].strftime("%d.%m.%Y") if c["created_at"] else "—"
        lines.append(
            f"👤 *{c['full_name']}* | `{c['phone'] or '—'}`\n"
            f"   @{c['username'] or '—'} | Заявок: {c['req_cnt']} | {dt}")
    text = f"👥 *Клієнти* (останні {len(clients)}):\n\n" + "\n\n".join(lines)
    await q.edit_message_text(text, parse_mode="Markdown",
                              reply_markup=InlineKeyboardMarkup([[
                                  InlineKeyboardButton("← Назад", callback_data="adm|home")
                              ]]))
    return ADMIN


async def handle_admin_text(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Обробляє введення адміна: нотатка / дедлайн / повідомлення клієнту."""
    msg = upd.message
    if not msg or not msg.text or msg.from_user.id not in ADMIN_IDS:
        return ADMIN

    text = msg.text.strip()

    # Нотатка
    if "crm_note_req_id" in ctx.user_data:
        req_id = ctx.user_data.pop("crm_note_req_id")
        conn = None
        try:
            conn = await _db_connect()
            await conn.execute(
                "UPDATE requests SET admin_notes=$1, updated_at=NOW() WHERE id=$2",
                text, req_id)
            await conn.execute(
                "INSERT INTO request_messages (request_id, sender, message) VALUES ($1,$2,$3)",
                req_id, "admin", f"[Нотатка] {text}")
        except Exception as e:
            await msg.reply_text(f"⚠️ Помилка: {e}")
            return ADMIN
        finally:
            if conn:
                await _db_release(conn)
        await msg.reply_text(f"✅ Нотатку збережено до заявки #{req_id}",
                             reply_markup=InlineKeyboardMarkup([[
                                 InlineKeyboardButton("← До заявки",
                                                      callback_data=f"adm|view|{req_id}")
                             ]]))
        return ADMIN

    # Дедлайн
    if "crm_deadline_req_id" in ctx.user_data:
        req_id = ctx.user_data.pop("crm_deadline_req_id")
        conn = None
        try:
            conn = await _db_connect()
            await conn.execute(
                "UPDATE requests SET deadline=$1, updated_at=NOW() WHERE id=$2",
                text, req_id)
        except Exception as e:
            await msg.reply_text(f"⚠️ Помилка: {e}")
            return ADMIN
        finally:
            if conn:
                await _db_release(conn)
        await msg.reply_text(f"✅ Дедлайн встановлено: *{text}*",
                             parse_mode="Markdown",
                             reply_markup=InlineKeyboardMarkup([[
                                 InlineKeyboardButton("← До заявки",
                                                      callback_data=f"adm|view|{req_id}")
                             ]]))
        return ADMIN

    # Повідомлення клієнту
    if "crm_msg_req_id" in ctx.user_data:
        req_id = ctx.user_data.pop("crm_msg_req_id")
        conn = None
        user_id = None
        req_type = "—"
        try:
            conn = await _db_connect()
            row = await conn.fetchrow(
                "SELECT user_id, request_type FROM requests WHERE id=$1", req_id)
            if row:
                user_id  = row["user_id"]
                req_type = row["request_type"]
            await conn.execute(
                "INSERT INTO request_messages (request_id, sender, message) VALUES ($1,$2,$3)",
                req_id, "admin", text)
            await conn.execute(
                "UPDATE requests SET updated_at=NOW() WHERE id=$1", req_id)
        except Exception as e:
            await msg.reply_text(f"⚠️ Помилка: {e}")
            return ADMIN
        finally:
            if conn:
                await _db_release(conn)

        if user_id:
            try:
                await upd.get_bot().send_message(
                    user_id,
                    f"📩 *Повідомлення від оцінювача*\n"
                    f"Замовлення: *{req_type}* (#{req_id})\n\n"
                    f"{text}\n\n📞 {PHONE2}",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        *([[InlineKeyboardButton("💬 Відповісти", url=MANAGER_TG_URL)]]
                          if MANAGER_TG_URL else [])
                    ]))
                await msg.reply_text(f"✅ Повідомлення надіслано клієнту (заявка #{req_id})",
                                     reply_markup=InlineKeyboardMarkup([[
                                         InlineKeyboardButton("← До заявки",
                                                              callback_data=f"adm|view|{req_id}")
                                     ]]))
            except Exception as e:
                await msg.reply_text(f"⚠️ Не вдалося надіслати клієнту: {e}")
        return ADMIN

    # Якщо немає активного введення — показуємо підказку
    await msg.reply_text(
        "🔐 Ви в режимі адмін-панелі.\n"
        "Натисніть кнопку в панелі або введіть /admin щоб повернутися.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🏠 Адмін-меню", callback_data="adm|home")
        ]]))
    return ADMIN


async def _admin_bans(q, ctx) -> int:
    if not _banned_users:
        await q.edit_message_text(
            "🛡 *Бан-список порожній.*\n\nЗаблокованих користувачів немає.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Назад", callback_data="adm|home")
            ]]))
        return ADMIN

    kb = [[InlineKeyboardButton(f"✅ Розблокувати {uid}", callback_data=f"adm|unban|{uid}")]
          for uid in list(_banned_users)[:10]]
    kb.append([InlineKeyboardButton("← Назад", callback_data="adm|home")])
    await q.edit_message_text(
        f"🚫 *Заблоковано: {len(_banned_users)} користувачів*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb))
    return ADMIN


# ══════════════════════════════════════════════════════════
#  ЗАХИСТ: перехоплення підозрілих повідомлень
# ══════════════════════════════════════════════════════════

async def security_middleware(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int | None:
    u = upd.effective_user
    if not u:
        return None
    if _is_banned(u.id):
        if upd.message:
            await upd.message.reply_text("⛔ Ваш акаунт заблоковано.")
        return MENU
    if _rate_check(u.id) and upd.message:
        if u.id not in _flood_warned:
            _flood_warned.add(u.id)
            await upd.message.reply_text(
                "⚠️ Ви надсилаєте повідомлення надто швидко. "
                "Зачекайте хвилину та спробуйте знову.")
            await _log_security(u.id, "RATE_LIMITED", f"@{u.username}")
    return None


# ══════════════════════════════════════════════════════════
#  ПОМИЛКИ
# ══════════════════════════════════════════════════════════

async def err_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Помилка: {ctx.error}", exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Виникла помилка. Спробуйте /start")
        except Exception:
            pass


# ══════════════════════════════════════════════════════════
#  POST_INIT
# ══════════════════════════════════════════════════════════

async def _post_init(app):
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Webhook видалено")
    except Exception as e:
        logger.warning(f"delete_webhook: {e}")
    if DATABASE_URL:
        try:
            await _get_pool()
            await init_db()
        except Exception as e:
            logger.error(f"DB init: {e}")


# ══════════════════════════════════════════════════════════
#  ЗБІРКА
# ══════════════════════════════════════════════════════════

def build():
    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            PHONE: [MessageHandler(
                (filters.CONTACT | filters.TEXT) & ~filters.COMMAND, handle_phone)],
            MENU: [CallbackQueryHandler(on_menu)],
            UPLOAD: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, handle_file),
                CallbackQueryHandler(on_menu),
            ],
            LOC: [
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
            AI_CHAT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_chat),
                MessageHandler(filters.PHOTO | filters.Document.ALL, handle_file),
                CallbackQueryHandler(on_menu),
            ],
            ADMIN: [
                CallbackQueryHandler(admin_callback, pattern=r"^(adm\||st\||2fa\|[0-9])"),
                CallbackQueryHandler(on_menu, pattern="^home$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text),
            ],
            ADMIN_2FA: [
                CallbackQueryHandler(handle_admin_2fa_callback, pattern=r"^2fa\|"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("admin",  admin_panel),
            CommandHandler("start",  cmd_start),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)
    # Хендлери поза ConversationHandler (завжди активні)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^(adm\||st\||2fa\|[0-9])"))
    app.add_error_handler(err_handler)
    return app


def main():
    _ensure_cyrillic_font_sync()
    _load_logo_sync()
    logger.info("🚀 ОЦІНКА24 Bot v6.0 | AI + 2FA + Security")
    logger.info(f"   Адмінів:  {len(ADMIN_IDS)} → {ADMIN_IDS}")
    logger.info(f"   Канал:    {CHANNEL_ID or '—'}")
    logger.info(f"   БД:       {'✅' if DATABASE_URL else '—'}")
    logger.info(f"   Gemini:   {'✅' if gemini_model else '—'}")
    build().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
