import os
import re
import json
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
        top_cities = data.get('top_cities', '')

        prompt = f"""Ты профессиональный астролог с 20-летним опытом, специалист по натальным картам и психологии личности.

Данные клиента:
- Дата рождения: {birth_date}
- Время рождения: {birth_time}
- Место рождения: {birth_city}
- Положение планет по знакам зодиака: {planets}

Составь глубокий персональный психологический портрет личности на основе этой натальной карты. Структура:

1. Общий портрет личности (3-4 предложения — стержень характера, через Солнце)

2. Внутренний мир и эмоции — как человек чувствует, что даёт ему опору и комфорт (через Луну)

3. Любовь и отношения — как человек проявляет себя в любви, чего ждёт от партнёра (через Венеру)

4. Энергия и действия — как человек принимает решения, проявляет волю и инициативу (через Марс)

5. Рост и потенциал — в чём раскрывается удача и широта взглядов (через Юпитер)

6. Уроки и дисциплина — какие сложности и зоны роста важно проработать (через Сатурн)

7. Главный совет — одно ключевое послание для этого человека

Пиши тепло и лично. Обращайся на «ты». Не упоминай города, переезд или релокацию — фокус только на личности, характере и внутреннем мире человека. Длина: 400-500 слов."""

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

async def handle_relocate(request):
    try:
        data = await request.json()
        from_country = data.get('from_country', 'России') or 'России'
        to_country = data.get('to_country', '') or 'выбранной страны'
        to_city = data.get('to_city', '') or ''

        prompt = f"""Ты опытный иммиграционный консультант. Собери ориентировочный гайд по переезду.

Гражданин страны: {from_country}
Хочет переехать жить в город: {to_city}, страна: {to_country}

Сначала используй веб‑поиск, чтобы найти актуальную информацию о визовых требованиях между этими странами.

Когда соберёшь информацию, ответь СТРОГО валидным JSON и больше ничем — без markdown, без ```, без пояснений до или после. Строго по этой схеме:

{{
  "visa_status": "1-2 предложения: нужна ли гражданину {from_country} виза для краткосрочного въезда в {to_country}, и на какой срок можно находиться без визы, если безвизовый режим есть",
  "pathways": [
    {{"title": "короткое название способа", "description": "1-2 предложения о том, как это работает именно для этой пары стран"}}
  ],
  "documents": ["документ 1", "документ 2", "документ 3"],
  "next_steps": "1-2 предложения о том, куда обращаться: посольство/консульство, официальный портал",
  "caveat": "если что-то неточно или часто меняется — упомяни здесь, иначе пустая строка"
}}

В pathways — от 2 до 4 реалистичных легальных способов переехать жить именно в {to_country} (рабочая виза, виза для фрилансеров/цифровых кочевников если она существует, учебная виза, воссоединение семьи, виза предпринимателя/инвестора) — указывай только то, что реально существует для этой пары стран. В documents — документы для самого реалистичного из путей.

Ответь только JSON-объектом, никакого текста до или после."""

        message = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2500,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )

        text_parts = [block.text for block in message.content if getattr(block, "type", None) == "text"]
        raw = "\n".join(text_parts).strip()
        cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.MULTILINE).strip()

        guide = None
        try:
            guide = json.loads(cleaned)
        except Exception:
            match = re.search(r'\{.*\}', cleaned, re.DOTALL)
            if match:
                try:
                    guide = json.loads(match.group(0))
                except Exception:
                    guide = None

        if not guide:
            logger.error(f"Relocation JSON parse failed, raw: {raw[:500]}")
            return web.json_response({'error': 'parse_failed'}, status=500)

        logger.info("Relocation guide generated successfully")
        return web.json_response({'guide': guide})

    except Exception as e:
        logger.error(f"Relocation error: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def handle_question(request):
    try:
        data = await request.json()
        question_title = data.get('question_title', '')
        question_hint = data.get('question_hint', '')
        birth_date = data.get('date', '')
        birth_time = data.get('time', '')
        birth_city = data.get('city', '')
        planets = data.get('planets', '')
        ascendant = data.get('ascendant', '')

        prompt = f"""Ты профессиональный астролог. Дай персональный ответ на конкретный вопрос по натальной карте.

Данные клиента:
- Дата рождения: {birth_date}
- Время рождения: {birth_time}
- Место рождения: {birth_city}
- Планеты по знакам: {planets}
- Асцендент: {ascendant}

Вопрос: {question_title}
Суть: {question_hint}

Ответь СТРОГО валидным JSON и больше ничем — без markdown, без ```, без пояснений до или после. Схема:

{{
  "headline": "1-2 предложения — самый главный вывод, прямой ответ на вопрос",
  "planet_key": "название главной планеты, которая отвечает за эту тему (одно слово)",
  "planet_sign": "знак зодиака этой планеты из натальной карты клиента",
  "planet_color": "hex-цвет для подсветки (подбери под стихию знака: огонь=#e8b88a, земля=#8ab89a, воздух=#b8c8d4, вода=#b8a8d4)",
  "sections": [
    {{"title": "Астрологическая основа", "text": "2-3 предложения: какие планеты и знаки формируют этот паттерн конкретно для этого человека"}},
    {{"title": "Как это проявляется", "text": "2-3 предложения: как это выражается в реальной жизни, в конкретных ситуациях"}},
    {{"title": "Практический совет", "text": "2-3 предложения: что конкретно делать с этим знанием прямо сейчас"}}
  ]
}}

Пиши на «ты», тепло и конкретно — про эту натальную карту, не общими словами. Только JSON."""

        message = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = message.content[0].text.strip()
        cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.MULTILINE).strip()
        answer = None
        try:
            answer = json.loads(cleaned)
        except Exception:
            match = re.search(r'\{.*\}', cleaned, re.DOTALL)
            if match:
                try:
                    answer = json.loads(match.group(0))
                except Exception:
                    answer = None

        if not answer:
            logger.error(f"Question JSON parse failed, raw: {raw[:300]}")
            return web.json_response({'error': 'parse_failed'}, status=500)

        logger.info(f"Question answer generated: {question_title}")
        return web.json_response({'answer': answer})

    except Exception as e:
        logger.error(f"Question error: {e}")
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
        app.router.add_post('/relocate', handle_relocate)
        app.router.add_options('/relocate', handle_relocate)
        app.router.add_post('/question', handle_question)
        app.router.add_options('/question', handle_question)
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
