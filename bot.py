# bot.py
import os
import json
import logging
from typing import List, Dict, Any, Optional
from aiogram.client.default import DefaultBotProperties

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, Update, KeyboardButton, ReplyKeyboardMarkup,
    ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, ContentType
)
from aiogram.filters import Command

import gspread
from google.oauth2 import service_account
from datetime import datetime

# ---------- Config & Logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("survey-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()  # raw JSON or base64

BASE_URL = os.getenv("BASE_URL", "").strip()  # e.g. https://your-service.onrender.com

ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

# ---------- Google Sheets Helper ----------
gc_client = None
sheet = None

def _init_sheets():
    global gc_client, sheet
    if not GOOGLE_SHEET_ID or not GOOGLE_SERVICE_ACCOUNT_JSON:
        log.warning("Google Sheets is not fully configured; responses will not be saved to Sheets.")
        return None

    # Accept raw JSON or base64-encoded JSON
    try:
        sa_info: Dict[str, Any]
        if GOOGLE_SERVICE_ACCOUNT_JSON.strip().startswith("{"):
            sa_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        else:
            import base64
            decoded = base64.b64decode(GOOGLE_SERVICE_ACCOUNT_JSON).decode("utf-8")
            sa_info = json.loads(decoded)

        creds = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        )
        gc_client = gspread.authorize(creds)
        sheet = gc_client.open_by_key(GOOGLE_SHEET_ID).sheet1
        # Ensure header row exists
        header = sheet.row_values(1)
        wanted = [
            "timestamp", "user_id", "username", "company", "city", "fleet_size",
            "lead_channels", "features", "pilot_interest", "contact_name", "contact_phone"
        ]
        if header != wanted:
            if len(header) == 0:
                sheet.insert_row(wanted, 1)
            else:
                # Overwrite header to keep it simple
                sheet.delete_rows(1)
                sheet.insert_row(wanted, 1)

        log.info("✅ Google Sheets connected")
    except Exception as e:
        log.exception("Google Sheets init failed: %s", e)

_init_sheets()

def save_response_to_sheet(row: Dict[str, Any]) -> bool:
    if sheet is None:
        log.warning("Sheet is not available; skip append")
        return False
    try:
        sheet.append_row([
            row.get("timestamp", ""),
            str(row.get("user_id", "")),
            row.get("username", ""),
            row.get("company", ""),
            row.get("city", ""),
            row.get("fleet_size", ""),
            row.get("lead_channels", ""),
            row.get("features", ""),
            row.get("pilot_interest", ""),
            row.get("contact_name", ""),
            row.get("contact_phone", ""),
        ], value_input_option="USER_ENTERED")
        return True
    except Exception as e:
        log.exception("Append to sheet failed: %s", e)
        return False

# ---------- Survey Definition ----------
# You can edit these without touching the logic.
FEATURES = [
    "Онлайн-оплата (Click/Payme)",
    "Рейтинг клиентов (скоринг)",
    "Аналитика и отчёты",
    "Админ-панель в Telegram",
    "API/1C интеграции",
    "Видимость в агрегаторе (витрина)",
]

SURVEY: List[Dict[str, Any]] = [
    {"key": "company", "text": "Как называется ваш автопарк/компания?", "type": "text"},
    {"key": "city", "text": "В каком городе вы работаете?", "type": "text"},
    {"key": "fleet_size", "text": "Сколько машин в автопарке (примерно)?", "type": "text"},
    {"key": "lead_channels", "text": "Где сейчас берёте клиентов? (Instagram, Telegram, сайт, Avtoelon и т.п.)", "type": "text"},
    {"key": "features", "text": "Какие функции для вас важны? Отметьте кнопками, затем нажмите «Готово».", "type": "multiselect", "options": FEATURES},
    {"key": "pilot_interest", "text": "Готовы участвовать в пилоте? (Да/Нет)", "type": "choice", "options": ["Да", "Нет"]},
    {"key": "contact_name", "text": "Как связаться: контактное лицо (ФИО)?", "type": "text"},
    {"key": "contact_phone", "text": "Оставьте номер телефона (или нажмите кнопку ниже «Отправить контакт»).", "type": "phone"},
]

# ---------- Bot / Dispatcher ----------
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# Per-user state storage (simple in-memory dict)
# In production consider redis storage for horizontal scaling.
STATE: Dict[int, Dict[str, Any]] = {}

def get_user_state(user_id: int) -> Dict[str, Any]:
    st = STATE.get(user_id)
    if not st:
        st = {"q": 0, "answers": {}, "features_selected": set()}
        STATE[user_id] = st
    return st

# ---------- Keyboards ----------
def kb_main():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Пройти опрос (2 минуты)", callback_data="start_survey")],
        [InlineKeyboardButton(text="📞 Оставить контакт без опроса", callback_data="leave_contact")],
    ])

def kb_features(selected: set, options: List[str]) -> InlineKeyboardMarkup:
    rows = []
    for opt in options:
        mark = "✅" if opt in selected else "☐"
        rows.append([InlineKeyboardButton(text=f"{mark} {opt}", callback_data=f"feat:{opt}")])
    rows.append([InlineKeyboardButton(text="Готово ✅", callback_data="feat_done")])
    rows.append([InlineKeyboardButton(text="Отмена ↩️", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_yes_no() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да", callback_data="choice:Да"),
        InlineKeyboardButton(text="Нет", callback_data="choice:Нет"),
    ], [InlineKeyboardButton(text="Отмена ↩️", callback_data="cancel")]])

def kb_request_contact() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отправить контакт", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

# ---------- Handlers ----------
@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Мы готовим платформу, которая помогает автопаркам получать клиентов напрямую.\n"
        "Хотим учесть ваши пожелания — ответьте на пару вопросов 🙌",
        reply_markup=kb_main()
    )

@router.callback_query(F.data == "start_survey")
async def cb_start_survey(call: CallbackQuery):
    st = get_user_state(call.from_user.id)
    st["q"] = 0
    st["answers"] = {}
    st["features_selected"] = set()

    await call.message.answer("Начнём! Можно остановиться в любой момент командой /cancel.")
    await ask_next_question(call.from_user.id, call.message)
    await call.answer()

@router.message(Command("cancel"))
async def cmd_cancel(message: Message):
    STATE.pop(message.from_user.id, None)
    await message.answer("Окей, остановил опрос. Возвращайтесь, когда будет удобно.", reply_markup=ReplyKeyboardRemove())

@router.callback_query(F.data == "leave_contact")
async def cb_leave_contact(call: CallbackQuery):
    st = get_user_state(call.from_user.id)
    st["q"] = len(SURVEY) - 2  # Jump to contact_name
    await call.message.answer("Оставьте, пожалуйста, контактное лицо (ФИО).")
    await call.answer()

async def ask_next_question(user_id: int, message: Message):
    st = get_user_state(user_id)
    q_idx = st["q"]
    if q_idx >= len(SURVEY):
        await finish_survey(user_id, message)
        return

    q = SURVEY[q_idx]
    t = q["text"]
    typ = q["type"]

    if typ == "multiselect":
        await message.answer(t, reply_markup=kb_features(st["features_selected"], q["options"]))
    elif typ == "choice":
        await message.answer(t, reply_markup=kb_yes_no())
    elif typ == "phone":
        await message.answer(t, reply_markup=kb_request_contact())
    else:
        await message.answer(t)

@router.message(F.content_type == ContentType.CONTACT)
async def on_contact(message: Message):
    st = get_user_state(message.from_user.id)
    q = SURVEY[st["q"]]
    if q["type"] == "phone":
        st["answers"][q["key"]] = message.contact.phone_number
        st["q"] += 1
        await message.answer("Спасибо! 👍", reply_markup=ReplyKeyboardRemove())
        await ask_next_question(message.from_user.id, message)

@router.message(F.text)
async def on_text(message: Message):
    st = get_user_state(message.from_user.id)
    if "q" not in st:
        await cmd_start(message)
        return

    q_idx = st["q"]
    if q_idx >= len(SURVEY):
        await message.answer("Мы уже закончили опрос. Нажмите /start, чтобы начать заново.")
        return

    q = SURVEY[q_idx]
    typ = q["type"]

    if typ in ("text",):
        st["answers"][q["key"]] = message.text.strip()
        st["q"] += 1
        await ask_next_question(message.from_user.id, message)
    elif typ == "choice":
        # If user types instead of pressing a button
        val = message.text.strip()
        if val not in q["options"]:
            await message.answer("Пожалуйста, нажмите кнопку «Да» или «Нет».")
            return
        st["answers"][q["key"]] = val
        st["q"] += 1
        await ask_next_question(message.from_user.id, message)
    elif typ == "phone":
        # Accept free-form phone text
        st["answers"][q["key"]] = message.text.strip()
        st["q"] += 1
        await message.answer("Спасибо! 👍", reply_markup=ReplyKeyboardRemove())
        await ask_next_question(message.from_user.id, message)
    else:
        await message.answer("Нажмите, пожалуйста, кнопки под сообщением.")

@router.callback_query(F.data.startswith("feat:"))
async def cb_toggle_feature(call: CallbackQuery):
    st = get_user_state(call.from_user.id)
    q = SURVEY[st["q"]]
    if q["type"] != "multiselect":
        await call.answer()
        return

    opt = call.data.split(":", 1)[1]
    if opt in st["features_selected"]:
        st["features_selected"].remove(opt)
    else:
        st["features_selected"].add(opt)

    await call.message.edit_reply_markup(reply_markup=kb_features(st["features_selected"], q["options"]))
    await call.answer("Обновлено")

@router.callback_query(F.data == "feat_done")
async def cb_feature_done(call: CallbackQuery):
    st = get_user_state(call.from_user.id)
    q = SURVEY[st["q"]]
    st["answers"][q["key"]] = ", ".join(st["features_selected"]) if st["features_selected"] else ""
    st["q"] += 1
    await call.message.answer("Принято ✅")
    await ask_next_question(call.from_user.id, call.message)
    await call.answer()

@router.callback_query(F.data.startswith("choice:"))
async def cb_choice(call: CallbackQuery):
    st = get_user_state(call.from_user.id)
    q = SURVEY[st["q"]]
    if q["type"] != "choice":
        await call.answer()
        return

    _, val = call.data.split(":", 1)
    st["answers"][q["key"]] = val
    st["q"] += 1
    await call.message.answer(f"Ответ: <b>{val}</b>")
    await ask_next_question(call.from_user.id, call.message)
    await call.answer()

@router.callback_query(F.data == "cancel")
async def cb_cancel(call: CallbackQuery):
    STATE.pop(call.from_user.id, None)
    await call.message.answer("Окей, остановил опрос. Возвращайтесь, когда будет удобно.", reply_markup=ReplyKeyboardRemove())
    await call.answer()

async def finish_survey(user_id: int, message: Message):
    st = get_user_state(user_id)
    answers = st.get("answers", {})
    # Persist to Google Sheets (if configured)
    row = {
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
        "user_id": user_id,
        "username": f"@{message.from_user.username}" if message.from_user.username else "",
        "company": answers.get("company", ""),
        "city": answers.get("city", ""),
        "fleet_size": answers.get("fleet_size", ""),
        "lead_channels": answers.get("lead_channels", ""),
        "features": answers.get("features", ""),
        "pilot_interest": answers.get("pilot_interest", ""),
        "contact_name": answers.get("contact_name", ""),
        "contact_phone": answers.get("contact_phone", ""),
    }
    ok = save_response_to_sheet(row)

    # Fallback: save to local CSV if Sheets not available
    if not ok:
        try:
            import csv, os
            exists = os.path.exists("responses.csv")
            with open("responses.csv", "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(row.keys()))
                if not exists:
                    w.writeheader()
                w.writerow(row)
            ok = True
        except Exception as e:
            log.exception("Local CSV write failed: %s", e)

    STATE.pop(user_id, None)
    if ok:
        await message.answer("Спасибо! Ваши ответы сохранены. Мы свяжемся с вами по итогам беты 🙌", reply_markup=ReplyKeyboardRemove())
    else:
        await message.answer("Спасибо! Ответы получены. (Не смог записать в таблицу — сохраняю у себя. Мы всё равно свяжемся.)", reply_markup=ReplyKeyboardRemove())

# ---------- FastAPI (webhook) ----------
app = FastAPI()

@app.get("/healthz")
async def healthz():
    return PlainTextResponse("ok")

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.post("/set-webhook")
async def set_webhook():
    if not BASE_URL:
        return {"ok": False, "error": "BASE_URL is not set"}
    await bot.set_webhook(f"{BASE_URL}/webhook", drop_pending_updates=True)
    return {"ok": True, "url": f"{BASE_URL}/webhook"}

# ---------- Local dev entry (polling) ----------
if __name__ == "__main__":
    import asyncio
    async def _main():
        log.info("Starting polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    asyncio.run(_main())

