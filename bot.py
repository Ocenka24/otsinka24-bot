#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════╗
║         TELEGRAM BOT — ОЦІНКА24                      ║
║         Версія: 1.0                                  ║
║         Мова: Python 3.10+                           ║
║         Бібліотека: python-telegram-bot v20+         ║
╚══════════════════════════════════════════════════════╝

Функції:
  • Ідентифікація клієнта (паспорт + селфі)
  • Надсилання документів (різні типи)
  • Геолокація об'єкта оцінки
  • Запис на відеоогляд
  • Повна процедура (всі кроки послідовно)
  • Адмін-панель із сповіщеннями
"""

import logging
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()  # Завантажує змінні з файлу .env автоматично

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
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

# ─── Придушення системних попереджень PTB ────────────────
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

# ─── Конфігурація ─────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    logger.critical("❌ BOT_TOKEN не знайдено! Заповніть файл .env")
    sys.exit(1)

_admin_raw = os.getenv("ADMIN_IDS", "")
if not _admin_raw:
    logger.critical("❌ ADMIN_IDS не знайдено! Заповніть файл .env")
    sys.exit(1)
ADMIN_IDS = [int(x.strip()) for x in _admin_raw.split(",") if x.strip().isdigit()]
# ADMIN_IDS — список Telegram ID адміністраторів (через кому у змінній середовища)

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
    "doc_title":      "📜 Правовстановлюючий документ",
    "doc_techpass":   "🗂 Технічний паспорт",
    "doc_extract":    "📋 Витяг з Держреєстру",
    "doc_plan":       "📐 План/схема об'єкта",
    "doc_cadastral":  "🗺 Кадастровий план (для землі)",
    "doc_id":         "🪪 Документ, що посвідчує особу",
    "doc_other":      "📎 Інший документ",
}

# ─── Доступний час для відеоогляду ───────────────────────
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
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🚀 ПОВНА ПРОЦЕДУРА (всі кроки)", callback_data="full_procedure"
        )],
        [
            InlineKeyboardButton("🪪 Ідентифікація",  callback_data="menu_ident"),
            InlineKeyboardButton("📄 Документи",       callback_data="menu_docs"),
        ],
        [
            InlineKeyboardButton("📍 Геолокація",      callback_data="menu_location"),
            InlineKeyboardButton("🎥 Відеоогляд",      callback_data="menu_video"),
        ],
        [
            InlineKeyboardButton("ℹ️ Про компанію",    callback_data="menu_about"),
            InlineKeyboardButton("📞 Контакти",        callback_data="menu_contact"),
        ],
    ])


def kb_doc_types(uploaded: list[str]) -> InlineKeyboardMarkup:
    """Клавіатура вибору типу документа з позначками ✅ вже надісланих."""
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
    for i, slot in enumerate(VIDEO_SLOTS):
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
#  ДОПОМІЖНІ ФУНКЦІЇ
# ══════════════════════════════════════════════════════════

def client_tag(update: Update) -> str:
    """Формує рядок з інформацією про клієнта для адміна."""
    u = update.effective_user
    ts = datetime.now().strftime("%d.%m.%Y %H:%M")
    return (
        f"👤 {u.full_name}  |  ID: {u.id}\n"
        f"📱 @{u.username or '—'}\n"
        f"🕐 {ts}"
    )


async def admin_notify(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, text, parse_mode="Markdown")
        except Exception as exc:
            logger.warning(f"Не вдалося надіслати сповіщення адміну {admin_id}: {exc}")


async def admin_send_photo(
    context: ContextTypes.DEFAULT_TYPE,
    file_id: str,
    caption: str,
) -> None:
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_photo(admin_id, file_id, caption=caption)
        except Exception as exc:
            logger.warning(f"Не вдалося надіслати фото адміну {admin_id}: {exc}")


async def admin_send_document(
    context: ContextTypes.DEFAULT_TYPE,
    file_id: str,
    caption: str,
) -> None:
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_document(admin_id, file_id, caption=caption)
        except Exception as exc:
            logger.warning(f"Не вдалося надіслати документ адміну {admin_id}: {exc}")


async def admin_send_location(
    context: ContextTypes.DEFAULT_TYPE,
    lat: float,
    lon: float,
) -> None:
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_location(admin_id, lat, lon)
        except Exception as exc:
            logger.warning(f"Не вдалося надіслати локацію адміну {admin_id}: {exc}")


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
#  /start
# ══════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    u = update.effective_user
    await update.message.reply_text(
        f"👋 Вітаємо, *{u.first_name}*!\n\n"
        "🏢 *ОЦІНКА24* — професійна оцінка майна по всій Україні.\n\n"
        "Через цього бота ви можете:\n"
        "• 🪪 Пройти ідентифікацію\n"
        "• 📄 Надіслати необхідні документи\n"
        "• 📍 Вказати місцезнаходження об'єкта\n"
        "• 🎥 Записатись на відеоогляд\n\n"
        "👇 Оберіть дію:",
        reply_markup=kb_main_menu(),
        parse_mode="Markdown",
    )
    return MAIN_MENU


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Скасовано.", reply_markup=ReplyKeyboardRemove()
    )
    return await show_main_menu(update, context)


# ══════════════════════════════════════════════════════════
#  ГОЛОВНЕ МЕНЮ — обробник кнопок
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
            "✅ Сертифіковані оцінювачі (відповідно до ЗУ «Про оцінку майна»)\n"
            "✅ Досвід роботи понад 10 років\n"
            "✅ Оцінка нерухомості, авто, бізнесу, збитків\n"
            "✅ Звіти для банків, нотаріусів, судів\n"
            "✅ Відповідність МСО та НСО України\n\n"
            "📬 info@otsinka24.ua\n"
            "🌐 www.otsinka24.ua",
            reply_markup=kb_back_menu(),
            parse_mode="Markdown",
        )
        return MAIN_MENU

    if data == "menu_contact":
        await query.edit_message_text(
            "📞 *Контакти ОЦІНКА24*\n\n"
            "☎️  +380 XX XXX XX XX\n"
            "📱 Viber / WhatsApp: +380 XX XXX XX XX\n"
            "📧 info@otsinka24.ua\n\n"
            "🕐 Пн–Пт: 09:00–18:00\n"
            "📅 Сб: 09:00–14:00 (лише за записом)",
            reply_markup=kb_back_menu(),
            parse_mode="Markdown",
        )
        return MAIN_MENU

    return MAIN_MENU


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
        await msg.reply_text(
            "⚠️ Надішліть *фото* або файл паспорта.",
            parse_mode="Markdown",
        )
        return IDENT_PASSPORT

    file_id = (msg.photo[-1].file_id if msg.photo else msg.document.file_id)
    context.user_data["passport_fid"] = file_id
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
        await msg.reply_text(
            "⚠️ Надішліть *фото* (селфі з паспортом).", parse_mode="Markdown"
        )
        return IDENT_SELFIE

    context.user_data["selfie_fid"] = msg.photo[-1].file_id
    tag = client_tag(update)

    # → адмін
    await admin_notify(context, f"🪪 *ІДЕНТИФІКАЦІЯ*\n\n{tag}")
    await admin_send_photo(
        context, context.user_data["passport_fid"],
        f"📄 Паспорт\n{tag}"
    )
    await admin_send_photo(
        context, context.user_data["selfie_fid"],
        f"🤳 Селфі\n{tag}"
    )

    context.user_data.setdefault("full_steps_done", []).append("ident")

    if context.user_data.get("flow") == "full":
        await msg.reply_text(
            "✅ *Ідентифікацію завершено!*\n\nПереходимо до документів… 📄",
            parse_mode="Markdown",
        )
        return await _start_docs(update, context)

    await msg.reply_text(
        "✅ *Ідентифікацію успішно завершено!*\n\n"
        "Ваші дані передані оцінювачу. Дякуємо!",
        reply_markup=kb_back_menu(),
        parse_mode="Markdown",
    )
    return MAIN_MENU


# ── Пропуск кроку ident (для full flow) ─────────────────
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
        await update.callback_query.edit_message_text(
            text, reply_markup=kbd, parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text(
            text, reply_markup=kbd, parse_mode="Markdown"
        )
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
        await admin_notify(
            context,
            f"📄 *ДОКУМЕНТИ ОТРИМАНО*\n\n{tag}\n\n"
            f"Кількість: {len(uploaded)}\n"
            f"Типи: {', '.join(DOCUMENT_TYPES.get(k, k) for k in set(uploaded))}",
        )

        context.user_data.setdefault("full_steps_done", []).append("docs")

        if context.user_data.get("flow") == "full":
            await query.edit_message_text(
                f"✅ *Документи надіслано!* ({len(uploaded)} шт.)\n\n"
                "Переходимо до геолокації… 📍",
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

    # Обраний тип документа
    if data in DOCUMENT_TYPES:
        context.user_data["current_doc_key"] = data
        label = DOCUMENT_TYPES[data]
        await query.edit_message_text(
            f"📎 *{label}*\n\n"
            "Надішліть файл або фото документа.\n"
            "_Підтримуються: фото, PDF, JPEG, PNG_",
            parse_mode="Markdown",
        )
        return DOC_UPLOAD

    return DOC_CHOOSE


async def handle_doc_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    doc_key = context.user_data.get("current_doc_key", "doc_other")
    label = DOCUMENT_TYPES.get(doc_key, "Документ")

    if msg.photo:
        file_id = msg.photo[-1].file_id
        is_photo = True
    elif msg.document:
        file_id = msg.document.file_id
        is_photo = False
    else:
        await msg.reply_text("⚠️ Надішліть файл або фото документа.")
        return DOC_UPLOAD

    # Фіксуємо
    uploaded = context.user_data.setdefault("uploaded_doc_types", [])
    uploaded.append(doc_key)

    # → адмін
    tag = client_tag(update)
    caption = f"{label}\n{tag}"
    if is_photo:
        await admin_send_photo(context, file_id, caption)
    else:
        await admin_send_document(context, file_id, caption)

    count = len(uploaded)
    await msg.reply_text(
        f"✅ *{label}* отримано! (Надіслано: {count} шт.)\n\n"
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
        await admin_notify(
            context,
            f"📍 *ГЕОЛОКАЦІЯ*\n\n{tag}\n\n"
            f"🗺 [Google Maps]({maps_url})\n"
            f"Координати: `{lat:.6f}, {lon:.6f}`",
        )
        await admin_send_location(context, lat, lon)

        success = (
            f"✅ *Геолокацію зафіксовано!*\n\n"
            f"📌 Координати: `{lat:.5f}, {lon:.5f}`\n"
            f"🗺 [Переглянути на Google Maps]({maps_url})"
        )

    elif msg.text and not msg.text.startswith("/"):
        address = msg.text.strip()
        context.user_data["location"] = {"address": address}
        tag = client_tag(update)
        await admin_notify(
            context, f"📍 *АДРЕСА ОБ'ЄКТА*\n\n{tag}\n\n📬 {address}"
        )
        success = f"✅ *Адресу зафіксовано!*\n\n📬 {address}"

    else:
        await msg.reply_text(
            "⚠️ Поділіться геолокацією або введіть адресу текстом."
        )
        return LOC_RECEIVE

    context.user_data.setdefault("full_steps_done", []).append("location")

    if context.user_data.get("flow") == "full":
        await msg.reply_text(
            success + "\n\nПереходимо до відеоогляду! 🎥",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown",
        )
        return await _start_video(update, context)

    await msg.reply_text(
        success,
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown",
    )
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

        await admin_notify(
            context,
            f"🎥 *ЗАПИТ НА ВІДЕООГЛЯД*\n\n{tag}\n\n"
            f"⏰ Бажаний час: *{slot}*\n"
            f"📅 Найближчий робочий день\n\n"
            f"▶️ Зв'яжіться з клієнтом для підтвердження.",
        )

        # Підсумок для full flow
        if context.user_data.get("flow") == "full":
            done = context.user_data.get("full_steps_done", [])
            steps_summary = "\n".join([
                f"{'✅' if 'ident'    in done else '⏭'} Ідентифікація",
                f"{'✅' if 'docs'     in done else '⏭'} Документи",
                f"{'✅' if 'location' in done else '⏭'} Геолокація",
                "✅ Відеоогляд — заплановано",
            ])
            await query.edit_message_text(
                f"🎉 *Повну процедуру завершено!*\n\n"
                f"{steps_summary}\n\n"
                f"⏰ Відеодзвінок: *{slot}* (найближчий Пн–Пт)\n\n"
                "Дякуємо за довіру до *ОЦІНКА24*!\n"
                "Ми зв'яжемося з вами для підтвердження. 🏢",
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
#  ОБРОБНИК ПОМИЛОК
# ══════════════════════════════════════════════════════════

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Помилка: {context.error}", exc_info=context.error)


# ══════════════════════════════════════════════════════════
#  ДОПОМІЖНІ ОБРОБНИКИ ДЛЯ ConversationHandler
# ══════════════════════════════════════════════════════════

async def handle_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Повернення до головного меню з будь-якого стану."""
    query = update.callback_query
    if query:
        await query.answer()
    return await show_main_menu(update, context, edit=bool(query))


# ══════════════════════════════════════════════════════════
#  ЗБІРКА ЗАСТОСУНКУ
# ══════════════════════════════════════════════════════════

def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(handle_main_menu),
            ],
            # ── Ідентифікація ──────────────────────────────
            IDENT_PASSPORT: [
                MessageHandler(
                    filters.PHOTO | filters.Document.ALL, handle_passport
                ),
                CallbackQueryHandler(handle_ident_skip, pattern="^(step_skip|back_main)$"),
            ],
            IDENT_SELFIE: [
                MessageHandler(filters.PHOTO, handle_selfie),
                CallbackQueryHandler(handle_back_to_menu, pattern="^back_main$"),
            ],
            # ── Документи ─────────────────────────────────
            DOC_CHOOSE: [
                CallbackQueryHandler(handle_doc_choose),
            ],
            DOC_UPLOAD: [
                MessageHandler(
                    filters.PHOTO | filters.Document.ALL, handle_doc_upload
                ),
                CallbackQueryHandler(handle_doc_choose, pattern="^(doc_done|back_main)$"),
            ],
            # ── Геолокація ────────────────────────────────
            LOC_RECEIVE: [
                MessageHandler(
                    (filters.LOCATION | filters.TEXT) & ~filters.COMMAND,
                    handle_location,
                ),
                CallbackQueryHandler(handle_loc_skip, pattern="^(step_skip|back_main)$"),
            ],
            # ── Відеоогляд ────────────────────────────────
            VIDEO_TIME: [
                CallbackQueryHandler(handle_video_time),
            ],
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
    logger.info("🚀 Запуск бота ОЦІНКА24...")
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
