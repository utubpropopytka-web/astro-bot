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

import random
import string
import time

# Хранилище одноразовых кодов доступа: {code: {plan: int, used: bool, created: float}}
ACCESS_CODES = {}

def generate_access_code(plan: int) -> str:
    """Генерирует уникальный 8-символьный код доступа"""
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        if code not in ACCESS_CODES:
            ACCESS_CODES[code] = {
                'plan': plan,
                'used': False,
                'created': time.time()
            }
            return code

def get_plan_from_tribute_message(text: str):
    """Определяет тариф по тексту сообщения от Tribute. Возвращает int или str."""
    text_lower = text.lower()
    # Основные тарифы
    if 'полный доступ' in text_lower or 'карта' in text_lower or '1350' in text or '1 350' in text:
        return 'tariff_full'
    if 'старт' in text_lower or 'топ 10' in text_lower or '630' in text:
        return 'tariff_start'
    # Гайды по городам
    if '5 город' in text_lower or '3000' in text or '3 000' in text:
        return 5
    if '3 город' in text_lower or '1300' in text or '1 300' in text:
        return 3
    if '1 город' in text_lower or '700' in text:
        return 1
    return 1

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://astrocartography-blond.vercel.app")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://worker-production-8acc.up.railway.app")
PORT = int(os.environ.get("PORT", 8080))

# Trybit
TRYBIT_API_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1dWlkIjoiTVRBM05UTTMiLCJ0eXBlIjoicHJvamVjdCIsInYiOiI2ODVmYjAzZDY1YWJhMDFhODc2NDZmOGNmZmE2NGEzNjQxZjhjN2FlMjBkMTQ0YjQ1MmQ5NzdjZDE1M2ExNjBiIiwiZXhwIjo4ODE4MjU4NTA3NH0.sDwkgK1-kMytjDrg_5jAG2c5C4audQpIb5MuxqY_E_g"
TRYBIT_SHOP_ID = "cT6EdNLgP50txyfc"

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
    keyboard = [[KeyboardButton("✦ RELO", web_app=WebAppInfo(url=WEBAPP_URL))]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "✦ *RELO*\n\n"
        "💬 Узнай лучшие места на Земле для жизни, карьеры и любви по дате рождения — твой ТОП 10 городов\n\n"
        "💬 Полный гайд переезда в другую страну — визы, необходимые документы, ограничения и возможности\n\n"
        "💬 15 ответов на самые важные вопросы по дате рождения — личный прогноз\n\n"
        "Нажми кнопку ниже 👇",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает все входящие сообщения — пересланные от Tribute и коды"""
    msg = update.message
    if not msg:
        return

    text = msg.text or msg.caption or ''

    # Проверяем — это пересланное сообщение от @tribute?
    is_from_tribute = False
    forward_origin = getattr(msg, 'forward_origin', None)
    forward_from = getattr(msg, 'forward_from', None)

    if forward_origin:
        # Новый формат forward (Telegram Bot API 7+)
        sender = getattr(forward_origin, 'sender_user', None)
        if sender and getattr(sender, 'username', '').lower() == 'tribute':
            is_from_tribute = True
        chat = getattr(forward_origin, 'chat', None)
        if chat and getattr(chat, 'username', '').lower() == 'tribute':
            is_from_tribute = True
    elif forward_from:
        # Старый формат
        if getattr(forward_from, 'username', '').lower() == 'tribute':
            is_from_tribute = True

    # Также проверяем forward_from_chat
    forward_from_chat = getattr(msg, 'forward_from_chat', None)
    if forward_from_chat and getattr(forward_from_chat, 'username', '').lower() == 'tribute':
        is_from_tribute = True

    if is_from_tribute:
        plan = get_plan_from_tribute_message(text)
        user_id = str(msg.from_user.id)

        # Сохраняем в PENDING_PAYMENTS
        PENDING_PAYMENTS[user_id] = {'plan': plan, 'paid': True, 'created': time.time()}

        if plan == 'tariff_full':
            plan_label = 'Полный доступ — Карта + Профиль'
        elif plan == 'tariff_start':
            plan_label = 'Старт — ТОП 10 городов'
        else:
            plan_label = {1: '1 город', 3: '3 города', 5: '5 городов'}.get(plan, str(plan))

        await msg.reply_text(
            f"✅ *Оплата подтверждена!*\n\n"
            f"Тариф: *{plan_label}*\n\n"
            f"Вернитесь в приложение RELO — доступ уже открыт! 🚀",
            parse_mode="Markdown"
        )
        logger.info(f"Payment confirmed for user {user_id}, plan {plan}")
        return

    # Проверка кода доступа — если пользователь просто написал код
    stripped = text.strip().upper()
    if len(stripped) == 8 and stripped.isalnum() and stripped in ACCESS_CODES:
        entry = ACCESS_CODES[stripped]
        if entry['used']:
            await msg.reply_text("❌ Этот код уже был использован.")
        else:
            plan = entry['plan']
            plan_label = {1: '1 город', 3: '3 города', 5: '5 городов'}.get(plan, str(plan))
            await msg.reply_text(
                f"✅ Код действителен! Тариф: *{plan_label}*\n\n"
                f"Введи его в приложении RELO во вкладке *Тарифы → Промокод*",
                parse_mode="Markdown"
            )
        return

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

        prompt = f"""Ты профессиональный астролог с 20-летним опытом, специалист по натальным картам и психологии личности.

Данные клиента:
- Дата рождения: {birth_date}
- Время рождения: {birth_time}
- Место рождения: {birth_city}
- Положение планет по знакам зодиака: {planets}

Составь глубокий персональный психологический портрет. Ответь СТРОГО валидным JSON и больше ничем — без markdown, без ```, без пояснений до или после. Схема:

{{
  "headline": "1 яркое предложение — самая суть этого человека",
  "sections": [
    {{"icon": "☀️", "title": "Общий портрет", "planet": "Солнце", "text": "3-4 предложения — стержень характера через Солнце"}},
    {{"icon": "🌙", "title": "Внутренний мир", "planet": "Луна", "text": "2-3 предложения — как чувствует, что даёт опору"}},
    {{"icon": "💫", "title": "Любовь и отношения", "planet": "Венера", "text": "2-3 предложения — как проявляет себя в любви"}},
    {{"icon": "⚡", "title": "Энергия и действия", "planet": "Марс", "text": "2-3 предложения — воля, инициатива, решения"}},
    {{"icon": "🌟", "title": "Рост и потенциал", "planet": "Юпитер", "text": "2-3 предложения — удача и широта взглядов"}},
    {{"icon": "🪐", "title": "Уроки жизни", "planet": "Сатурн", "text": "2-3 предложения — сложности и зоны роста"}},
    {{"icon": "✦", "title": "Главный совет", "planet": "", "text": "1-2 предложения — ключевое послание для этого человека"}}
  ]
}}

Пиши тепло, на «ты». Только JSON, без markdown."""

        message = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )

        import re as re_mod
        raw = message.content[0].text.strip()
        # strip markdown code fences if present
        cleaned = re_mod.sub(r'```(?:json)?\s*|\s*```', '', raw).strip()
        analysis_json = None
        try:
            analysis_json = json.loads(cleaned)
        except Exception:
            match = re_mod.search(r'\{[\s\S]*\}', cleaned)
            if match:
                try:
                    analysis_json = json.loads(match.group(0))
                except Exception:
                    pass

        if analysis_json:
            logger.info("Analysis JSON generated successfully")
            return web.json_response({'analysis': analysis_json})
        else:
            logger.warning("Analysis JSON parse failed, returning raw")
            return web.json_response({'analysis': raw})

    except Exception as e:
        logger.error(f"Analysis error: {e}")
        return web.json_response({'error': str(e)}, status=500)



async def handle_relocate(request):
    try:
        data = await request.json()
        from_country = data.get('from_country', 'России') or 'России'
        to_country = data.get('to_country', '') or 'выбранной страны'
        to_city = data.get('to_city', '') or ''

        prompt = f"""Ты опытный иммиграционный консультант, который умеет объяснять сложные вещи просто и живо — без канцелярита, без сухих формулировок из методичек.

Гражданин страны: {from_country}
Хочет переехать жить в город: {to_city}, страна: {to_country}

Сначала используй веб‑поиск, чтобы найти актуальную информацию о визовых требованиях между этими странами.

Когда соберёшь информацию, ответь СТРОГО валидным JSON и больше ничем — без markdown, без ```, без пояснений до или после. Строго по этой схеме:

{{
  "visa_status": "1-2 предложения: нужна ли гражданину {from_country} виза для краткосрочного въезда в {to_country}, и на какой срок можно находиться без визы, если безвизовый режим есть. Начни с прямого, конкретного ответа.",
  "pathways": [
    {{"title": "короткое название способа", "description": "1-2 предложения о том, как это работает именно для этой пары стран — конкретно, живо, как будто объясняешь другу за чашкой кофе, а не зачитываешь регламент"}}
  ],
  "documents": ["документ 1", "документ 2", "документ 3"],
  "next_steps": "1-2 предложения о том, куда обращаться: посольство/консульство, официальный портал",
  "caveat": "если что-то неточно или часто меняется — упомяни здесь, иначе пустая строка"
}}

В pathways — от 2 до 4 реалистичных легальных способов переехать жить именно в {to_country} (рабочая виза, виза для фрилансеров/цифровых кочевников если она существует, учебная виза, воссоединение семьи, виза предпринимателя/инвестора) — указывай только то, что реально существует для этой пары стран. В documents — документы для самого реалистичного из путей.

Пиши конкретно и по делу, но не сухо — избегай канцелярита и общих фраз. Каждое предложение должно содержать факт, а не воду. Ответь только JSON-объектом, никакого текста до или после."""

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

        prompt = f"""Ты профессиональный астролог, но пишешь не как учебник, а как человек, который умеет рассказывать так, что от текста невозможно оторваться. Твоя цель — не просто дать информацию, а зацепить, удивить, заставить кивнуть и подумать «да, это правда про меня».

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
  "headline": "1-2 предложения — яркий, конкретный, неожиданный вывод. Не общая фраза, а что-то, от чего человек удивится точности. Используй живой образ или метафору, если уместно.",
  "planet_key": "название главной планеты, которая отвечает за эту тему (одно слово)",
  "planet_sign": "знак зодиака этой планеты из натальной карты клиента",
  "planet_color": "hex-цвет для подсветки (подбери под стихию знака: огонь=#e8b88a, земля=#8ab89a, воздух=#b8c8d4, вода=#b8a8d4)",
  "sections": [
    {{"title": "Астрологическая основа", "text": "2-3 предложения: объясни конкретно, через живые образы и сравнения, а не сухие термины. Покажи, а не просто расскажи."}},
    {{"title": "Как это проявляется", "text": "2-3 предложения: конкретные жизненные ситуации, узнаваемые сцены — чтобы человек воскликнул 'точно, у меня так и было'."}},
    {{"title": "Практический совет", "text": "2-3 предложения: конкретное, действенное, написанное с энергией — не "постарайся", а "сделай так-то"."}}
  ]
}}

Пиши на «ты», живо, с характером — как будто говоришь с человеком лично, а не зачитываешь справку. Избегай канцелярита и общих фраз вроде "это важная часть твоей личности". Каждое предложение должно нести конкретику, а не воду. Только JSON."""

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

# Хранилище ожидаемых оплат: {user_id: {plan, paid, created}}
PENDING_PAYMENTS = {}

async def handle_health(request):
    return web.Response(text='OK')

async def handle_create_payment(request):
    """Создаёт платёж через Trybit API"""
    try:
        import aiohttp as aiohttp_lib
        data = await request.json()
        amount = data.get('amount')      # сумма в рублях
        plan = data.get('plan')          # план: 1,3,5,'tariff_start','tariff_full'
        user_id = str(data.get('user_id', 'anon'))
        order_id = f"{user_id}_{plan}_{int(time.time())}"

        payload = {
            "shop_id": TRYBIT_SHOP_ID,
            "amount": str(amount),
            "currency": "RUB",
            "order_id": order_id,
            "comment": f"RELO — план {plan}",
            "success_url": WEBAPP_URL,
            "fail_url": WEBAPP_URL,
        }

        async with aiohttp_lib.ClientSession() as session:
            async with session.post(
                "https://api.trybit.com/api/v1/payment/create",
                json=payload,
                headers={
                    "Authorization": f"Bearer {TRYBIT_API_KEY}",
                    "Content-Type": "application/json"
                }
            ) as resp:
                result = await resp.json()
                logger.info(f"Trybit create payment response: {result}")

        if result.get('status') == 'success' or result.get('data', {}).get('url'):
            pay_url = result.get('data', {}).get('url') or result.get('url')
            pay_id = result.get('data', {}).get('id') or result.get('id') or order_id
            # Сохраняем ожидание
            PENDING_PAYMENTS[order_id] = {
                'plan': plan,
                'user_id': user_id,
                'paid': False,
                'created': time.time()
            }
            return web.json_response({'ok': True, 'url': pay_url, 'order_id': order_id})
        else:
            logger.error(f"Trybit error: {result}")
            return web.json_response({'ok': False, 'error': str(result)}, status=500)

    except Exception as e:
        logger.error(f"Create payment error: {e}")
        return web.json_response({'ok': False, 'error': str(e)}, status=500)

async def handle_trybit_webhook(request):
    """Получает уведомление от Trybit об успешной оплате"""
    try:
        data = await request.json()
        logger.info(f"Trybit webhook: {data}")

        status = data.get('status') or data.get('payment_status', '')
        order_id = str(data.get('order_id', ''))

        if status in ('success', 'paid', 'completed', 'PAID', 'SUCCESS'):
            # Находим ожидание по order_id
            if order_id in PENDING_PAYMENTS:
                entry = PENDING_PAYMENTS[order_id]
                entry['paid'] = True
                user_id = entry['user_id']
                plan = entry['plan']
                # Также сохраняем по user_id для polling
                PENDING_PAYMENTS[user_id] = {'plan': plan, 'paid': True, 'created': time.time()}
                logger.info(f"Payment confirmed: order={order_id} user={user_id} plan={plan}")
            else:
                # order_id = user_id_plan_ts — парсим user_id
                parts = order_id.split('_')
                if len(parts) >= 2:
                    user_id = parts[0]
                    plan = parts[1] if len(parts) > 1 else 1
                    try: plan = int(plan)
                    except: pass
                    PENDING_PAYMENTS[user_id] = {'plan': plan, 'paid': True, 'created': time.time()}
                    logger.info(f"Payment confirmed (no entry): user={user_id} plan={plan}")

        return web.Response(text='OK')
    except Exception as e:
        logger.error(f"Trybit webhook error: {e}")
        return web.Response(text='OK')

async def handle_await_payment(request):
    """Регистрирует ожидание оплаты от пользователя"""
    try:
        data = await request.json()
        user_id = str(data.get('user_id', ''))
        plan = int(data.get('plan', 1))
        if user_id:
            PENDING_PAYMENTS[user_id] = {'plan': plan, 'paid': False, 'created': time.time()}
        return web.json_response({'ok': True})
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)

async def handle_check_payment(request):
    """Проверяет статус оплаты для пользователя"""
    try:
        data = await request.json()
        user_id = str(data.get('user_id', ''))
        entry = PENDING_PAYMENTS.get(user_id)
        if entry and entry.get('paid'):
            # Сбрасываем флаг после выдачи
            entry['paid'] = False
            return web.json_response({'paid': True, 'plan': entry['plan']})
        return web.json_response({'paid': False})
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)

async def handle_verify_code(request):
    """Проверяет код доступа из mini app и помечает как использованный"""
    try:
        data = await request.json()
        code = data.get('code', '').strip().upper()
        if not code:
            return web.json_response({'valid': False, 'error': 'no_code'})

        if code not in ACCESS_CODES:
            # Также проверяем статичные промокоды
            if code == 'USTIK':
                return web.json_response({'valid': True, 'plan': 99, 'promo': True})
            return web.json_response({'valid': False, 'error': 'not_found'})

        entry = ACCESS_CODES[code]
        if entry['used']:
            return web.json_response({'valid': False, 'error': 'already_used'})

        # Помечаем как использованный
        entry['used'] = True
        plan = entry['plan']
        logger.info(f"Code {code} verified for plan {plan}")
        return web.json_response({'valid': True, 'plan': plan})

    except Exception as e:
        logger.error(f"Verify code error: {e}")
        return web.json_response({'valid': False, 'error': str(e)}, status=500)

def main():
    import asyncio

    async def run_all():
        global tg_app

        tg_app = Application.builder().token(BOT_TOKEN).build()
        tg_app.add_handler(CommandHandler("start", start))
        from telegram.ext import MessageHandler, filters
        tg_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

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
        app.router.add_post('/verify_code', handle_verify_code)
        app.router.add_options('/verify_code', handle_verify_code)
        app.router.add_post('/await_payment', handle_await_payment)
        app.router.add_options('/await_payment', handle_await_payment)
        app.router.add_post('/check_payment', handle_check_payment)
        app.router.add_options('/check_payment', handle_check_payment)
        app.router.add_post('/create_payment', handle_create_payment)
        app.router.add_options('/create_payment', handle_create_payment)
        app.router.add_post('/trybit_webhook', handle_trybit_webhook)
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
