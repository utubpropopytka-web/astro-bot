import os
import re
import json
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
import anthropic
from aiohttp import web
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import random
import string
import time

# Хранилище одноразовых кодов доступа: {code: {plan: int, used: bool, created: float}}
ACCESS_CODES = {}

# Одноразовые промокоды на полный доступ: {code: использован?}
ONE_TIME_PROMOS = {
    'RELO1': False,
    'RELO2': False,
    'RELO3': False,
    'RELO4': False,
    'RELO5': False,
    'RELO6': False,
    'RELO7': False,
    'RELO8': False,
    'RELO9': False,
    'RELO10': False,
    'RELO11': False,
}

# Файл, куда сохраняется состояние "использован/нет" — чтобы промокоды не
# обнулялись обратно в неиспользованные при каждом перезапуске бота.
# ВАЖНО: это спасает от обычных перезапусков/падений процесса, но НЕ спасает
# от полного передеплоя на Railway, если там не подключён постоянный volume —
# в таком случае файловая система пересоздаётся заново вместе с кодом.
PROMO_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'promo_state.json')

def load_promo_state():
    try:
        with open(PROMO_STATE_FILE, 'r') as f:
            saved = json.load(f)
        for code in ONE_TIME_PROMOS:
            if code in saved:
                ONE_TIME_PROMOS[code] = bool(saved[code])
        logger.info(f"Promo state loaded: {ONE_TIME_PROMOS}")
    except FileNotFoundError:
        logger.info("No promo state file yet — starting fresh")
    except Exception as e:
        logger.error(f"Failed to load promo state: {e}")

def save_promo_state():
    try:
        with open(PROMO_STATE_FILE, 'w') as f:
            json.dump(ONE_TIME_PROMOS, f)
    except Exception as e:
        logger.error(f"Failed to save promo state: {e}")

load_promo_state()

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
    if '5 город' in text_lower or '3350' in text or '3 350' in text:
        return 5
    if '3 город' in text_lower or '980' in text:
        return 3
    if '1 город' in text_lower or '520' in text:
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

# Пул потоков для вызовов Anthropic API — чтобы синхронный клиент не блокировал
# весь event loop сервера (иначе один "тяжёлый" запрос замораживает вообще всё,
# включая параллельные запросы других пользователей и вебхук Telegram).
_ANTHROPIC_EXECUTOR = ThreadPoolExecutor(max_workers=16)

# Актуальные цены Anthropic API, $ за 1 млн токенов (вход, выход).
# Если цены изменятся — поправить здесь, и расчёт стоимости в логах будет точным.
MODEL_PRICING = {
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
}

async def create_message_async(_tag="unknown", **kwargs):
    """Неблокирующая обёртка над anthropic_client.messages.create —
    выполняет вызов в отдельном потоке, не замораживая сервер.
    Логирует РЕАЛЬНОЕ число токенов и точную стоимость запроса (не оценку)."""
    loop = asyncio.get_event_loop()
    message = await loop.run_in_executor(
        _ANTHROPIC_EXECUTOR,
        lambda: anthropic_client.messages.create(**kwargs)
    )
    try:
        usage = message.usage
        model = kwargs.get("model", "")
        in_price, out_price = MODEL_PRICING.get(model, (0.0, 0.0))
        cost = (usage.input_tokens / 1_000_000 * in_price) + (usage.output_tokens / 1_000_000 * out_price)
        logger.info(
            f"[USAGE] tag={_tag} model={model} in={usage.input_tokens} out={usage.output_tokens} cost=${cost:.5f}"
        )
    except Exception as e:
        logger.error(f"Usage logging failed: {e}")
    return message

# Глобальный кэш гайдов по переезду (страна→город), на всех пользователей сразу.
# Гайд не зависит от даты рождения/гороскопа — только от пары стран и города,
# поэтому не имеет смысла генерировать его заново для каждого человека отдельно.
# Обновляется раз в сутки (visa_status/documents могут со временем меняться).
_RELOC_CACHE = {}
_RELOC_LOCKS = {}

def _reloc_cache_key(from_country, to_country, to_city):
    return (from_country or '').strip().lower() + '|' + (to_country or '').strip().lower() + '|' + (to_city or '').strip().lower()
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
    inline_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть RELO", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await update.message.reply_text(
        "✦ *RELO*\n\n"
        "💬 Узнай лучшие места на Земле для жизни, карьеры и любви по дате рождения — твой ТОП 10 городов\n\n"
        "💬 Полный гайд переезда в другую страну — визы, необходимые документы, ограничения и возможности\n\n"
        "💬 15 ответов на самые важные вопросы по дате рождения — личный прогноз",
        parse_mode="Markdown",
        reply_markup=inline_markup
    )

# Telegram ID администратора — только этот аккаунт может выдавать бесплатный
# доступ через команду /grant. Взято из ID, который назвал владелец бота.
ADMIN_ID = 8382319436

async def handle_grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ-команда: /grant <telegram_id> — генерирует одноразовый код полного
    доступа и сразу отправляет его указанному пользователю в личные сообщения."""
    if update.effective_user.id != ADMIN_ID:
        return  # молча игнорируем — не палим, что команда вообще существует

    if not context.args:
        await update.message.reply_text(
            "Использование: /grant <telegram_id>\n\nID пользователя человек может взять в приложении RELO — на вкладке Профиль внизу есть его ID с кнопкой копирования."
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом. Пример: /grant 123456789")
        return

    code = generate_access_code(99)  # 99 = полный доступ ко всему

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                "🎁 Вам открыт полный бесплатный доступ к RELO!\n\n"
                f"Код: `{code}`\n\n"
                "Введите его в приложении в поле промокода (вкладка Тарифы) — откроются все города, все 15 ответов и прогнозы."
            ),
            parse_mode="Markdown"
        )
        await update.message.reply_text(f"✅ Код {code} сгенерирован и отправлен пользователю {target_id}.")
        logger.info(f"Admin granted access code {code} to user {target_id}")
    except Exception as e:
        # Частая причина: человек ещё не писал боту /start — Telegram не даёт написать первым
        await update.message.reply_text(
            f"⚠️ Код сгенерирован: `{code}`\n\n"
            f"Но отправить его пользователю {target_id} не удалось ({e}) — скорее всего, он ещё не запускал бота (/start). "
            f"Перешлите код ему вручную.",
            parse_mode="Markdown"
        )
        logger.error(f"Failed to DM grant code to {target_id}: {e}")

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

        message = await create_message_async(
            _tag="analyze",
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

        day_tag = time.strftime('%Y-%m-%d', time.gmtime())
        cache_key = _reloc_cache_key(from_country, to_country, to_city)

        cached = _RELOC_CACHE.get(cache_key)
        if cached and cached.get('day') == day_tag:
            logger.info(f"Relocation guide cache HIT: {cache_key}")
            return web.json_response({'guide': cached['guide']})

        # Лок на конкретную пару страна→город — если несколько человек одновременно
        # запросили один и тот же город впервые за день, генерируем только один раз,
        # остальные дожидаются результата и берут его из кэша.
        lock = _RELOC_LOCKS.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = _RELOC_CACHE.get(cache_key)
            if cached and cached.get('day') == day_tag:
                logger.info(f"Relocation guide cache HIT (post-lock): {cache_key}")
                return web.json_response({'guide': cached['guide']})

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

            message = await create_message_async(
                _tag="relocate",
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

            _RELOC_CACHE[cache_key] = {'day': day_tag, 'guide': guide}
            logger.info(f"Relocation guide generated and cached: {cache_key}")
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

        seed = data.get('seed', '')

        prompt = f"""Ты опытный астролог-консультант с 20-летней практикой. Твоя репутация построена на том, что ты говоришь правду по карте, а не льстишь клиенту. Дай персональный ответ на конкретный вопрос по натальной карте.

Данные клиента:
- Дата рождения: {birth_date}
- Время рождения: {birth_time}
- Место рождения: {birth_city}
- Планеты по знакам: {planets}
- Асцендент: {ascendant}

Вопрос: {question_title}
Суть: {question_hint}

ПРАВИЛА ПЕРСОНАЛЬНОСТИ И ЧЕСТНОСТИ:
1. КАЖДОЕ утверждение привязывай к конкретному положению из ЭТОЙ карты (планета + знак). Никаких фраз, которые подошли бы любому человеку («у тебя есть скрытый потенциал», «ты способен на многое») — это запрещено.
2. Учитывай СОЧЕТАНИЯ планет: как минимум один вывод строй на взаимодействии двух положений (например, конфликт между Марсом и Луной, или как Асцендент окрашивает Солнце). Именно сочетания делают ответ уникальным.
3. Будь честным: обязательно назови и теневую сторону — где эта конфигурация мешает, в чём человек сам себе вредит, какой ценой даются его сильные стороны. Без этого ответ выглядит лестью.
4. Приводи конкретику из реальной жизни: типичная ситуация, узнаваемое поведение, характерная ошибка (например «берёшься за три проекта и бросаешь на середине» вместо «тебе свойственна многозадачность»).
5. Не преувеличивай и не обещай («тебя ждёт успех» — запрещено). Вместо предсказаний — склонности и вероятные сценарии.
6. Если время рождения не указано или данных мало — не выдумывай Асцендент и дома, опирайся только на то, что есть.
7. Каждое поле "text" в sections — 2-3 КОРОТКИХ абзаца по 1-2 предложения. Абзацы разделяй последовательностью из двух символов: обратный слэш и n (перенос строки в JSON-строке).
8. КРИТИЧЕСКИ ВАЖНО: поле "text" в КАЖДОЙ секции ОБЯЗАНО содержать готовый развёрнутый текст. Пустые строки, многоточия или заглушки вместо текста — запрещены. Если хотя бы одна секция останется без текста — ответ считается браком.

ВАЖНО: Код уникальности сессии: {seed}. Даже если данные похожи на предыдущие запросы — пиши ИНАЧЕ: другими словами, другими примерами, другим порядком мыслей. Каждый ответ должен звучать свежо и по-новому.

Ответь СТРОГО валидным JSON и больше ничем — без markdown, без ```, без пояснений до или после. Схема:

{{
  "headline": "1-2 предложения — самый главный вывод, прямой и честный ответ на вопрос, с опорой на конкретное положение карты",
  "planet_key": "название главной планеты, которая отвечает за эту тему (одно слово)",
  "planet_sign": "знак зодиака этой планеты из натальной карты клиента",
  "planet_color": "hex-цвет для подсветки (подбери под стихию знака: огонь=#e8b88a, земля=#8ab89a, воздух=#b8c8d4, вода=#b8a8d4)",
  "sections": [
    {{"title": "Астрологическая основа", "text": "какие планеты и знаки формируют этот паттерн конкретно у этого человека, включая одно значимое сочетание двух положений"}},
    {{"title": "Как это проявляется", "text": "узнаваемые ситуации из реальной жизни — и сильная сторона, и теневая (где это мешает или какой ценой достаётся)"}},
    {{"title": "Практический совет", "text": "что конкретно делать с этим знанием прямо сейчас — действие, а не пожелание"}}
  ]
}}

Пиши на «ты», тепло, но прямо — как консультант, которому доверяют за честность. Только JSON."""

        message = await create_message_async(
            _tag="question",
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
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

async def handle_monthly(request):
    """Персональный астропрогноз на месяц по реальным транзитам"""
    try:
        data = await request.json()
        birth_date = data.get('date', '')
        birth_time = data.get('time', '')
        birth_city = data.get('city', '')
        natal = data.get('natal', '')
        transits = data.get('transits', '')
        aspects = data.get('aspects', '')
        month = data.get('month', '')
        ascendant = data.get('ascendant', '')

        prompt = f"""Ты опытный астролог-консультант с 20-летней практикой. Твоя репутация построена на честных прогнозах по реальным транзитам, а не на лести. Составь персональный прогноз на месяц.

Данные клиента:
- Дата рождения: {birth_date}
- Время рождения: {birth_time}
- Место рождения: {birth_city}
- Натальные планеты: {natal}
- Асцендент: {ascendant}

РЕАЛЬНЫЕ АСТРОНОМИЧЕСКИЕ ДАННЫЕ (вычислены точно, используй именно их):
- Месяц прогноза: {month}
- Положение транзитных планет сейчас: {transits}
- Точные аспекты транзитных планет к натальной карте клиента: {aspects}

ПРАВИЛА:
1. Строй прогноз ТОЛЬКО на перечисленных выше транзитах и аспектах — не выдумывай другие. Каждый вывод привязывай к конкретному аспекту (например «транзитный Сатурн в квадрате к твоему Солнцу — поэтому...»).
2. Никаких фраз, подходящих любому человеку. Всё — через ЕГО карту и ЕГО аспекты.
3. Будь честным: если аспект напряжённый (квадрат, оппозиция) — прямо скажи, где будет трудно и как это пройти. Если гармоничный (трин, секстиль) — где окно возможностей. Не обещай («тебя ждёт успех» — запрещено), говори о склонностях и вероятных сценариях.
4. Каждое поле "text" — 2-3 КОРОТКИХ абзаца по 1-2 предложения. Абзацы разделяй последовательностью из двух символов: обратный слэш и n (перенос строки в JSON-строке).
5. КРИТИЧЕСКИ ВАЖНО: каждое поле "text" ОБЯЗАНО содержать готовый текст прогноза. Пустые строки, многоточия или описания-заглушки вместо текста — запрещены. Если не заполнишь все секции текстом — ответ считается браком.

Ответь СТРОГО валидным JSON и больше ничем — без markdown, без ```, без пояснений. Схема:

{{
  "headline": "1-2 предложения — главная тема месяца для этого человека, по самому сильному аспекту",
  "sections": [
    {{"title": "Общий фон месяца", "text": "какие транзиты задают тон и что это значит именно для его карты"}},
    {{"title": "Карьера и деньги", "text": "по соответствующим транзитам — где возможности, где риски"}},
    {{"title": "Отношения", "text": "по Венере и Луне — что происходит в личной сфере"}},
    {{"title": "Энергия и самочувствие", "text": "по Марсу и Луне — уровень сил, когда беречь себя"}}
  ],
  "key_dates": [
    {{"date": "примерный период, например 5–10 числа", "text": "1 предложение — что за окно и что делать"}},
    {{"date": "...", "text": "..."}}
  ],
  "advice": "2-3 предложения — главный совет месяца, конкретное действие"
}}

В key_dates — 2-4 периода. Пиши на «ты», тепло, но прямо. Только JSON."""

        message = await create_message_async(
            _tag="monthly",
            model="claude-haiku-4-5-20251001",
            max_tokens=2400,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = message.content[0].text.strip()
        cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.MULTILINE).strip()
        forecast = None
        try:
            forecast = json.loads(cleaned)
        except Exception:
            match = re.search(r'\{.*\}', cleaned, re.DOTALL)
            if match:
                try:
                    forecast = json.loads(match.group(0))
                except Exception:
                    forecast = None

        if not forecast:
            logger.error(f"Monthly JSON parse failed, raw: {raw[:300]}")
            return web.json_response({'error': 'parse_failed'}, status=500)

        logger.info("Monthly forecast generated")
        return web.json_response({'forecast': forecast})

    except Exception as e:
        logger.error(f"Monthly error: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def handle_daily(request):
    """Точный персональный прогноз на сегодня по реальным транзитам"""
    try:
        data = await request.json()
        birth_date = data.get('date', '')
        birth_time = data.get('time', '')
        birth_city = data.get('city', '')
        natal = data.get('natal', '')
        transits = data.get('transits', '')
        aspects = data.get('aspects', '')
        today = data.get('today', '')
        ascendant = data.get('ascendant', '')

        prompt = f"""Ты опытный астролог-консультант. Составь точный, честный прогноз НА ОДИН ДЕНЬ — сегодня.

Данные клиента:
- Дата рождения: {birth_date}
- Время рождения: {birth_time}
- Место рождения: {birth_city}
- Натальные планеты: {natal}
- Асцендент: {ascendant}

РЕАЛЬНЫЕ АСТРОНОМИЧЕСКИЕ ДАННЫЕ НА СЕГОДНЯ (вычислены точно, используй именно их):
- Сегодняшняя дата: {today}
- Положение транзитных планет сегодня: {transits}
- Точные аспекты транзитных планет к натальной карте клиента: {aspects}

ПРАВИЛА:
1. Строй прогноз ТОЛЬКО на перечисленных транзитах и аспектах. Каждый вывод привязывай к конкретному аспекту.
2. Никаких фраз, подходящих любому. Всё — через ЕГО карту и сегодняшние аспекты к ней.
3. Честно: напряжённый аспект — скажи прямо, где сегодня будет трудно и как пройти. Гармоничный — какое окно открыто именно сегодня. Не обещай, говори о склонностях.
4. Максимум конкретики и практических советов: что сделать сегодня, что отложить, на что обратить внимание.
5. Каждое поле "text" — 2-3 КОРОТКИХ абзаца по 1-2 предложения. Абзацы разделяй последовательностью из двух символов: обратный слэш и n (перенос строки в JSON-строке).
6. КРИТИЧЕСКИ ВАЖНО: каждое поле "text" ОБЯЗАНО содержать готовый текст прогноза. Пустые строки или заглушки запрещены — все секции должны быть заполнены.

Ответь СТРОГО валидным JSON и больше ничем — без markdown, без ```. Схема:

{{
  "headline": "1-2 предложения — главная энергия сегодняшнего дня для этого человека, по самому сильному аспекту",
  "sections": [
    {{"title": "Фон дня", "text": "какие аспекты работают сегодня и что это значит для его карты"}},
    {{"title": "Что сегодня получится", "text": "конкретные дела и сферы, где сегодня открыто окно — с советом что сделать"}},
    {{"title": "Чего сегодня избегать", "text": "где напряжение, какие решения и разговоры лучше отложить"}}
  ],
  "advice": "2-3 предложения — главный совет на сегодня, конкретное действие"
}}

Пиши на «ты», тепло, но прямо. Только JSON."""

        message = await create_message_async(
            _tag="daily",
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = message.content[0].text.strip()
        cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.MULTILINE).strip()
        forecast = None
        try:
            forecast = json.loads(cleaned)
        except Exception:
            match = re.search(r'\{.*\}', cleaned, re.DOTALL)
            if match:
                try:
                    forecast = json.loads(match.group(0))
                except Exception:
                    forecast = None

        if not forecast:
            logger.error(f"Daily JSON parse failed, raw: {raw[:300]}")
            return web.json_response({'error': 'parse_failed'}, status=500)

        logger.info("Daily forecast generated")
        return web.json_response({'forecast': forecast})

    except Exception as e:
        logger.error(f"Daily error: {e}")
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

        # Одноразовые промокоды — сгорают после первого использования
        if code in ONE_TIME_PROMOS:
            if ONE_TIME_PROMOS[code]:
                return web.json_response({'valid': False, 'error': 'already_used'})
            ONE_TIME_PROMOS[code] = True
            save_promo_state()
            logger.info(f"One-time promo {code} activated")
            return web.json_response({'valid': True, 'plan': 99, 'promo': True})

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

async def handle_create_subscription(request):
    """Заглушка: оформление подписки (235 ₽/мес, автопродление).
    TODO: подключить создание рекуррентного платежа через новую платёжную систему
    (сохранение карты/токена, вебхук на регулярное списание). Пока возвращает
    ok:false — фронтенд покажет 'скоро будет доступно'."""
    try:
        data = await request.json()
        logger.info(f"create_subscription (stub): {data}")
        return web.json_response({'ok': False, 'error': 'not_implemented'})
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=500)

async def handle_check_subscription(request):
    """Заглушка: проверка активности подписки. TODO: подключить реальный статус
    от платёжной системы после интеграции."""
    try:
        data = await request.json()
        return web.json_response({'active': False})
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)

async def handle_cancel_subscription(request):
    """Заглушка: отмена автопродления подписки. TODO: вызывать отмену
    рекуррентного платежа у платёжной системы после интеграции."""
    try:
        data = await request.json()
        logger.info(f"cancel_subscription (stub): {data}")
        return web.json_response({'ok': True})
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=500)

def main():
    import asyncio

    async def run_all():
        global tg_app

        tg_app = Application.builder().token(BOT_TOKEN).build()
        tg_app.add_handler(CommandHandler("start", start))
        tg_app.add_handler(CommandHandler("grant", handle_grant))
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
        app.router.add_post('/monthly', handle_monthly)
        app.router.add_options('/monthly', handle_monthly)
        app.router.add_post('/daily', handle_daily)
        app.router.add_options('/daily', handle_daily)
        app.router.add_post('/verify_code', handle_verify_code)
        app.router.add_options('/verify_code', handle_verify_code)
        app.router.add_post('/await_payment', handle_await_payment)
        app.router.add_options('/await_payment', handle_await_payment)
        app.router.add_post('/check_payment', handle_check_payment)
        app.router.add_options('/check_payment', handle_check_payment)
        app.router.add_post('/create_payment', handle_create_payment)
        app.router.add_options('/create_payment', handle_create_payment)
        app.router.add_post('/trybit_webhook', handle_trybit_webhook)
        app.router.add_post('/create_subscription', handle_create_subscription)
        app.router.add_options('/create_subscription', handle_create_subscription)
        app.router.add_post('/check_subscription', handle_check_subscription)
        app.router.add_options('/check_subscription', handle_check_subscription)
        app.router.add_post('/cancel_subscription', handle_cancel_subscription)
        app.router.add_options('/cancel_subscription', handle_cancel_subscription)
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
