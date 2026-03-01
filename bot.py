import os
import json
from datetime import date
from openai import OpenAI
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

LIMIT_PER_DAY = 5
TELEGRAM_MAX_MESSAGE = 4096
MODES = ("essay", "speaking", "qa")
MODE_LABELS = {"essay": "Insho tekshirish", "speaking": "Speaking analiz", "qa": "Savollar"}

WELCOME = "Assalomu alaykum! Bo‘limni tanlang:"
HELP_TEXT = (
    "📝 Insho — arabcha matn yuboring.\n"
    "🎤 Speaking — ovoz yuboring.\n"
    "❓ Savollar — savol yozing.\n\n"
    f"Kuniga {LIMIT_PER_DAY} marta."
)


def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Insho tekshirish", callback_data="essay")],
        [InlineKeyboardButton("🎤 Speaking analiz", callback_data="speaking")],
        [InlineKeyboardButton("❓ Arab tiliga oid savollar", callback_data="qa")],
    ])


def menu_btn():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Bosh menyu", callback_data="menu")]])

USAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage.json")
user_state = {}
user_usage = {}


def load_usage():
    global user_usage
    if not os.path.exists(USAGE_FILE):
        return
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        user_usage = {int(k): v for k, v in data.items()}
    except (json.JSONDecodeError, ValueError, IOError):
        user_usage = {}


def save_usage():
    try:
        data = {str(k): v for k, v in user_usage.items()}
        with open(USAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except IOError:
        pass


def split_message(text: str, max_len: int = TELEGRAM_MAX_MESSAGE - 100):
    parts = []
    while len(text) > max_len:
        parts.append(text[:max_len])
        text = text[max_len:]
    if text:
        parts.append(text)
    return parts


def today_str():
    return date.today().isoformat()


def get_usage(user_id):
    uid = user_id
    today = today_str()
    if uid not in user_usage or user_usage[uid].get("date") != today:
        user_usage[uid] = {"date": today, "essay": 0, "speaking": 0, "qa": 0}
        save_usage()
    return user_usage[uid]


def check_limit(user_id, mode):
    u = get_usage(user_id)
    return u[mode] < LIMIT_PER_DAY


def inc_usage(user_id, mode):
    u = get_usage(user_id)
    u[mode] += 1
    save_usage()


def limit_msg(user_id, mode):
    u = get_usage(user_id)
    return f"{u[mode]}/{LIMIT_PER_DAY}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state.pop(update.effective_user.id, None)
    await update.message.reply_text(WELCOME, reply_markup=main_keyboard())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    if data == "menu":
        user_state.pop(user_id, None)
        await query.message.reply_text(WELCOME, reply_markup=main_keyboard())
        return
    if data not in MODES:
        return
    if not check_limit(user_id, data):
        await query.message.reply_text("Limit tugadi. Ertaga.", reply_markup=menu_btn())
        return
    user_state[user_id] = data
    prompts = {
        "essay": f"Inshoni yuboring. {limit_msg(user_id, 'essay')}",
        "speaking": f"Ovoz yuboring. {limit_msg(user_id, 'speaking')}",
        "qa": f"Savol yozing. {limit_msg(user_id, 'qa')}",
    }
    await query.message.reply_text(prompts[data])


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = (update.message.text or "").strip()
    if user_id not in user_state:
        await update.message.reply_text("/start bosing.", reply_markup=menu_btn())
        return
    mode = user_state[user_id]
    if mode == "speaking":
        await update.message.reply_text("Ovoz yuboring.")
        return
    if mode not in ("essay", "qa"):
        return
    if not check_limit(user_id, mode):
        await update.message.reply_text("Limit tugadi. Ertaga.", reply_markup=menu_btn())
        return
    if not text:
        await update.message.reply_text("Matn yuboring.")
        return
    essay_system = (
        "Javobni o‘zbekcha, quyidagi sarlavhalar bilan yozing: "
        "«Grammatika:», «Lug‘at:», «Maslahat:». Har bir bo‘limda aniq va qisqa fikr bering."
    )
    qa_system = "Javobni o‘zbekcha, aniq va tushunarli qiling. Kerak bo‘lsa misol keltiring."
    prompt_essay = f"""Arab tilidagi quyidagi inshoni tekshir: grammatik va lug‘aviy xatolar, yaxshilash bo‘yicha maslahat.
Insho:
{text}"""
    prompt_qa = f"Arab tiliga oid savol (o‘zbekcha javob bering):\n{text}"
    loading = await update.message.reply_text("⏳ Tekshirilmoqda...")
    try:
        system = essay_system if mode == "essay" else qa_system
        content = prompt_essay if mode == "essay" else prompt_qa
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        )
        answer = response.choices[0].message.content or ""
        inc_usage(user_id, mode)
        footer = "\n\n" + limit_msg(user_id, mode)
        full = answer + footer
        try:
            await loading.delete()
        except Exception:
            pass
        if len(full) <= TELEGRAM_MAX_MESSAGE:
            await update.message.reply_text(full, reply_markup=menu_btn())
        else:
            for part in split_message(answer):
                await update.message.reply_text(part)
            await update.message.reply_text(limit_msg(user_id, mode), reply_markup=menu_btn())
    except Exception:
        try:
            await loading.delete()
        except Exception:
            pass
        await update.message.reply_text("Xatolik. Qayta urinib ko‘ring.", reply_markup=menu_btn())


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_state.get(user_id) != "speaking":
        return
    if not check_limit(user_id, "speaking"):
        await update.message.reply_text("Limit tugadi. Ertaga.", reply_markup=menu_btn())
        return
    path = f"audio_{user_id}_{update.update_id}.ogg"
    loading = await update.message.reply_text("⏳ Tahlil qilinmoqda...")
    try:
        file = await update.message.voice.get_file()
        await file.download_to_drive(path)
        with open(path, "rb") as f:
            transcript = openai_client.audio.transcriptions.create(model="whisper-1", file=f)
        text = (transcript.text or "").strip()
        if len(text) < 3:
            await loading.edit_text("Ovoz aniqlanmadi. Qayta yuboring.", reply_markup=menu_btn())
            return
        system = (
            "Javobni o‘zbekcha, quyidagi sarlavhalar bilan yozing: "
            "«Talaffuz:», «Grammatika:», «Maslahat:». Har birida qisqa va aniq fikr."
        )
        prompt = f"Quyidagi arabcha nutq transkriptini baholang (talaffuz, grammatika, yaxshilash).\nMatn:\n{text}"
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        answer = response.choices[0].message.content or ""
        inc_usage(user_id, "speaking")
        try:
            await loading.delete()
        except Exception:
            pass
        block1 = f"📄 Transkript:\n{text}\n\n📊 Feedback:\n{answer}"
        footer = "\n\n" + limit_msg(user_id, "speaking")
        if len(block1) + len(footer) <= TELEGRAM_MAX_MESSAGE:
            await update.message.reply_text(block1 + footer, reply_markup=menu_btn())
        else:
            await update.message.reply_text(f"📄 Transkript:\n{text}")
            for part in split_message("📊 Feedback:\n" + answer):
                await update.message.reply_text(part)
            await update.message.reply_text(limit_msg(user_id, "speaking"), reply_markup=menu_btn())
    except Exception:
        try:
            await loading.delete()
        except Exception:
            pass
        await update.message.reply_text("Xatolik. Qayta urinib ko‘ring.", reply_markup=menu_btn())
    finally:
        if os.path.exists(path):
            os.remove(path)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    if update and isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("Xatolik. Qayta urinib ko‘ring.", reply_markup=menu_btn())


def main():
    if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
        print("XATO: .env da TELEGRAM_TOKEN va OPENAI_API_KEY to'ldiring.")
        return
    load_usage()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_audio))
    app.add_error_handler(error_handler)
    print("Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
