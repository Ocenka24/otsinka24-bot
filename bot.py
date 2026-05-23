#!/usr/bin/env python3
"""ОЦІНКА24 — Telegram Bot v4.0"""
import logging, os, sys, uuid
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

import warnings
warnings.filterwarnings("ignore", category=UserWarning)

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
BOT_TOKEN  = os.getenv("BOT_TOKEN","");  assert BOT_TOKEN, "BOT_TOKEN відсутній"
ADMIN_IDS  = [int(x) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip().isdigit()]
CHANNEL_ID = int(os.getenv("CHANNEL_ID","0"))
WEBSITE    = "https://ocenka24.com.ua/"
EMAIL      = "info@ocenka24.com.ua"
PHONE1     = "0 800 502-977"
PHONE2     = "+38 (050) 3000-173"
LOGO       = "https://ocenka24.com.ua/img/ocenka24-logo.png"

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
        "📸 Фото кімнат, кухні, служб",
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
#  РОЗСИЛКА
# ══════════════════════════════════════════════════════════

async def notify(ctx, text):
    for a in ADMIN_IDS:
        try: await ctx.bot.send_message(a, text, parse_mode="Markdown")
        except Exception as e: logger.warning(f"Адмін {a}: {e}")
    if CHANNEL_ID:
        try: await ctx.bot.send_message(CHANNEL_ID, text, parse_mode="Markdown")
        except Exception as e: logger.warning(f"Канал: {e}")

async def notify_photo(ctx, fid, caption):
    for a in ADMIN_IDS:
        try: await ctx.bot.send_photo(a, fid, caption=caption, parse_mode="Markdown")
        except Exception as e: logger.warning(f"Адмін фото {a}: {e}")
    if CHANNEL_ID:
        try: await ctx.bot.send_photo(CHANNEL_ID, fid, caption=caption, parse_mode="Markdown")
        except Exception as e: logger.warning(f"Канал фото: {e}")

async def notify_doc(ctx, fid, caption):
    for a in ADMIN_IDS:
        try: await ctx.bot.send_document(a, fid, caption=caption, parse_mode="Markdown")
        except Exception as e: logger.warning(f"Адмін doc {a}: {e}")
    if CHANNEL_ID:
        try: await ctx.bot.send_document(CHANNEL_ID, fid, caption=caption, parse_mode="Markdown")
        except Exception as e: logger.warning(f"Канал doc: {e}")

async def notify_loc(ctx, lat, lon):
    for a in ADMIN_IDS:
        try: await ctx.bot.send_location(a, lat, lon)
        except Exception as e: logger.warning(f"Адмін loc {a}: {e}")
    if CHANNEL_ID:
        try: await ctx.bot.send_location(CHANNEL_ID, lat, lon)
        except Exception as e: logger.warning(f"Канал loc: {e}")

# ══════════════════════════════════════════════════════════
#  /start
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
    return MENU

async def cmd_cancel(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await upd.message.reply_text("❌ Скасовано.", reply_markup=ReplyKeyboardRemove())
    await upd.message.reply_text("🏠 Головне меню:", reply_markup=main_kb())
    return MENU

# ══════════════════════════════════════════════════════════
#  МЕНЮ
# ══════════════════════════════════════════════════════════

async def on_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = upd.callback_query
    await q.answer()
    d = q.data

    # Головне меню
    if d == "home":
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await ctx.bot.send_message(upd.effective_chat.id,
            "🏠 Головне меню:", reply_markup=main_kb())
        return MENU

    # Про компанію
    if d == "about":
        await q.edit_message_reply_markup(reply_markup=None)
        await ctx.bot.send_message(upd.effective_chat.id,
            "🏢 *ОЦІНКА24*\n\n"
            "✅ Сертифіковані оцінювачі (ЗУ «Про оцінку майна»)\n"
            "✅ Досвід роботи понад 10 років\n"
            "✅ Оцінка нерухомості, авто, бізнесу, збитків\n"
            "✅ Звіти для банків, нотаріусів, судів\n"
            "✅ Відповідність МСО та НСО України",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📞 Контакти",     callback_data="contact")],
                [InlineKeyboardButton("🏠 Головне меню", callback_data="home")],
            ]))
        return MENU

    # Контакти
    if d == "contact":
        await q.edit_message_reply_markup(reply_markup=None)
        await ctx.bot.send_message(upd.effective_chat.id,
            f"📞 *Контакти ОЦІНКА24*\n\n"
            f"☎️ {PHONE1}\n"
            f"📱 {PHONE2}\n"
            f"📧 `{EMAIL}`\n"
            f"🌐 {WEBSITE}\n\n"
            "🕐 *Графік:*\n"
            "Пн–Пт: 09:00–18:00\n"
            "Сб: 09:00–14:00 (за записом)\n"
            "Нд: вихідний",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("ℹ️ Про компанію", callback_data="about")],
                [InlineKeyboardButton("🏠 Головне меню", callback_data="home")],
            ]))
        return MENU

    # Геолокація
    if d == "location":
        await q.edit_message_reply_markup(reply_markup=None)
        await ctx.bot.send_message(upd.effective_chat.id,
            "📍 *Геолокація об'єкта оцінки*\n\n"
            "Перебуваючи біля об'єкта натисніть кнопку нижче,\n"
            "або введіть адресу текстом.",
            parse_mode="Markdown", reply_markup=home_kb())
        await ctx.bot.send_message(upd.effective_chat.id,
            "👇 Натисніть кнопку або введіть адресу:",
            reply_markup=gps_kb())
        return LOC

    # Відеоогляд
    if d == "video":
        return await start_video(upd, ctx)

    # Завершити надсилання документів
    if d == "done":
        return await finish_upload(upd, ctx)

    # Вибір об'єкта
    key = d.replace("obj_","")
    if key in OBJECTS:
        return await show_object(upd, ctx, key)

    return MENU

# ══════════════════════════════════════════════════════════
#  ДОКУМЕНТИ
# ══════════════════════════════════════════════════════════

async def show_object(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, key: str) -> int:
    icon, name, docs = OBJECTS[key]
    ctx.user_data["obj_key"]  = key
    ctx.user_data["obj_name"] = f"{icon} {name}"
    ctx.user_data["files"]    = []

    doc_list = "\n".join(f"  {i+1}. {d}" for i,d in enumerate(docs))
    text = (
        f"{icon} *{name}*\n\n"
        f"📋 *Необхідні документи:*\n{doc_list}\n\n"
        "Надсилайте документи та фото по одному.\n"
        "Коли надішлете всі — натисніть *«✅ Завершити надсилання»*."
    )
    q = upd.callback_query
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await ctx.bot.send_message(upd.effective_chat.id, text,
                               parse_mode="Markdown", reply_markup=upload_kb())
    return UPLOAD

async def handle_file(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    msg = upd.message
    name = ctx.user_data.get("obj_name","Документ")
    files = ctx.user_data.setdefault("files",[])
    u = msg.from_user

    caption = (
        f"{name}\n"
        f"👤 {u.full_name} | 🆔 `{u.id}`\n"
        f"📱 @{u.username or '—'}"
    )

    if msg.photo:
        files.append(msg.photo[-1].file_id)
        await notify_photo(ctx, msg.photo[-1].file_id, caption)
    elif msg.document:
        files.append(msg.document.file_id)
        await notify_doc(ctx, msg.document.file_id, caption)
    else:
        await msg.reply_text("⚠️ Надішліть фото або PDF документа.")
        return UPLOAD

    await msg.reply_text(
        f"✅ Файл прийнято! Надіслано: *{len(files)}* шт.\n\n"
        "Надсилайте ще або натисніть *«✅ Завершити надсилання»*",
        parse_mode="Markdown", reply_markup=upload_kb()
    )
    return UPLOAD

async def finish_upload(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = upd.callback_query
    u = upd.effective_user
    name  = ctx.user_data.get("obj_name","—")
    files = ctx.user_data.get("files",[])
    ts    = datetime.now().strftime("%d.%m.%Y %H:%M")

    if not files:
        await q.answer("⚠️ Надішліть хоча б один файл!", show_alert=True)
        return UPLOAD

    summary = (
        f"📋 *ДОКУМЕНТИ ОТРИМАНО*\n"
        f"{'─'*28}\n"
        f"👤 *{u.full_name}*\n"
        f"🆔 `{u.id}` | @{u.username or '—'}\n"
        f"🕐 {ts}\n\n"
        f"{name}\n"
        f"Файлів надіслано: *{len(files)}*\n\n"
        f"[✉️ Написати клієнту](tg://user?id={u.id})"
    )
    await notify(ctx, summary)

    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await ctx.bot.send_message(upd.effective_chat.id,
        f"✅ *Документи надіслано!*\n\n"
        f"📦 Файлів: *{len(files)}*\n"
        f"{name}\n\n"
        "Оцінювач перевірить їх і зв'яжеться з вами.",
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
            f"📱 @{u.username or '—'}\n"
            f"🕐 {ts}\n\n"
            f"📌 `{lat:.6f}, {lon:.6f}`\n"
            f"🗺 [Google Maps]({maps})\n\n"
            f"[✉️ Написати клієнту](tg://user?id={u.id})")
        await notify_loc(ctx, lat, lon)
        await msg.reply_text(
            f"✅ *Геолокацію зафіксовано!*\n\n"
            f"📌 `{lat:.5f}, {lon:.5f}`\n"
            f"🗺 [Google Maps]({maps})",
            parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())

    elif msg.text and not msg.text.startswith("/"):
        await notify(ctx,
            f"📍 *АДРЕСА ОБ'ЄКТА*\n"
            f"👤 *{u.full_name}* | 🆔 `{u.id}`\n"
            f"🕐 {ts}\n📬 {msg.text.strip()}\n\n"
            f"[✉️ Написати клієнту](tg://user?id={u.id})")
        await msg.reply_text(
            f"✅ *Адресу зафіксовано!*\n📬 {msg.text.strip()}",
            parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    else:
        await msg.reply_text("⚠️ Поділіться геолокацією або введіть адресу.")
        return LOC

    await ctx.bot.send_message(msg.chat.id,
        "Дякуємо! Оцінювач отримав місцезнаходження.",
        reply_markup=main_kb())
    return MENU

# ══════════════════════════════════════════════════════════
#  ВІДЕООГЛЯД (Jitsi)
# ══════════════════════════════════════════════════════════

async def start_video(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    u    = upd.effective_user
    ts   = datetime.now().strftime("%d.%m.%Y %H:%M")
    room = f"Otsinka24-{uuid.uuid4().hex[:12].upper()}"
    url  = f"https://meet.jit.si/{room}"
    ctx.user_data["jitsi"] = url

    await notify(ctx,
        f"📹 *ВІДЕООГЛЯД — ОНЛАЙН*\n"
        f"{'─'*28}\n"
        f"👤 *{u.full_name}* | 🆔 `{u.id}`\n"
        f"📱 @{u.username or '—'}\n"
        f"🕐 {ts}\n\n"
        f"📍 GPS — клієнт надсилає зараз...\n\n"
        f"🔗 Кімната: `{room}`\n"
        f"[📹 Приєднатися до відеодзвінка]({url})\n\n"
        f"⚡️ Клієнт підключається!\n"
        f"[✉️ Написати клієнту](tg://user?id={u.id})")

    q = upd.callback_query
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await ctx.bot.send_message(upd.effective_chat.id,
        "📹 *Відеоогляд розпочато!*\n\nОцінювач отримав сповіщення.",
        parse_mode="Markdown")

    await ctx.bot.send_message(upd.effective_chat.id,
        "📍 Поділіться геолокацією об'єкта:",
        reply_markup=gps_kb("📍 Надіслати геолокацію об'єкта"))

    await ctx.bot.send_message(upd.effective_chat.id,
        "👇 Натисніть щоб увійти у відеодзвінок з оцінювачем:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📹 Увійти у відеодзвінок", url=url)
        ]]))
    return VIDEOLOC

async def handle_video_loc(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    msg  = upd.message
    u    = msg.from_user
    ts   = datetime.now().strftime("%d.%m.%Y %H:%M")
    room = ctx.user_data.get("jitsi","")

    if msg.location:
        lat, lon = msg.location.latitude, msg.location.longitude
        maps = f"https://maps.google.com/?q={lat},{lon}"
        await notify(ctx,
            f"📍 *GPS ОБ'ЄКТА ОТРИМАНО*\n"
            f"👤 *{u.full_name}* | 🕐 {ts}\n"
            f"📌 `{lat:.6f}, {lon:.6f}`\n"
            f"🗺 [Google Maps]({maps})")
        await notify_loc(ctx, lat, lon)
        await msg.reply_text(
            f"✅ *GPS зафіксовано!*\n📌 `{lat:.5f}, {lon:.5f}`",
            parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())

    elif msg.text and not msg.text.startswith("/"):
        await notify(ctx,
            f"📍 *АДРЕСА ОБ'ЄКТА*\n"
            f"👤 {u.full_name} | 🕐 {ts}\n"
            f"📬 {msg.text.strip()}")
        await msg.reply_text(
            f"✅ Адресу зафіксовано!\n📬 {msg.text.strip()}",
            reply_markup=ReplyKeyboardRemove())
    else:
        await msg.reply_text("⚠️ Поділіться геолокацією або введіть адресу.")
        return VIDEOLOC

    if room:
        await ctx.bot.send_message(msg.chat.id,
            "👇 Увійдіть у відеодзвінок:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📹 Увійти у відеодзвінок", url=room)
            ]]))
    return MENU

# ══════════════════════════════════════════════════════════
#  ЗБІРКА
# ══════════════════════════════════════════════════════════

async def err(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Помилка: {ctx.error}", exc_info=ctx.error)

def build():
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
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
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start",  cmd_start),
        ],
        allow_reentry=True,
    )
    app.add_handler(conv)
    app.add_error_handler(err)
    return app

def main():
    logger.info("🚀 ОЦІНКА24 Bot v4.0")
    logger.info(f"   Адмінів: {len(ADMIN_IDS)}")
    logger.info(f"   Канал:   {'✅' if CHANNEL_ID else '—'}")
    build().run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
