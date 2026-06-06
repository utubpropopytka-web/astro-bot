import os
import logging
import anthropic
from aiohttp import web
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes
 
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
 
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://astrocartography-blond.vercel.app")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://worker-production-8acc.up.railway.app")
PORT = int(os.environ.get("PORT", 8080))
 
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
tg_app = None
 
CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
}
 
@web.middleware
async def cors_middleware(request, handler):
    if request.method == 'OPTIONS':
        return web.Response(status=200, headers=CORS_HEADERS)
    try:
        response = await handler(request)
    except Exception as e:
        response = web.json_response({'error': str(e)}, status=500)
    for key, val in CORS_HEADERS.items():
        response.headers[key] = val
    return response
 
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[KeyboardButton("🌍 Открыть Астрокартографию", web_app=WebAppInfo(url=WEBAPP_URL))]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "✦ *АСТРОКАРТОГРАФИЯ*\n\nУзнай лучшие места на Земле для жизни, карьеры и любви по дате рождения.\n\nНажми кнопку ниже 👇",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
 
async def handle_webhook(request):
    try:
        data = await request.json()
        update = Update.de_json(data, tg_app.bot)
        await tg_app.process_update(update)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    return web.Response(text='OK')
 
async def handle_analyze(request):
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
 
2. Топ-3 города для релокации — с подробным объяснением почему именно эти города. Укажи конкретные планеты и зоны (AC/MC/DC/IC). Выбирай лучшие города мира — Европа, США, Азия. Города России только если реально подходят по астрологии.
 
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
        logger.info("Analysis generated successfully")
        return web.json_response({'analysis': analysis})
 
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        return web.json_response({'error': str(e)}, status=500)
 
async def handle_health(request):
    return web.Response(text='OK')
 
def main():
    import asyncio
 
    async def run_all():
        global tg_app
 
        tg_app = Application.builder().token(BOT_TOKEN).build()
        tg_app.add_handler(CommandHandler("start", start))
 
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
        logger.info(f"Webhook set to {WEBHOOK_URL}/webhook")
 
        app = web.Application(middlewares=[cors_middleware])
        app.router.add_get('/health', handle_health)
        app.router.add_post('/webhook', handle_webhook)
        app.router.add_post('/analyze', handle_analyze)
        app.router.add_options('/analyze', handle_analyze)
        app.router.add_options('/{path_info:.*}', handle_analyze)
 
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        logger.info(f"Web server started on port {PORT}")
        logger.info("Bot started!")
 
        try:
            await asyncio.Event().wait()
        finally:
            await tg_app.stop()
            await tg_app.shutdown()
 
    asyncio.run(run_all())
 
if __name__ == "__main__":
    main()
