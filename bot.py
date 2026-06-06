import os
import logging
import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, PreCheckoutQueryHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://astrocartography-blond.vercel.app")
STARS_PRICE = int(os.environ.get("STARS_PRICE", "50"))

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🌍 Открыть Астрокартографию", web_app={"url": WEBAPP_URL})]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "✦ *АСТРОКАРТОГРАФИЯ*\n\n"
        "Узнай лучшие места на Земле для жизни, карьеры и любви по дате рождения.\n\n"
        "Нажми кнопку чтобы открыть карту 👇",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_invoice(update, context)

async def send_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_invoice(
        chat_id=chat_id,
        title="🔮 Глубокий астро-анализ",
        description="Персональный разбор твоей астрокарты: лучшие города для релокации, карьеры, любви и здоровья — написанный специально для тебя.",
        payload="deep_analysis",
        currency="XTR",
        prices=[LabeledPrice("Глубокий анализ", STARS_PRICE)],
    )

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    if query.invoice_payload != "deep_analysis":
        await query.answer(ok=False, error_message="Что-то пошло не так. Попробуй снова.")
    else:
        await query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data.get("birth_data", {})
    birth_date = user_data.get("date", "неизвестна")
    birth_time = user_data.get("time", "неизвестно")
    birth_city = user_data.get("city", "неизвестен")

    await update.message.reply_text(
        "✅ Оплата прошла! Генерирую твой персональный анализ... ⏳\n\n"
        "Это займёт 15-30 секунд."
    )

    analysis = await generate_analysis(birth_date, birth_time, birth_city)

    await update.message.reply_text(
        f"🔮 *ТВОЙ ГЛУБОКИЙ АСТРО-АНАЛИЗ*\n\n{analysis}",
        parse_mode="Markdown"
    )

async def generate_analysis(birth_date: str, birth_time: str, birth_city: str) -> str:
    try:
        prompt = f"""Ты профессиональный астролог-астрокартограф с 20-летним опытом. 
        
Данные клиента:
- Дата рождения: {birth_date}
- Время рождения: {birth_time}
- Место рождения: {birth_city}

Составь глубокий персональный анализ астрокарты для релокации. Структура:

1. **Общая характеристика** (3-4 предложения о натальной карте и предрасположенности к переездам)

2. **Топ-3 города для релокации** с подробным объяснением почему именно эти города подходят данному человеку (учитывай планетарные линии, укажи конкретные планеты и зоны)

3. **Карьера и финансы** — в каких географических направлениях искать возможности

4. **Любовь и отношения** — где высока вероятность встретить партнёра

5. **Здоровье и энергетика** — какие места дадут силу, а каких лучше избегать

6. **Главный совет** — одно ключевое послание для этого человека

Пиши тепло, лично, как будто говоришь с конкретным человеком. Используй конкретные названия городов из СНГ и мира. Длина: 400-500 слов."""

        message = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return "Произошла ошибка при генерации анализа. Пожалуйста, напиши нам — мы вернём Stars или пришлём анализ вручную."

async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.effective_message.web_app_data.data
    import json
    try:
        birth_data = json.loads(data)
        context.user_data["birth_data"] = birth_data
        await send_invoice(update, context)
    except Exception as e:
        logger.error(f"WebApp data error: {e}")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("analyze", analyze_command))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
