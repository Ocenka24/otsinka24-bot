#!/usr/bin/env python3
"""ОЦІНКА24 — Telegram Bot v4.3 | Google Maps + IP Geolocation + Watermark"""

import logging
import os
import uuid
from datetime import datetime
from io import BytesIO

import warnings
warnings.filterwarnings("ignore", category=UserWarning)

import requests
from PIL import Image, ImageDraw, ImageFont
from geopy.geocoders import Nominatim
from dotenv import load_dotenv

load_dotenv()

import googlemaps

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
WEBSITE             = "https://ocenka24.com.ua/"
EMAIL               = "info@ocenka24.com.ua"
PHONE1              = "0 800 502-977"
PHONE2              = "+38 (050) 3000-173"
LOGO                = "https://ocenka24.com.ua/img/ocenka24-logo.png"
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
IPGEO_API_KEY       = "836ef1c604514a50a4a315be1f114f34"   # Ваш ключ

# Ініціалізація API
geolocator_nominatim = Nominatim(user_agent="ocenka24_bot")
gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY) if GOOGLE_MAPS_API_KEY else None

# ── Об'єкти оцінки ────────────────────────────────────────
OBJECTS = {
    "car": ("🚗", "Оцінка транспортного засобу", [
        "📋 Технічний паспорт (свідоцтво про реєстрацію)",
        "🪪 Документ що посвідчує особу",
        "📸 Фото ТЗ ззовні з 4 кутів",
        "📸 Фото салону, пробігу та VIN-коду",
    ]),
    "flat": ("🏠", "Оцінка квартири", [
        "📜 Правовстановлюючий документ",
        "📋 Технічний паспорт",
        "🪪 Документ що посвідчує особу",
        "📸 Фото кімнат, кухні, служб",
    ]),
    "house": ("🏡", "Оцінка житлового будинку", [
        "📜 Правовстановлюючий документ на будинок",
        "📋 Технічний паспорт",
        "📜 Правовстановлюючий документ на землю",
        "🪪 Документ що посвідчує особу",
        "📸 Фото будинку ззовні з 4 кутів та всередині",
    ]),
    "land": ("🌿", "Оцінка земельної ділянки", [
        "📜 Правовстановлюючий документ на землю",
        "🪪 Документ що посвідчує особу",
        "📸 Фото ділянки (4-6 штук)",
    ]),
    "nonres": ("🏭", "Оцінка нежитлової будівлі/споруди", [
        "📜 Правовстановлюючий документ",
        "📋 Технічний паспорт",
        "🪪 Документ що посвідчує особу / юридичну особу",
        "📸 Фото будівлі ззовні з 4 кутів та всередині",
    ]),
}

# ── Стани ─────────────────────────────────────────────────
MENU, UPLOAD, LOC, VIDEOLOC = range(4)


# ══════════════════════════════════════════════════════════
#  КЛАВІАТУРИ
# ══════════════════════════════════════════════════════════

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚗 Оцінка транспортного засобу",       callback_data="obj_car")],
        [InlineKeyboardButton("🏠 Оцінка квартири",                   callback_data="obj_flat")],
        [InlineKeyboardButton("🏡 Оцінка житлового будинку",          callback_data="obj_house")],
        [InlineKeyboardButton("🌿 Оцінка земельної ділянки",          callback_data="obj_land")],
        [InlineKeyboardButton("🏭 Оцінка нежитлової будівлі/споруди", callback_data="obj_nonres")],
        [InlineKeyboardButton("📹 Онлайн відеоогляд об'єкта оцінки",  callback_data="video")],
        [InlineKeyboardButton("📍 Геолокація об'єкта оцінки",         callback_data="location")],
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
        one_time_keyboard=True, resize_keyboard=True
    )


def home_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Головне меню", callback_data="home")]])


# ══════════════════════════════════════════════════════════
#  WATERMARK + АДРЕСА (Google + IPGeo + Nominatim)
# ══════════════════════════════════════════════════════════

def add_watermark(image: Image.Image) -> Image.Image:
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 70)
    except:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 70)
        except:
            font = ImageFont.load_default()

    text = "ОЦІНКА24"
    bbox = draw.textbbox((0, 0), text, font=font)
    x = image.width - bbox[2] - 40
    y = image.height - bbox[3] - 40

    draw.text((x + 4, y + 4), text, font=font, fill=(0, 0, 0, 180))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 230))
    return image


def get_address_google(lat: float, lon: float) -> str:
    if not gmaps:
        return None
    try:
        result = gmaps.reverse_geocode((lat, lon), language="uk")
        if result:
            return result[0]['formatted_address']
    except Exception as e:
        logger.warning(f"Google Maps error: {e}")
    return None


def get_address_ipgeo(lat: float, lon: float) -> str:
    try:
        url = f"https://api.ipgeolocation.io/ipgeo?lat={lat}&lon={lon}&apiKey={IPGEO_API_KEY}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            city = data.get("city", "")
            country = data.get("country_name", "")
            return f"{city}, {country}".strip(", ")
    except Exception as e:
        logger.warning(f"IPGeolocation error: {e}")
    return None


async def process_photo_with_geotag(photo_file):
    photo_bytes = await photo_file.download_as_bytearray()
    image = Image.open(BytesIO(photo_bytes)).convert("RGB")

    lat = lon = address = None

    # Витягуємо GPS з EXIF
    try:
        exif = image._getexif()
        if exif and 34853 in exif:
            gps = exif[34853]
            def get_coord(val):
                return float(val[0]) + float(val[1])/60 + float(val[2])/3600
            lat = get_coord(gps.get(2))
            if gps.get(1) == 'S': lat = -lat
            lon = get_coord(gps.get(4))
            if gps.get(3) == 'W': lon = -lon
    except Exception as e:
        logger.warning(f"EXIF error: {e}")

    # Визначення адреси (пріоритет: Google → IPGeo → Nominatim)
    if lat and lon:
        address = get_address_google(lat, lon)
        if not address:
            address = get_address_ipgeo(lat, lon)
        if not address:
            try:
                loc = geolocator_nominatim.reverse((lat, lon), language="uk", timeout=10)
                address = loc.address if loc else None
            except Exception as e:
                logger.warning(f"Nominatim error: {e}")

    image = add_watermark(image)

    output = BytesIO()
    image.save(output, format="JPEG", quality=88, optimize=True)
    output.seek(0)

    return output, lat, lon, address or "Адресу не вдалося визначити"


# ══════════════════════════════════════════════════════════
#  КОМАНДИ ТА ОБРОБНИКИ
# ══════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    u = update.effective_user

    text = (
        f"👋 Вітаємо, *{u.first_name}*!\n\n"
        "🏢 *ОЦІНКА24* — професійна оцінка майна по всій Україні.\n\n"
        "Для проведення оцінки оберіть тип об'єкта і надішліть "
        "документи або проведіть онлайн відеоогляд.\n\n"
        f"☎️ {PHONE1} (безкоштовно)\n"
        f"📱 {PHONE2} (WhatsApp, Viber)\n"
        f"📧 {EMAIL}\n"
        f"🌐 {WEBSITE}\n\n"
        "👇 Оберіть дію:"
    )

    try:
        await update.message.reply_photo(
            photo=LOGO, caption=text,
            parse_mode="Markdown", reply_markup=main_kb()
        )
    except Exception:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_kb())

    return MENU


async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    d = q.data

    if d == "home":
        try: await q.edit_message_reply_markup(reply_markup=None)
        except: pass
        await context.bot.send_message(update.effective_chat.id, "🏠 Головне меню:", reply_markup=main_kb())
        return MENU

    if d == "about":
        await q.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(update.effective_chat.id,
            "🏢 *ОЦІНКА24*\n\n"
            "✅ Сертифіковані оцінювачі (ЗУ «Про оцінку майна»)\n"
            "✅ Досвід роботи понад 10 років\n"
            "✅ Оцінка по всій Україні\n"
            "✅ Звіти для банків, нотаріусів, судів",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📞 Контакти", callback_data="contact")],
                [InlineKeyboardButton("🏠 Головне меню", callback_data="home")],
            ]))
        return MENU

    if d == "contact":
        await q.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(update.effective_chat.id,
            f"📞 *Контакти ОЦІНКА24*\n\n"
            f"☎️ {PHONE1}\n"
            f"📱 {PHONE2}\n"
            f"📧 `{EMAIL}`\n"
            f"🌐 {WEBSITE}\n\n"
            "🕐 *Графік роботи:*\n"
            "Пн–Пт: 09:00–18:00\n"
            "Сб: 09:00–14:00 (за записом)\n"
            "Нд: вихідний",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("ℹ️ Про компанію", callback_data="about")],
                [InlineKeyboardButton("🏠 Головне меню", callback_data="home")],
            ]))
        return MENU

    if d == "location":
        await q.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(update.effective_chat.id,
            "📍 *Геолокація об'єкта оцінки*\n\n"
            "Перебуваючи біля об'єкта, натисніть кнопку нижче\nабо введіть адресу текстом.",
            parse_mode="Markdown", reply_markup=home_kb())
        await context.bot.send_message(update.effective_chat.id,
            "👇 Надішліть геолокацію:", reply_markup=gps_kb())
        return LOC

    if d == "video":
        await q.answer("Функція відеоогляду в розробці", show_alert=True)
        return MENU

    if d == "done":
        return await finish_upload(update, context)

    key = d.replace("obj_", "")
    if key in OBJECTS:
        return await show_object(update, context, key)

    return MENU


async def show_object(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> int:
    icon, name, docs = OBJECTS[key]
    context.user_data["obj_key"] = key
    context.user_data["obj_name"] = f"{icon} {name}"
    context.user_data["files"] = []

    doc_list = "\n".join(f"  {i+1}. {d}" for i, d in enumerate(docs))
    text = (
        f"{icon} *{name}*\n\n"
        f"📋 *Необхідні документи:*\n{doc_list}\n\n"
        "Надсилайте фото та документи по одному.\n"
        "Після завершення натисніть «✅ Завершити надсилання»."
    )

    try:
        await update.callback_query.edit_message_reply_markup(reply_markup=None)
    except:
        pass

    await context.bot.send_message(update.effective_chat.id, text,
                                   parse_mode="Markdown", reply_markup=upload_kb())
    return UPLOAD


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    u = msg.from_user
    obj_name = context.user_data.get("obj_name", "Документ")
    files = context.user_data.setdefault("files", [])

    if msg.photo:
        photo_file = await msg.photo[-1].get_file()
        processed, lat, lon, address = await process_photo_with_geotag(photo_file)

        caption = f"{obj_name}\n👤 {u.full_name} | 🆔 `{u.id}`"
        if lat and lon:
            caption += f"\n📍 `{lat:.6f}, {lon:.6f}`"
        if address:
            caption += f"\n📬 {address}"

        await msg.reply_photo(processed, caption="✅ Фото оброблено з водяним знаком та адресою")

        for chat_id in set(ADMIN_IDS + ([CHANNEL_ID] if CHANNEL_ID else [])):
            if chat_id:
                await context.bot.send_photo(chat_id, processed, caption=caption, parse_mode="Markdown")

        files.append(msg.photo[-1].file_id)

    elif msg.document:
        caption = f"{obj_name}\n👤 {u.full_name} | 🆔 `{u.id}`"
        for chat_id in set(ADMIN_IDS + ([CHANNEL_ID] if CHANNEL_ID else [])):
            if chat_id:
                await context.bot.send_document(chat_id, msg.document.file_id, caption=caption, parse_mode="Markdown")
        files.append(msg.document.file_id)

    await msg.reply_text(
        f"✅ Файл прийнято! Всього: *{len(files)}*",
        parse_mode="Markdown", reply_markup=upload_kb()
    )
    return UPLOAD


async def finish_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    u = update.effective_user
    name = context.user_data.get("obj_name", "—")
    files = context.user_data.get("files", [])

    if not files:
        await q.answer("⚠️ Надішліть хоча б один файл!", show_alert=True)
        return UPLOAD

    summary = (
        f"📋 *ДОКУМЕНТИ ОТРИМАНО*\n"
        f"{'─'*30}\n"
        f"👤 *{u.full_name}*\n"
        f"🆔 `{u.id}` | @{u.username or '—'}\n"
        f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        f"{name}\n"
        f"Файлів: *{len(files)}*\n\n"
        f"[Написати клієнту](tg://user?id={u.id})"
    )

    for chat_id in set(ADMIN_IDS + ([CHANNEL_ID] if CHANNEL_ID else [])):
        if chat_id:
            await context.bot.send_message(chat_id, summary, parse_mode="Markdown")

    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except:
        pass

    await context.bot.send_message(update.effective_chat.id,
        "✅ *Документи успішно надіслано!*\n\nОцінювач зв'яжеться з вами найближчим часом.",
        parse_mode="Markdown", reply_markup=main_kb())
    return MENU


# ══════════════════════════════════════════════════════════
#  ЗАПУСК БОТА
# ══════════════════════════════════════════════════════════

def main():
    logger.info("🚀 ОЦІНКА24 Bot v4.3 запущено")
    logger.info(f"Google Maps API: {'✅ Підключено' if GOOGLE_MAPS_API_KEY else '❌ Не вказано'}")
    logger.info("IP Geolocation API: ✅ Активний")

    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            MENU: [CallbackQueryHandler(on_menu)],
            UPLOAD: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, handle_file),
                CallbackQueryHandler(on_menu),
            ],
            LOC: [
                MessageHandler((filters.LOCATION | filters.TEXT) & ~filters.COMMAND, lambda u, c: MENU),
                CallbackQueryHandler(on_menu, pattern="^home$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: MENU)],
        allow_reentry=True,
    )

    app.add_handler(conv_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()