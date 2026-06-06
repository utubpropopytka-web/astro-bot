import os
import logging
import json
import anthropic
from aiohttp import web
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, PreCheckoutQueryHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://astrocartography-blond.vercel.app")
PORT = int(os.environ.get("PORT", 8080))

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[KeyboardButton("🌍 Открыть Астрокартографию", web_app=WebAppInfo(url=WEBAPP_URL))]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "✦ *АСТРОКАРТОГРАФИЯ*\n\nУзнай лучшие места на Земле для жизни, карьеры и любви по дате рождения.\n\nНажми кнопку ниже 👇",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def handle_analyze(request):
    # CORS headers
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
    }
    
    if request.method == 'OPTIONS':
        return web.Response(status=200, headers=headers)
    
    try:
        data = await request.json()
        birth_date = data.get('date', 'неизвестна')
        birth_time = data.get('time', 'неизвестно')
        birth_city = data.get('city', 'неизвестен')
        planets = data.get('planets', '')
        
        prompt = f"""Ты профессиональный астролог-астрокартограф с 20-летним опытом.

Данные клиента:
- Дата рождения: {birth_date}
- Время рождения: {birth_time}
- Место рождения: {birth_city}
- Планетарные линии: {planets}

Составь глубокий персональный анализ астрокарты для релокации. Структура:

1. Общая характеристика (3-4 предложения о натальной карте)

2. Топ-3 города для релокации — с подробным объяснением почему именно эти города. Укажи конкретные планеты и зоны (AC/MC/DC/IC). Выбирай из городов СНГ и мира.

3. Карьера и финансы — в каких географических направлениях искать возможности

4. Любовь и отношения — где высока вероятность встретить партнёра

5. Здоровье и энергетика — какие места дадут силу

6. Главный совет — одно ключевое послание

Пиши тепло и лично. Обращайся на «ты». Длина: 400-500 слов."""

        message = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        
        analysis = message.content[0].text
        return web.json_response({'analysis': analysis}, headers=headers)
        
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        return web.json_response({'error': str(e)}, status=500, headers=headers)

async def handle_health(request):
    return web.Response(text='OK')

async def run_web_server():
    app = web.Application()
    app.router.add_get('/health', handle_health)
    app.router.add_post('/analyze', handle_analyze)
    app.router.add_options('/analyze', handle_analyze)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")

def main():
    import asyncio
    
    async def run_all():
        await run_web_server()
        
        tg_app = Application.builder().token(BOT_TOKEN).build()
        tg_app.add_handler(CommandHandler("start", start))
        
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Bot started!")
        
        try:
            await asyncio.Event().wait()
        finally:
            await tg_app.updater.stop()
            await tg_app.stop()
            await tg_app.shutdown()
    
    asyncio.run(run_all())

if __name__ == "__main__":
    main()
