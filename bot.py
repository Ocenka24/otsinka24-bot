#!/usr/bin/env python3
"""
ОЦІНКА24 — Telegram Bot v3.0
"""
import logging
import os
import sys
import json
import uuid
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

import warnings
warnings.filterwarnings("ignore", message=".*CallbackQueryHandler.*per_message.*", category=UserWarning)

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ConversationHandler, ContextTypes, MessageHandler, filters,
)

# ── Logging ───────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Конфігурація ──────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    logger.critical("❌ BOT_TOKEN не знайдено!")
    sys.exit(1)

_admin_raw = os.getenv("ADMIN_IDS", "")
if not _admin_raw:
    logger.critical("❌ ADMIN_IDS не знайдено!")
    sys.exit(1)
ADMIN_IDS  = [int(x.strip()) for x in _admin_raw.split(",") if x.strip().isdigit()]
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

WEBSITE    = "https://ocenka24.com.ua/"
INFO_EMAIL = "info@ocenka24.com.ua"
HOTLINE    = "0 800 502-977"
MOBILE     = "+38 (050) 3000-173"
LOGO_URL   = "https://ocenka24.com.ua/img/ocenka24-logo.png"

# ── Типи об'єктів оцінки ──────────────────────────────────
OBJECT_TYPES = {
    "obj_car": {
        "icon": "🚗",
        "name": "Транспортний засіб",
        "docs": [
            "📋 Технічний паспорт (свідоцтво про реєстрацію)",
            "🪪 Документ що посвідчує особу",
            "📸 Фото ТЗ ззовні з 4 кутів",
            "📸 Фото салону, пробігу та VIN-коду",
        ],
    },
    "obj_flat": {
        "icon": "🏠",
        "name": "Квартира",
        "docs": [
            "📜 Правовстановлюючий документ",
            "📋 Технічний паспорт",
            "🪪 Документ що посвідчує особу",
            "📸 Фото кімнат, кухні, служб",
        ],
    },
    "obj_house": {
        "icon": "🏡",
        "name": "Житловий будинок",
        "docs": [
            "📜 Правовстановлюючий документ на будинок",
            "📋 Технічний паспорт",
            "📜 Правовстановлюючий документ на землю",
            "🪪 Документ що посвідчує особу",
            "📸 Фото будинку ззовні з 4 кутів та всередині",
        ],
    },
    "obj_land": {
        "icon": "🌿",
        "name": "Земельна ділянка",
        "docs": [
            "📜 Правовстановлюючий документ на землю",
            "🪪 Документ що посвідчує особу",
            "📸 Фото ділянки (4-6 штук)",
        ],
    },
    "obj_nonresidential": {
        "icon": "🏭",
        "name": "Нежитлові будівлі та споруди",
        "docs": [
            "📜 Правовстановлюючий документ",
            "📋 Технічний паспорт",
            "🪪 Документ що посвідчує особу / юридичну особу",
            "📸 Фото будівлі ззовні з 4 кутів та всередині",
        ],
    },
}

# ── Стани ─────────────────────────────────────────────────
(
    MAIN_MENU,
    DOC_OBJECT_TYPE,
    DOC_UPLOAD,
    LOC_RECEIVE,
    VIDEO_LOC,
) = range(5)


# ══════════════════════════════════════════════════════════
#  КЛАВІАТУРИ
# ══════════════════════════════════════════════════════════

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚗 Транспортний засіб",         callback_data="obj_car")],
        [InlineKeyboardButton("🏠 Квартира",                   callback_data="obj_flat")],
        [InlineKeyboardButton("🏡 Житловий будинок",           callback_data="obj_house")],
        [InlineKeyboardButton("🌿 Земельна ділянка",           callback_data="obj_land")],
        [InlineKeyboardButton("🏭 Нежитлові будівлі/споруди",  callback_data="obj_nonresidential")],
        [InlineKeyboardButton("📹 Онлайн відеоогляд",          callback_data="menu_video")],
        [InlineKeyboardButton("📍 Геолокація об'єкта",         callback_data="menu_location")],
        [
            InlineKeyboardButton("ℹ️ Про компанію", callback_data="menu_about"),
            InlineKeyboardButton("📞 Контакти",     callback_data="menu_contact"),
        ],
    ])


def kb_object_types() -> InlineKeyboardMarkup:
    rows = []
    for key, obj in OBJECT_TYPES.items():
        rows.append([InlineKeyboardButton(
            f"{obj['icon']} {obj['name']}",
            callback_data=key
        )])
    rows.append([InlineKeyboardButton("🏠 Головне меню", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def kb_doc_upload(obj_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Завершити надсилання", callback_data="doc_done")],
        [InlineKeyboardButton("🔄 Інший тип об'єкта",   callback_data="back_main")],
        [InlineKeyboardButton("🏠 Головне меню",         callback_data="back_main")],
    ])


def kb_back_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏠 Головне меню", callback_data="back_main")
    ]])


def kb_gps() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Поділитися геолокацією", request_location=True)]],
        one_time_keyboard=True, resize_keyboard=True,
    )


def kb_gps_videocall() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Надіслати геолокацію об'єкта", request_location=True)]],
        one_time_keyboard=True, resize_keyboard=True,
    )


# ══════════════════════════════════════════════════════════
#  ДОПОМІЖНІ ФУНКЦІЇ
# ══════════════════════════════════════════════════════════

def client_tag(update: Update, phone: str = "") -> str:
    u = update.effective_user
    ts = datetime.now().strftime("%d.%m.%Y %H:%M")
    phone_line = f"\n📞 `{phone}`" if phone else ""
    link = f"[✉️ Написати клієнту](tg://user?id={u.id})"
    return (
        f"👤 *{u.full_name}*  |  🆔 `{u.id}`\n"
        f"📱 @{u.username or '—'}{phone_line}\n"
        f"🕐 {ts}\n{link}"
    )


async def send_all(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if CHANNEL_ID:
        try:
            await context.bot.send_message(CHANNEL_ID, text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Канал: {e}")
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(aid, text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Адмін {aid}: {e}")


async def send_photo_all(context: ContextTypes.DEFAULT_TYPE, file_id: str, caption: str) -> None:
    if CHANNEL_ID:
        try:
            await context.bot.send_photo(CHANNEL_ID, file_id, caption=caption, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Канал фото: {e}")
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_photo(aid, file_id, caption=caption, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Адмін {aid} фото: {e}")


async def send_doc_all(context: ContextTypes.DEFAULT_TYPE, file_id: str, caption: str) -> None:
    if CHANNEL_ID:
        try:
            await context.bot.send_document(CHANNEL_ID, file_id, caption=caption, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Канал документ: {e}")
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_document(aid, file_id, caption=caption, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Адмін {aid} документ: {e}")


async def send_location_all(context: ContextTypes.DEFAULT_TYPE, lat: float, lon: float) -> None:
    if CHANNEL_ID:
        try:
            await context.bot.send_location(CHANNEL_ID, lat, lon)
        except Exception as e:
            logger.warning(f"Канал локація: {e}")
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_location(aid, lat, lon)
        except Exception as e:
            logger.warning(f"Адмін {aid} локація: {e}")


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> int:
    text = (
        "🏢 *ОЦІНКА24* — професійна оцінка майна\n\n"
        "Оберіть потрібну дію:"
    )
    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=kb_main(), parse_mode="Markdown"
            )
        except Exception:
            await context.bot.send_message(
                update.effective_chat.id, text,
                reply_markup=kb_main(), parse_mode="Markdown"
            )
    else:
        await update.effective_message.reply_text(
            text, reply_markup=kb_main(), parse_mode="Markdown"
        )
    return MAIN_MENU


# ══════════════════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    u = update.effective_user
    caption = (
        f"👋 Вітаємо, *{u.first_name}*!\n\n"
        "🏢 *ОЦІНКА24* — професійна оцінка майна по всій Україні.\n\n"
        "Оберіть тип об'єкта і надішліть документи,\n"
        "або проведіть онлайн відеоогляд.\n\n"
        f"☎️ {HOTLINE}  |  📱 {MOBILE}\n"
        f"📧 {INFO_EMAIL}\n"
        f"🌐 {WEBSITE}\n\n"
        "👇 Оберіть дію:"
    )
    try:
        await update.message.reply_photo(
            photo=LOGO_URL, caption=caption,
            parse_mode="Markdown", reply_markup=kb_main(),
        )
    except Exception:
        await update.message.reply_text(
            caption, parse_mode="Markdown", reply_markup=kb_main()
        )
    return MAIN_MENU


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Скасовано.", reply_markup=ReplyKeyboardRemove())
    return await show_main_menu(update, context)


# ══════════════════════════════════════════════════════════
#  ГОЛОВНЕ МЕНЮ
# ══════════════════════════════════════════════════════════

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "back_main":
        return await show_main_menu(update, context, edit=True)

    if data == "menu_video":
        return await start_videocall(update, context)

    if data == "menu_location":
        return await start_location(update, context)

    if data == "menu_about":
        await query.edit_message_text(
            "🏢 *ОЦІНКА24*\n\n"
            "✅ Сертифіковані оцінювачі (ЗУ «Про оцінку майна»)\n"
            "✅ Досвід роботи понад 10 років\n"
            "✅ Оцінка нерухомості, авто, бізнесу, збитків\n"
            "✅ Звіти для банків, нотаріусів, судів\n"
            "✅ Відповідність МСО та НСО України\n\n"
            f"📧 `{INFO_EMAIL}`\n"
            f"🌐 {WEBSITE}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📞 Контакти", callback_data="menu_contact")],
                [InlineKeyboardButton("🏠 Головне меню", callback_data="back_main")],
            ]),
            parse_mode="Markdown",
        )
        return MAIN_MENU

    if data == "menu_contact":
        await query.edit_message_text(
            "📞 *Контакти ОЦІНКА24*\n\n"
            f"☎️ Гаряча лінія: `{HOTLINE}`\n"
            f"📱 Мобільний: `{MOBILE}`\n"
            f"📧 `{INFO_EMAIL}`\n"
            f"🌐 {WEBSITE}\n\n"
            "🕐 *Графік роботи:*\n"
            "Пн–Пт: 09:00–18:00\n"
            "Сб: 09:00–14:00 (за записом)\n"
            "Нд: вихідний",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("ℹ️ Про компанію", callback_data="menu_about")],
                [InlineKeyboardButton("🏠 Головне меню", callback_data="back_main")],
            ]),
            parse_mode="Markdown",
        )
        return MAIN_MENU

    # Вибір типу об'єкта
    if data in OBJECT_TYPES:
        return await select_object_type(update, context, data)

    if data == "doc_done":
        return await finish_docs(update, context)

    return MAIN_MENU


# ══════════════════════════════════════════════════════════
#  БЛОК 1: ДОКУМЕНТИ
# ══════════════════════════════════════════════════════════

async def start_docs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("uploaded", [])
    text = (
        "📄 *Документи для оцінки*\n\n"
        "Оберіть *тип об'єкта* — бот покаже перелік необхідних документів:"
    )
    try:
        await update.callback_query.edit_message_text(
            text, reply_markup=kb_object_types(), parse_mode="Markdown"
        )
    except Exception:
        await context.bot.send_message(
            update.effective_chat.id, text,
            reply_markup=kb_object_types(), parse_mode="Markdown"
        )
    return DOC_OBJECT_TYPE


async def select_object_type(update: Update, context: ContextTypes.DEFAULT_TYPE, obj_key: str) -> int:
    obj = OBJECT_TYPES[obj_key]
    context.user_data["obj_key"]  = obj_key
    context.user_data["uploaded"] = []

    docs_list = "\n".join(f"  {i+1}. {d}" for i, d in enumerate(obj["docs"]))

    text = (
        f"{obj['icon']} *{obj['name']}*\n\n"
        f"📋 *Необхідні документи:*\n{docs_list}\n\n"
        "Надсилайте документи по одному (фото або PDF).\n"
        "Коли надішлете всі — натисніть «✅ Завершити»."
    )
    try:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=kb_doc_upload(obj_key),
            parse_mode="Markdown"
        )
    except Exception:
        await context.bot.send_message(
            update.effective_chat.id, text,
            reply_markup=kb_doc_upload(obj_key),
            parse_mode="Markdown"
        )
    return DOC_UPLOAD


async def handle_doc_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    obj_key = context.user_data.get("obj_key", "")
    obj     = OBJECT_TYPES.get(obj_key, {})
    uploaded = context.user_data.setdefault("uploaded", [])

    if msg.photo:
        file_id, is_photo = msg.photo[-1].file_id, True
    elif msg.document:
        file_id, is_photo = msg.document.file_id, False
    else:
        await msg.reply_text("⚠️ Надішліть фото або PDF документа.")
        return DOC_UPLOAD

    uploaded.append(file_id)
    u = msg.from_user

    caption = (
        f"{obj.get('icon','')} {obj.get('name','')}\n"
        f"👤 {u.full_name} | 🆔 `{u.id}`\n"
        f"📱 @{u.username or '—'}"
    )

    if is_photo:
        await send_photo_all(context, file_id, caption)
    else:
        await send_doc_all(context, file_id, caption)

    await msg.reply_text(
        f"✅ Файл прийнято! Всього: {len(uploaded)} шт.\n\n"
        "Надсилайте ще файли або натисніть «✅ Завершити надсилання».",
        reply_markup=kb_doc_upload(obj_key),
    )
    return DOC_UPLOAD


async def finish_docs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    obj_key  = context.user_data.get("obj_key", "")
    obj      = OBJECT_TYPES.get(obj_key, {})
    uploaded = context.user_data.get("uploaded", [])
    u = update.effective_user
    ts = datetime.now().strftime("%d.%m.%Y %H:%M")

    if not uploaded:
        await query.edit_message_text(
            "⚠️ Ви ще не надіслали жодного файлу.\nНадішліть хоча б один файл.",
            reply_markup=kb_doc_upload(obj_key),
        )
        return DOC_UPLOAD

    docs_list = "\n".join(f"  {i+1}. {d}" for i, d in enumerate(obj.get("docs", [])))
    summary = (
        f"📋 *ДОКУМЕНТИ ОТРИМАНО*\n"
        f"{'─' * 28}\n"
        f"👤 *{u.full_name}*\n"
        f"🆔 `{u.id}` | @{u.username or '—'}\n"
        f"🕐 {ts}\n\n"
        f"{obj.get('icon','')} *{obj.get('name','')}*\n"
        f"Надіслано: *{len(uploaded)}* файлів\n\n"
        f"📋 Перелік документів:\n{docs_list}\n\n"
        f"[✉️ Написати клієнту](tg://user?id={u.id})"
    )
    await send_all(context, summary)

    await query.edit_message_text(
        f"✅ *Документи успішно надіслано!*\n\n"
        f"📦 Файлів: *{len(uploaded)}*\n"
        f"{obj.get('icon','')} Тип: *{obj.get('name','')}*\n\n"
        "Оцінювач перевірить їх найближчим часом і зв'яжеться з вами.",
        reply_markup=kb_main(),
        parse_mode="Markdown",
    )
    return MAIN_MENU


async def handle_doc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "back_main":
        return await show_main_menu(update, context, edit=True)
    if data == "doc_done":
        return await finish_docs(update, context)
    if data in OBJECT_TYPES:
        return await select_object_type(update, context, data)
    return DOC_UPLOAD


# ══════════════════════════════════════════════════════════
#  БЛОК 2: ГЕОЛОКАЦІЯ
# ══════════════════════════════════════════════════════════

async def start_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (
        "📍 *Геолокація об'єкта оцінки*\n\n"
        "Перебуваючи біля об'єкта натисніть кнопку нижче,\n"
        "або введіть *адресу текстом*.\n\n"
        "_Координати будуть зафіксовані у справі._"
    )
    try:
        await update.callback_query.edit_message_text(
            text, reply_markup=kb_back_main(), parse_mode="Markdown"
        )
    except Exception:
        await context.bot.send_message(
            update.effective_chat.id, text,
            reply_markup=kb_back_main(), parse_mode="Markdown"
        )
    await context.bot.send_message(
        update.effective_chat.id,
        "👇 Натисніть кнопку або введіть адресу:",
        reply_markup=kb_gps(),
    )
    return LOC_RECEIVE


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message

    if msg.location:
        lat, lon = msg.location.latitude, msg.location.longitude
        maps_url = f"https://maps.google.com/?q={lat},{lon}"
        tag = client_tag(update)
        await send_all(
            context,
            f"📍 *ГЕОЛОКАЦІЯ ОБ'ЄКТА*\n\n{tag}\n\n"
            f"📌 `{lat:.6f}, {lon:.6f}`\n"
            f"🗺 [Google Maps]({maps_url})"
        )
        await send_location_all(context, lat, lon)
        await msg.reply_text(
            f"✅ *Геолокацію зафіксовано!*\n\n"
            f"📌 `{lat:.5f}, {lon:.5f}`\n"
            f"🗺 [Google Maps]({maps_url})",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )

    elif msg.text and not msg.text.startswith("/"):
        address = msg.text.strip()
        tag = client_tag(update)
        await send_all(context, f"📍 *АДРЕСА ОБ'ЄКТА*\n\n{tag}\n\n📬 {address}")
        await msg.reply_text(
            f"✅ *Адресу зафіксовано!*\n\n📬 {address}",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await msg.reply_text("⚠️ Поділіться геолокацією або введіть адресу.")
        return LOC_RECEIVE

    await context.bot.send_message(
        msg.chat.id,
        "Дякуємо! Оцінювач отримав місцезнаходження.",
        reply_markup=kb_main(),
    )
    return MAIN_MENU


async def handle_loc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "back_main":
        return await show_main_menu(update, context, edit=True)
    return LOC_RECEIVE


# ══════════════════════════════════════════════════════════
#  БЛОК 3: ВІДЕОДЗВІНОК
# ══════════════════════════════════════════════════════════

async def start_videocall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    u     = update.effective_user
    ts    = datetime.now().strftime("%d.%m.%Y %H:%M")
    room  = f"Otsinka24-{uuid.uuid4().hex[:12].upper()}"
    jitsi = f"https://meet.jit.si/{room}"

    context.user_data["jitsi_room"] = room
    context.user_data["jitsi_url"]  = jitsi

    # Сповіщення адміну одразу
    admin_msg = (
        f"📹 *ВІДЕООГЛЯД — ОНЛАЙН*\n"
        f"{'─' * 28}\n"
        f"👤 *{u.full_name}*\n"
        f"🆔 `{u.id}` | @{u.username or chr(8212)}\n"
        f"🕐 {ts}\n\n"
        f"📍 GPS — клієнт надсилає зараз...\n\n"
        f"🔗 Кімната: `{room}`\n"
        f"[📹 Приєднатися до відеодзвінка]({jitsi})\n\n"
        f"⚡️ Клієнт підключається!\n"
        f"[✉️ Написати клієнту](tg://user?id={u.id})"
    )
    await send_all(context, admin_msg)

    # Клієнту — кнопка входу
    try:
        await update.callback_query.edit_message_text(
            "📹 *Відеоогляд розпочато!*\n\n"
            "Оцінювач отримав сповіщення і незабаром підключиться.",
            parse_mode="Markdown",
        )
    except Exception:
        pass

    # Просимо GPS
    await context.bot.send_message(
        update.effective_chat.id,
        "📍 *Поділіться геолокацією об'єкта*\n\n"
        "Перебуваючи біля об'єкта натисніть кнопку нижче.\n"
        "_GPS прив'яжеться до вашого відеоогляду._",
        parse_mode="Markdown",
        reply_markup=kb_gps_videocall(),
    )

    # Кнопка входу у кімнату
    await context.bot.send_message(
        update.effective_chat.id,
        "👇 Натисніть щоб увійти у відеодзвінок з оцінювачем:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📹 Увійти у відеодзвінок", url=jitsi)
        ]])
    )
    return VIDEO_LOC


async def handle_videocall_gps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg   = update.message
    u     = msg.from_user
    room  = context.user_data.get("jitsi_room", "—")
    jitsi = context.user_data.get("jitsi_url", "")

    if msg.location:
        lat, lon = msg.location.latitude, msg.location.longitude
        maps_url = f"https://maps.google.com/?q={lat},{lon}"

        await send_all(
            context,
            f"📍 *GPS ОБ'ЄКТА ОТРИМАНО*\n"
            f"{'─' * 28}\n"
            f"👤 {u.full_name} | 🆔 `{u.id}`\n"
            f"🔗 Кімната: `{room}`\n\n"
            f"📌 `{lat:.6f}, {lon:.6f}`\n"
            f"🗺 [Google Maps]({maps_url})"
        )
        await send_location_all(context, lat, lon)

        await msg.reply_text(
            f"✅ *GPS зафіксовано!*\n\n"
            f"📌 `{lat:.5f}, {lon:.5f}`\n\n"
            "Оцінювач отримав координати об'єкта.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        if jitsi:
            await context.bot.send_message(
                msg.chat.id,
                "👇 Ви можете увійти у відеодзвінок:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📹 Увійти у відеодзвінок", url=jitsi)
                ]])
            )

    elif msg.text and not msg.text.startswith("/"):
        address = msg.text.strip()
        await send_all(
            context,
            f"📍 *АДРЕСА ОБ'ЄКТА*\n"
            f"👤 {u.full_name} | Кімната: `{room}`\n"
            f"📬 {address}"
        )
        await msg.reply_text(
            f"✅ *Адресу зафіксовано!*\n📬 {address}",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await msg.reply_text("⚠️ Поділіться геолокацією кнопкою нижче.")
        return VIDEO_LOC

    return MAIN_MENU


async def handle_video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "back_main":
        return await show_main_menu(update, context, edit=True)
    return VIDEO_LOC


# ══════════════════════════════════════════════════════════
#  ЗБІРКА
# ══════════════════════════════════════════════════════════

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Помилка: {context.error}", exc_info=context.error)


def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(handle_main_menu),
            ],
            DOC_OBJECT_TYPE: [
                CallbackQueryHandler(handle_main_menu),
            ],
            DOC_UPLOAD: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, handle_doc_upload),
                CallbackQueryHandler(handle_doc_callback),
            ],
            LOC_RECEIVE: [
                MessageHandler(
                    (filters.LOCATION | filters.TEXT) & ~filters.COMMAND,
                    handle_location,
                ),
                CallbackQueryHandler(handle_loc_callback, pattern="^back_main$"),
            ],
            VIDEO_LOC: [
                MessageHandler(
                    (filters.LOCATION | filters.TEXT) & ~filters.COMMAND,
                    handle_videocall_gps,
                ),
                CallbackQueryHandler(handle_video_callback, pattern="^back_main$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start",  cmd_start),
        ],
        allow_reentry=True,
        name="otsinka24_v3",
    )

    app.add_handler(conv)
    app.add_error_handler(error_handler)
    return app


def main() -> None:
    logger.info("🚀 Запуск бота ОЦІНКА24 v3.0...")
    logger.info(f"   Адмінів: {len(ADMIN_IDS)}")
    logger.info(f"   Канал:   {'так' if CHANNEL_ID else 'ні'}")
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
