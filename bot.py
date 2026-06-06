import os
import logging
import json
import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, PreCheckoutQueryHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://astrocartography-blond.vercel.app")
STARS_PRICE = int(os.environ.get("STARS_PRICE", "50"))

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Используем ReplyKeyboard с WebApp кнопкой — только так работает sendData
    keyboard = [[KeyboardButton("🌍 Открыть Астрокартографию", web_app=WebAppInfo(url=WEBAPP_URL))]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "✦ *АСТРОКАРТОГРАФИЯ*\n\n"
        "Узнай лучшие места на Земле для жизни, карьеры и любви по дате рождения.\n\n"
        "Нажми кнопку ниже 👇",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    if query.invoice_payload.startswith("deep_analysis"):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Что-то пошло не так.")

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payload = update.message.successful_payment.invoice_payload
    birth_date = "неизвестна"
    birth_time = "неизвестно"
    birth_city = "неизвестен"
    try:
        parts = payload.split("|")
        if len(parts) >= 4:
            birth_date = parts[1]
            birth_time = parts[2]
            birth_city = parts[3]
    except:
        pass

    await update.message.reply_text(
        "✅ Оплата прошла! Генерирую твой персональный анализ... ⏳\n\nЭто займёт 15-30 секунд."
    )

    analysis = generate_analysis(birth_date, birth_time, birth_city)
    chunks = [analysis[i:i+4000] for i in range(0, len(analysis), 4000)]
    for chunk in chunks:
        try:
            await update.message.reply_text(chunk, parse_mode="Markdown")
        except:
            await update.message.reply_text(chunk)

async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data_str = update.effective_message.web_app_data.data
        logger.info(f"WebApp data: {data_str}")
        data = json.loads(data_str)

        if data.get("action") == "deep_analysis":
            birth_date = data.get("date", "неизвестна")
            birth_time = data.get("time", "неизвестно")
            birth_city = data.get("city", "неизвестен")
            payload = f"deep_analysis|{birth_date}|{birth_time}|{birth_city}"

            chat_id = update.effective_chat.id
            await context.bot.send_invoice(
                chat_id=chat_id,
                title="🔮 Глубокий астро-анализ",
                description=f"Персональный разбор для {birth_city}, {birth_date}. Топ городов, карьера, любовь, здоровье.",
                payload=payload,
                currency="XTR",
                provider_token="",
                prices=[LabeledPrice("Глубокий анализ", STARS_PRICE)],
            )
    except Exception as e:
        logger.error(f"WebApp data error: {e}")

def generate_analysis(birth_date: str, birth_time: str, birth_city: str) -> str:
    try:
        prompt = f"""Ты профессиональный астролог-астрокартограф с 20-летним опытом.

Данные клиента:
- Дата рождения: {birth_date}
- Время рождения: {birth_time}
- Место рождения: {birth_city}

Составь глубокий персональный анализ астрокарты для релокации. Структура:

1. *Общая характеристика* — 3-4 предложения о натальной карте

2. *Топ-3 города для релокации* — с подробным объяснением, укажи планеты и зоны AC/MC/DC/IC

3. *Карьера и финансы* — в каких направлениях искать возможности

4. *Любовь и отношения* — где высока вероятность встретить партнёра

5. *Здоровье и энергетика* — какие места дадут силу

6. *Главный совет* — одно ключевое послание

Пиши тепло и лично. Длина: 400-500 слов."""

        message = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return "Произошла ошибка. Напиши нам — мы вернём Stars или пришлём анализ вручную."

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
