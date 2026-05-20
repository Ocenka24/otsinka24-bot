#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════╗
║         TELEGRAM BOT — ОЦІНКА24                      ║
║         Версія: 2.0                                  ║
║         Мова: Python 3.10+                           ║
║         Бібліотека: python-telegram-bot v20+         ║
╚══════════════════════════════════════════════════════╝

Функції v2.0:
  • Логотип + контакти при /start
  • Ідентифікація клієнта (паспорт + селфі)
  • Надсилання документів (7 типів)
  • Геолокація об'єкта оцінки
  • Запис на відеоогляд (слоти)
  • Web App — відео з камери + GPS прямо в боті
  • Закритий канал + дублювання адмінам
  • Повна процедура (всі кроки послідовно)
"""

import logging
import os
import sys
import json
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import warnings
warnings.filterwarnings(
    "ignore",
    message=".*CallbackQueryHandler.*per_message.*",
    category=UserWarning,
)

# ─── Logging ──────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Конфігурація — токен і адміни ────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    logger.critical("❌ BOT_TOKEN не знайдено! Заповніть файл .env")
    sys.exit(1)

_admin_raw = os.getenv("ADMIN_IDS", "")
if not _admin_raw:
    logger.critical("❌ ADMIN_IDS не знайдено! Заповніть файл .env")
    sys.exit(1)
ADMIN_IDS = [int(x.strip()) for x in _admin_raw.split(",") if x.strip().isdigit()]

# ─── ID закритого каналу для документів ──────────────────
# Формат: -1001234567890  (з мінусом!)
# Як отримати: дивіться інструкцію нижче у README
# Поки канал не створено — залиште 0, бот буде надсилати тільки адмінам
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

# ─── Контактні дані ОЦІНКА24 ─────────────────────────────
WEBSITE     = "https://ocenka24.com.ua/"
INFO_EMAIL  = "info@ocenka24.com.ua"
HOTLINE     = "0 800 502-977"
MOBILE      = "+38 (050) 3000-173"
LOGO_URL    = "https://ocenka24.com.ua/img/ocenka24-logo.png"

# ─── Web App URL (GitHub Pages після деплою) ─────────────
# Після того як завантажите webapp/index.html на GitHub Pages,
# замініть це посилання на ваше: https://USERNAME.github.io/otsinka24-bot/
WEBAPP_URL  = os.getenv("WEBAPP_URL", "")

# ─── Стани розмови ────────────────────────────────────────
(
    MAIN_MENU,
    IDENT_PASSPORT,
    IDENT_SELFIE,
    DOC_CHOOSE,
    DOC_UPLOAD,
    LOC_RECEIVE,
    VIDEO_TIME,
) = range(7)

# ─── Типи документів ──────────────────────────────────────
DOCUMENT_TYPES = {
    "doc_title":     "📜 Правовстановлюючий документ",
    "doc_techpass":  "🗂 Технічний паспорт",
    "doc_extract":   "📋 Витяг з Держреєстру",
    "doc_plan":      "📐 План/схема об'єкта",
    "doc_cadastral": "🗺 Кадастровий план (для землі)",
    "doc_id":        "🪪 Документ, що посвідчує особу",
    "doc_other":     "📎 Інший документ",
}

# ─── Часові слоти відеоогляду ─────────────────────────────
VIDEO_SLOTS = [
    "09:00", "10:00", "11:00",
    "12:00", "13:00", "14:00",
    "15:00", "16:00", "17:00",
    "18:00",
]


# ══════════════════════════════════════════════════════════
#  КЛАВІАТУРИ
# ══════════════════════════════════════════════════════════

def kb_main_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🚀 ПОВНА ПРОЦЕДУРА (всі кроки)", callback_data="full_procedure")],
        [
            InlineKeyboardButton("🪪 Ідентифікація", callback_data="menu_ident"),
            InlineKeyboardButton("📄 Документи",      callback_data="menu_docs"),
        ],
        [
            InlineKeyboardButton("📍 Геолокація",     callback_data="menu_location"),
            InlineKeyboardButton("🎥 Відеоогляд",     callback_data="menu_video"),
        ],
    ]
    # Web App кнопка — показуємо тільки якщо URL налаштовано
    if WEBAPP_URL:
        rows.append([
            InlineKeyboardButton(
                "📱 Відео + GPS (Web App)",
                web_app=WebAppInfo(url=WEBAPP_URL),
            )
        ])
    rows.append([
        InlineKeyboardButton("ℹ️ Про компанію", callback_data="menu_about"),
        InlineKeyboardButton("📞 Контакти",     callback_data="menu_contact"),
    ])
    return InlineKeyboardMarkup(rows)


def kb_doc_types(uploaded: list) -> InlineKeyboardMarkup:
    rows = []
    for key, label in DOCUMENT_TYPES.items():
        mark = "✅ " if key in uploaded else ""
        rows.append([InlineKeyboardButton(f"{mark}{label}", callback_data=key)])
    rows.append([
        InlineKeyboardButton("✔️ Завершити надсилання", callback_data="doc_done"),
        InlineKeyboardButton("🏠 Меню",                 callback_data="back_main"),
    ])
    return InlineKeyboardMarkup(rows)


def kb_location() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Поділитися геолокацією", request_location=True)]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )


def kb_video_slots() -> InlineKeyboardMarkup:
    rows = []
    row = []
    for slot in VIDEO_SLOTS:
        row.append(InlineKeyboardButton(slot, callback_data=f"slot_{slot}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🏠 Головне меню", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def kb_back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏠 Головне меню", callback_data="back_main")
    ]])


def kb_skip_or_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⏭ Пропустити", callback_data="step_skip"),
        InlineKeyboardButton("🏠 Меню",       callback_data="back_main"),
    ]])


# ══════════════════════════════════════════════════════════
#  ДОПОМІЖНІ ФУНКЦІЇ — клієнт, канал, адміни
# ══════════════════════════════════════════════════════════

def client_tag(update: Update) -> str:
    u = update.effective_user
    ts = datetime.now().strftime("%d.%m.%Y %H:%M")
    return (
        f"👤 {u.full_name}  |  ID: `{u.id}`\n"
        f"📱 @{u.username or '—'}\n"
        f"🕐 {ts}"
    )


async def _send_text_all(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Надсилає текст і в канал, і всім адмінам."""
    # → Канал
    if CHANNEL_ID:
        try:
            await context.bot.send_message(CHANNEL_ID, text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Канал: помилка надсилання тексту: {e}")
    # → Адміни
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(aid, text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Адмін {aid}: помилка тексту: {e}")


async def _send_photo_all(context: ContextTypes.DEFAULT_TYPE, file_id: str, caption: str) -> None:
    """Надсилає фото і в канал, і всім адмінам."""
    if CHANNEL_ID:
        try:
            await context.bot.send_photo(CHANNEL_ID, file_id, caption=caption, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Канал: помилка фото: {e}")
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_photo(aid, file_id, caption=caption, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Адмін {aid}: помилка фото: {e}")


async def _send_document_all(context: ContextTypes.DEFAULT_TYPE, file_id: str, caption: str) -> None:
    """Надсилає документ і в канал, і всім адмінам."""
    if CHANNEL_ID:
        try:
            await context.bot.send_document(CHANNEL_ID, file_id, caption=caption, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Канал: помилка документа: {e}")
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_document(aid, file_id, caption=caption, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Адмін {aid}: помилка документа: {e}")


async def _send_location_all(context: ContextTypes.DEFAULT_TYPE, lat: float, lon: float) -> None:
    """Надсилає геолокацію і в канал, і всім адмінам."""
    if CHANNEL_ID:
        try:
            await context.bot.send_location(CHANNEL_ID, lat, lon)
        except Exception as e:
            logger.warning(f"Канал: помилка локації: {e}")
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_location(aid, lat, lon)
        except Exception as e:
            logger.warning(f"Адмін {aid}: помилка локації: {e}")


async def _send_video_all(context: ContextTypes.DEFAULT_TYPE, file_id: str, caption: str) -> None:
    """Надсилає відео і в канал, і всім адмінам."""
    if CHANNEL_ID:
        try:
            await context.bot.send_video(CHANNEL_ID, file_id, caption=caption, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Канал: помилка відео: {e}")
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_video(aid, file_id, caption=caption, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Адмін {aid}: помилка відео: {e}")


async def show_main_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    edit: bool = False,
) -> int:
    text = (
        "🏢 *ОЦІНКА24* — Ваш надійний партнер у оцінці майна\n\n"
        "Оберіть потрібну дію або запустіть *Повну процедуру*:"
    )
    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=kb_main_menu(), parse_mode="Markdown"
            )
        except Exception:
            await context.bot.send_message(
                update.effective_chat.id,
                text, reply_markup=kb_main_menu(), parse_mode="Markdown",
            )
    else:
        await update.effective_message.reply_text(
            text, reply_markup=kb_main_menu(), parse_mode="Markdown"
        )
    return MAIN_MENU


# ══════════════════════════════════════════════════════════
#  /start — логотип + контакти
# ══════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    u = update.effective_user

    caption = (
        f"👋 Вітаємо, *{u.first_name}*!\n\n"
        "🏢 *ОЦІНКА24* — професійна оцінка майна по всій Україні.\n\n"
        "Через цього бота ви можете:\n"
        "• 🪪 Пройти ідентифікацію\n"
        "• 📄 Надіслати необхідні документи\n"
        "• 📍 Вказати місцезнаходження об'єкта\n"
        "• 🎥 Записатись на відеоогляд\n\n"
        f"🌐 {WEBSITE}\n"
        f"📧 {INFO_EMAIL}\n"
        f"☎️ Гаряча лінія: {HOTLINE}\n"
        f"📱 Моб.: {MOBILE}\n\n"
        "👇 Оберіть дію:"
    )

    try:
        await update.message.reply_photo(
            photo=LOGO_URL,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=kb_main_menu(),
        )
    except Exception as e:
        logger.warning(f"Логотип не завантажився ({e}), fallback без фото")
        await update.message.reply_text(
            caption,
            parse_mode="Markdown",
            reply_markup=kb_main_menu(),
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

    if data == "full_procedure":
        context.user_data["flow"] = "full"
        context.user_data["full_steps_done"] = []
        return await _start_ident(update, context)
    if data == "menu_ident":
        context.user_data["flow"] = "single"
        return await _start_ident(update, context)
    if data == "menu_docs":
        context.user_data["flow"] = "single"
        return await _start_docs(update, context)
    if data == "menu_location":
        context.user_data["flow"] = "single"
        return await _start_location(update, context)
    if data == "menu_video":
        context.user_data["flow"] = "single"
        return await _start_video(update, context)
    if data == "back_main":
        return await show_main_menu(update, context, edit=True)

    if data == "menu_about":
        await query.edit_message_text(
            "🏢 *ОЦІНКА24*\n\n"
            "✅ Сертифіковані оцінювачі (ЗУ «Про оцінку майна»)\n"
            "✅ Досвід роботи понад 10 років\n"
            "✅ Оцінка нерухомості, авто, бізнесу, збитків\n"
            "✅ Звіти для банків, нотаріусів, судів\n"
            "✅ Відповідність МСО та НСО України\n\n"
            f"📧 {INFO_EMAIL}\n"
            f"🌐 {WEBSITE}",
            reply_markup=kb_back_menu(),
            parse_mode="Markdown",
        )
        return MAIN_MENU

    if data == "menu_contact":
        await query.edit_message_text(
            "📞 *Контакти ОЦІНКА24*\n\n"
            f"☎️ Гаряча лінія: `{HOTLINE}`\n"
            f"📱 Мобільний: `{MOBILE}`\n"
            f"📧 {INFO_EMAIL}\n"
            f"🌐 {WEBSITE}\n\n"
            "🕐 *Графік:*\n"
            "Пн–Пт: 09:00–18:00\n"
            "Сб: 09:00–14:00 (за записом)",
            reply_markup=kb_back_menu(),
            parse_mode="Markdown",
        )
        return MAIN_MENU

    return MAIN_MENU


# ══════════════════════════════════════════════════════════
#  WEB APP — обробник даних від index.html
# ══════════════════════════════════════════════════════════

async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отримує дані з Web App (відео-огляд і геолокація)."""
    msg = update.message
    if not msg.web_app_data:
        return

    try:
        data = json.loads(msg.web_app_data.data)
    except Exception:
        await msg.reply_text("⚠️ Помилка обробки даних Web App.")
        return

    tag = client_tag(update)
    event = data.get("event", "")

    # Геолокація з Web App
    if event == "location":
        lat = data.get("lat")
        lon = data.get("lon")
        acc = data.get("accuracy", "—")
        if lat and lon:
            maps_url = f"https://maps.google.com/?q={lat},{lon}"
            await _send_text_all(
                context,
                f"📍 *ГЕОЛОКАЦІЯ (Web App)*\n\n{tag}\n\n"
                f"🗺 [Google Maps]({maps_url})\n"
                f"Координати: `{lat:.6f}, {lon:.6f}`\n"
                f"Точність: {acc} м",
            )
            await _send_location_all(context, lat, lon)
            await msg.reply_text(
                f"✅ *Геолокацію надіслано!*\n\n"
                f"📌 `{lat:.5f}, {lon:.5f}`\n"
                f"🗺 [Google Maps]({maps_url})",
                parse_mode="Markdown",
                reply_markup=kb_back_menu(),
            )

    # Відео з Web App
    elif event == "video_note":
        await msg.reply_text(
            "✅ *Відео отримано!* Оцінювач перегляне його найближчим часом.",
            parse_mode="Markdown",
            reply_markup=kb_back_menu(),
        )
        await _send_text_all(context, f"🎥 *ВІДЕО-ОГЛЯД (Web App)*\n\n{tag}")


# ══════════════════════════════════════════════════════════
#  БЛОК 1: ІДЕНТИФІКАЦІЯ
# ══════════════════════════════════════════════════════════

async def _start_ident(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (
        "🪪 *Ідентифікація клієнта* — Крок 1/2\n\n"
        "Надішліть *фото паспорта*:\n"
        "• Паспорт-книжечка → сторінки 1 та 2\n"
        "• ID-картка → обидві сторони\n\n"
        "⚠️ _Дані обробляються згідно з ЗУ «Про захист "
        "персональних даних» № 2297-VI та GDPR._"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=kb_skip_or_back(), parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text(
            text, reply_markup=kb_skip_or_back(), parse_mode="Markdown"
        )
    return IDENT_PASSPORT


async def handle_passport(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    if not (msg.photo or msg.document):
        await msg.reply_text("⚠️ Надішліть *фото* або файл паспорта.", parse_mode="Markdown")
        return IDENT_PASSPORT

    file_id = msg.photo[-1].file_id if msg.photo else msg.document.file_id
    context.user_data["passport_fid"]  = file_id
    context.user_data["passport_type"] = "photo" if msg.photo else "document"

    await msg.reply_text(
        "✅ Паспорт отримано!\n\n"
        "🤳 *Крок 2/2 — Селфі з паспортом*\n\n"
        "Надішліть фото вашого *обличчя поряд з паспортом у руці*.\n"
        "_Це потрібно для верифікації особи._",
        parse_mode="Markdown",
    )
    return IDENT_SELFIE


async def handle_selfie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    if not msg.photo:
        await msg.reply_text("⚠️ Надішліть *фото* (селфі з паспортом).", parse_mode="Markdown")
        return IDENT_SELFIE

    context.user_data["selfie_fid"] = msg.photo[-1].file_id
    tag = client_tag(update)

    await _send_text_all(context, f"🪪 *НОВА ІДЕНТИФІКАЦІЯ*\n\n{tag}")
    await _send_photo_all(context, context.user_data["passport_fid"], f"📄 *Паспорт*\n{tag}")
    await _send_photo_all(context, context.user_data["selfie_fid"],   f"🤳 *Селфі*\n{tag}")

    context.user_data.setdefault("full_steps_done", []).append("ident")

    if context.user_data.get("flow") == "full":
        await msg.reply_text(
            "✅ *Ідентифікацію завершено!*\n\nПереходимо до документів… 📄",
            parse_mode="Markdown",
        )
        return await _start_docs(update, context)

    await msg.reply_text(
        "✅ *Ідентифікацію успішно завершено!*\n\nВаші дані передані оцінювачу. Дякуємо!",
        reply_markup=kb_back_menu(),
        parse_mode="Markdown",
    )
    return MAIN_MENU


async def handle_ident_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "step_skip" and context.user_data.get("flow") == "full":
        await query.edit_message_text("⏭ Ідентифікацію пропущено.")
        return await _start_docs(update, context)
    if query.data == "back_main":
        return await show_main_menu(update, context, edit=True)
    return IDENT_PASSPORT


# ══════════════════════════════════════════════════════════
#  БЛОК 2: ДОКУМЕНТИ
# ══════════════════════════════════════════════════════════

async def _start_docs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("uploaded_doc_types", [])
    text = (
        "📄 *Надсилання документів*\n\n"
        "Оберіть *тип документа*, який хочете надіслати.\n"
        "Можна надіслати кілька документів різних типів."
    )
    kbd = kb_doc_types(context.user_data["uploaded_doc_types"])
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kbd, parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(text, reply_markup=kbd, parse_mode="Markdown")
    return DOC_CHOOSE


async def handle_doc_choose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "back_main":
        return await show_main_menu(update, context, edit=True)

    if data == "doc_done":
        uploaded = context.user_data.get("uploaded_doc_types", [])
        if not uploaded:
            await query.edit_message_text(
                "⚠️ Ви ще не надіслали жодного документа.\nОберіть тип і надішліть файл.",
                reply_markup=kb_doc_types([]),
            )
            return DOC_CHOOSE

        tag = client_tag(update)
        await _send_text_all(
            context,
            f"📄 *ДОКУМЕНТИ ОТРИМАНО*\n\n{tag}\n\n"
            f"Кількість: {len(uploaded)}\n"
            f"Типи: {', '.join(DOCUMENT_TYPES.get(k, k) for k in set(uploaded))}",
        )
        context.user_data.setdefault("full_steps_done", []).append("docs")

        if context.user_data.get("flow") == "full":
            await query.edit_message_text(
                f"✅ *Документи надіслано!* ({len(uploaded)} шт.)\n\nПереходимо до геолокації… 📍",
                parse_mode="Markdown",
            )
            return await _start_location(update, context)

        await query.edit_message_text(
            f"✅ *Документи успішно надіслано!* ({len(uploaded)} шт.)\n\n"
            "Оцінювач перевірить їх найближчим часом.",
            reply_markup=kb_back_menu(),
            parse_mode="Markdown",
        )
        return MAIN_MENU

    if data in DOCUMENT_TYPES:
        context.user_data["current_doc_key"] = data
        label = DOCUMENT_TYPES[data]
        await query.edit_message_text(
            f"📎 *{label}*\n\nНадішліть файл або фото документа.\n"
            "_Підтримуються: фото, PDF, JPEG, PNG_",
            parse_mode="Markdown",
        )
        return DOC_UPLOAD

    return DOC_CHOOSE


async def handle_doc_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    doc_key = context.user_data.get("current_doc_key", "doc_other")
    label   = DOCUMENT_TYPES.get(doc_key, "Документ")

    if msg.photo:
        file_id, is_photo = msg.photo[-1].file_id, True
    elif msg.document:
        file_id, is_photo = msg.document.file_id, False
    else:
        await msg.reply_text("⚠️ Надішліть файл або фото документа.")
        return DOC_UPLOAD

    uploaded = context.user_data.setdefault("uploaded_doc_types", [])
    uploaded.append(doc_key)

    tag = client_tag(update)
    caption = f"*{label}*\n{tag}"
    if is_photo:
        await _send_photo_all(context, file_id, caption)
    else:
        await _send_document_all(context, file_id, caption)

    await msg.reply_text(
        f"✅ *{label}* отримано! (Надіслано: {len(uploaded)} шт.)\n\n"
        "Оберіть ще тип або завершіть надсилання:",
        reply_markup=kb_doc_types(uploaded),
        parse_mode="Markdown",
    )
    return DOC_CHOOSE


# ══════════════════════════════════════════════════════════
#  БЛОК 3: ГЕОЛОКАЦІЯ
# ══════════════════════════════════════════════════════════

async def _start_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (
        "📍 *Геолокація об'єкта оцінки*\n\n"
        "Натисніть кнопку нижче, перебуваючи біля об'єкта, "
        "або введіть *адресу текстом*.\n\n"
        "📌 _Координати будуть автоматично зафіксовані у справі._"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=kb_skip_or_back(), parse_mode="Markdown"
        )
        await context.bot.send_message(
            update.effective_chat.id,
            "👇 Натисніть кнопку або введіть адресу:",
            reply_markup=kb_location(),
        )
    else:
        await update.effective_message.reply_text(text, parse_mode="Markdown")
        await update.effective_message.reply_text(
            "👇 Натисніть кнопку або введіть адресу:",
            reply_markup=kb_location(),
        )
    return LOC_RECEIVE


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message

    if msg.location:
        lat, lon = msg.location.latitude, msg.location.longitude
        context.user_data["location"] = {"lat": lat, "lon": lon}
        maps_url = f"https://maps.google.com/?q={lat},{lon}"

        tag = client_tag(update)
        await _send_text_all(
            context,
            f"📍 *ГЕОЛОКАЦІЯ ОБ'ЄКТА*\n\n{tag}\n\n"
            f"🗺 [Google Maps]({maps_url})\n"
            f"Координати: `{lat:.6f}, {lon:.6f}`",
        )
        await _send_location_all(context, lat, lon)

        success = (
            f"✅ *Геолокацію зафіксовано!*\n\n"
            f"📌 Координати: `{lat:.5f}, {lon:.5f}`\n"
            f"🗺 [Переглянути на Google Maps]({maps_url})"
        )

    elif msg.text and not msg.text.startswith("/"):
        address = msg.text.strip()
        context.user_data["location"] = {"address": address}
        tag = client_tag(update)
        await _send_text_all(context, f"📍 *АДРЕСА ОБ'ЄКТА*\n\n{tag}\n\n📬 {address}")
        success = f"✅ *Адресу зафіксовано!*\n\n📬 {address}"

    else:
        await msg.reply_text("⚠️ Поділіться геолокацією або введіть адресу текстом.")
        return LOC_RECEIVE

    context.user_data.setdefault("full_steps_done", []).append("location")

    if context.user_data.get("flow") == "full":
        await msg.reply_text(
            success + "\n\nПереходимо до відеоогляду! 🎥",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown",
        )
        return await _start_video(update, context)

    await msg.reply_text(success, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    await msg.reply_text(
        "Дякуємо! Оцінювач отримав місцезнаходження об'єкта.",
        reply_markup=kb_back_menu(),
    )
    return MAIN_MENU


async def handle_loc_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "step_skip" and context.user_data.get("flow") == "full":
        await query.edit_message_text("⏭ Геолокацію пропущено.")
        return await _start_video(update, context)
    if query.data == "back_main":
        return await show_main_menu(update, context, edit=True)
    return LOC_RECEIVE


# ══════════════════════════════════════════════════════════
#  БЛОК 4: ВІДЕООГЛЯД
# ══════════════════════════════════════════════════════════

async def _start_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (
        "🎥 *Відеоогляд об'єкта*\n\n"
        "Оберіть *зручний час* для відеодзвінка з оцінювачем.\n\n"
        "📅 Доступні дні: *Пн–Пт*\n"
        "📲 Дзвінок відбудеться через *Telegram*"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=kb_video_slots(), parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text(
            text, reply_markup=kb_video_slots(), parse_mode="Markdown"
        )
    return VIDEO_TIME


async def handle_video_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "back_main":
        return await show_main_menu(update, context, edit=True)

    if query.data.startswith("slot_"):
        slot = query.data.replace("slot_", "")
        context.user_data["video_slot"] = slot
        tag = client_tag(update)

        await _send_text_all(
            context,
            f"🎥 *ЗАПИТ НА ВІДЕООГЛЯД*\n\n{tag}\n\n"
            f"⏰ Бажаний час: *{slot}*\n"
            f"📅 Найближчий робочий день\n\n"
            f"▶️ Зв'яжіться з клієнтом для підтвердження.",
        )

        if context.user_data.get("flow") == "full":
            done = context.user_data.get("full_steps_done", [])
            steps = "\n".join([
                f"{'✅' if 'ident'    in done else '⏭'} Ідентифікація",
                f"{'✅' if 'docs'     in done else '⏭'} Документи",
                f"{'✅' if 'location' in done else '⏭'} Геолокація",
                "✅ Відеоогляд — заплановано",
            ])
            await query.edit_message_text(
                f"🎉 *Повну процедуру завершено!*\n\n{steps}\n\n"
                f"⏰ Відеодзвінок: *{slot}* (Пн–Пт)\n\n"
                "Дякуємо за довіру до *ОЦІНКА24*! 🏢\n"
                "Ми зв'яжемося з вами для підтвердження.",
                reply_markup=kb_back_menu(),
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_text(
                f"✅ *Запит на відеоогляд прийнято!*\n\n"
                f"⏰ Бажаний час: *{slot}*\n"
                f"📅 Найближчий робочий день (Пн–Пт)\n\n"
                "📲 Оцінювач зв'яжеться для підтвердження.",
                reply_markup=kb_back_menu(),
                parse_mode="Markdown",
            )
        return MAIN_MENU

    return VIDEO_TIME


# ══════════════════════════════════════════════════════════
#  ДОПОМІЖНІ ОБРОБНИКИ
# ══════════════════════════════════════════════════════════

async def handle_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
    return await show_main_menu(update, context, edit=bool(query))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Помилка: {context.error}", exc_info=context.error)


# ══════════════════════════════════════════════════════════
#  ЗБІРКА ЗАСТОСУНКУ
# ══════════════════════════════════════════════════════════

def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    # Web App data handler (поза ConversationHandler — глобальний)
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            MAIN_MENU: [CallbackQueryHandler(handle_main_menu)],
            IDENT_PASSPORT: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, handle_passport),
                CallbackQueryHandler(handle_ident_skip, pattern="^(step_skip|back_main)$"),
            ],
            IDENT_SELFIE: [
                MessageHandler(filters.PHOTO, handle_selfie),
                CallbackQueryHandler(handle_back_to_menu, pattern="^back_main$"),
            ],
            DOC_CHOOSE: [CallbackQueryHandler(handle_doc_choose)],
            DOC_UPLOAD: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, handle_doc_upload),
                CallbackQueryHandler(handle_doc_choose, pattern="^(doc_done|back_main)$"),
            ],
            LOC_RECEIVE: [
                MessageHandler((filters.LOCATION | filters.TEXT) & ~filters.COMMAND, handle_location),
                CallbackQueryHandler(handle_loc_skip, pattern="^(step_skip|back_main)$"),
            ],
            VIDEO_TIME: [CallbackQueryHandler(handle_video_time)],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start",  cmd_start),
        ],
        allow_reentry=True,
        name="otsinka24_conv",
    )

    app.add_handler(conv)
    app.add_error_handler(error_handler)
    return app


def main() -> None:
    logger.info("🚀 Запуск бота ОЦІНКА24 v2.0...")
    logger.info(f"   Адмінів: {len(ADMIN_IDS)}")
    logger.info(f"   Канал:   {'налаштовано' if CHANNEL_ID else 'не налаштовано'}")
    logger.info(f"   Web App: {'налаштовано' if WEBAPP_URL else 'не налаштовано'}")
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
