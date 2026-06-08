import os
import re
import telebot
from telebot import types
from dotenv import load_dotenv
import aiohttp
import asyncio
from datetime import datetime, timedelta
import feedparser
from threading import Thread
import html

# Загружаем токен из .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Создаём бота
bot = telebot.TeleBot(BOT_TOKEN)

# ========== КЭШ ДЛЯ КУРСОВ ВАЛЮТ ==========
currency_cache = {}  # {'USD_BYN': (курс, время)}
CACHE_TTL = 3600  # 1 час

# ========== КЭШ ДЛЯ НОВОСТЕЙ ==========
news_cache = []
NEWS_CACHE_TTL = 600  # 10 минут
news_cache_time = None

# ========== RSS ИСТОЧНИКИ НОВОСТЕЙ О ВАЛЮТАХ ==========
RSS_SOURCES = {
    "investing_com": "https://www.investing.com/rss/news_25.rss",  # Валютные новости
    "dailyfx": "https://www.dailyfx.com/rss/news/",                 # Forex новости
    "fxstreet": "https://www.fxstreet.com/rss/feed/news",           # Финансовые новости
    "reuters_currencies": "https://www.reuters.com/markets/currencies/rss"
}

# Альтернативные источники (если какие-то не работают)
BACKUP_RSS_SOURCES = {
    "rbc_currency": "https://www.rbc.ru/rss/finances/currency/___/news.rss",
    "finmarket": "https://www.finmarket.ru/main/rss/news"
}

# ========== КЛАСС ДЛЯ РАБОТЫ С API ==========
class CurrencyConverter:
    """Конвертер валют с кэшированием"""
    
    async def get_rate(self, from_currency: str, to_currency: str):
        """Получить курс конвертации"""
        cache_key = f"{from_currency}_{to_currency}"
        
        # Проверяем кэш
        if cache_key in currency_cache:
            rate, timestamp = currency_cache[cache_key]
            if datetime.now() - timestamp < timedelta(seconds=CACHE_TTL):
                return rate
        
        # Запрос к API
        url = f"https://api.exchangerate-api.com/v4/latest/{from_currency}"
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        rate = data['rates'].get(to_currency.upper())
                        
                        if rate:
                            # Сохраняем в кэш
                            currency_cache[cache_key] = (rate, datetime.now())
                            return rate
                    return None
            except Exception as e:
                print(f"Ошибка API: {e}")
                return None
    
    async def convert(self, amount: float, from_cur: str, to_cur: str):
        """Конвертировать сумму"""
        rate = await self.get_rate(from_cur, to_cur)
        if rate:
            result = amount * rate
            return round(result, 2), round(rate, 4)
        return None, None

# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С НОВОСТЯМИ ==========
def fetch_currency_news():
    """Получение новостей о валютах из RSS-лент"""
    global news_cache, news_cache_time
    
    # Проверяем кэш
    if news_cache_time and (datetime.now() - news_cache_time).seconds < NEWS_CACHE_TTL:
        return news_cache
    
    news_list = []
    
    for source_name, rss_url in RSS_SOURCES.items():
        try:
            feed = feedparser.parse(rss_url)
            for entry in feed.entries[:5]:  # Берём по 5 новостей из источника
                # Очищаем HTML теги из описания
                description = html.unescape(entry.get('summary', ''))
                description = re.sub(r'<[^>]+>', '', description)
                description = description[:200] + "..." if len(description) > 200 else description
                
                news_item = {
                    'title': entry.get('title', 'Без заголовка'),
                    'description': description,
                    'link': entry.get('link', '#'),
                    'source': source_name,
                    'published': entry.get('published', 'Дата неизвестна')
                }
                news_list.append(news_item)
        except Exception as e:
            print(f"Ошибка при получении новостей из {source_name}: {e}")
            continue
    
    # Сортируем по дате (самые свежие первые)
    news_list = news_list[:15]  # Ограничиваем 15 новостями
    
    # Сохраняем в кэш
    news_cache = news_list
    news_cache_time = datetime.now()
    
    return news_list

def format_news_for_telegram(news_list, limit=5):
    """Форматирует новости для отправки в Telegram"""
    if not news_list:
        return "📭 *Нет свежих новостей* на данный момент.\nПопробуйте позже или проверьте другие источники."
    
    result = "📈 *СВЕЖИЕ НОВОСТИ О ВАЛЮТАХ* 📉\n\n"
    result += f"🕐 Обновлено: {datetime.now().strftime('%H:%M:%S')}\n"
    result += "━" * 20 + "\n\n"
    
    for i, news in enumerate(news_list[:limit], 1):
        # Добавляем эмодзи в зависимости от источника
        source_emoji = {
            "investing_com": "📊",
            "dailyfx": "💱",
            "fxstreet": "📰",
            "reuters_currencies": "🗞️"
        }.get(news['source'], "📌")
        
        result += f"{i}. {source_emoji} *{news['title']}*\n"
        if news['description']:
            result += f"   📝 {news['description']}\n"
        result += f"   🔗 [Читать полностью]({news['link']})\n"
        result += f"   🕒 {news['published'][:25]}\n\n"
    
    return result

# ========== КЛАВИАТУРЫ ==========
def get_main_keyboard():
    """Главная клавиатура с кнопками"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = [
        types.KeyboardButton("💱 Конвертировать"),
        types.KeyboardButton("📊 Курсы валют"),
        types.KeyboardButton("📰 Новости валют"),
        types.KeyboardButton("❓ Помощь")
    ]
    keyboard.add(*buttons)
    return keyboard

def get_currency_inline():
    """Inline-клавиатура с валютами"""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton("🇷🇺 RUB → BYN", callback_data="conv_RUB_BYN"),
        types.InlineKeyboardButton("🇺🇸 USD → BYN", callback_data="conv_USD_BYN"),
        types.InlineKeyboardButton("🇪🇺 EUR → BYN", callback_data="conv_EUR_BYN"),
        types.InlineKeyboardButton("🇬🇧 GBP → BYN", callback_data="conv_GBP_BYN"),
        types.InlineKeyboardButton("🇨🇳 CNY → BYN", callback_data="conv_CNY_BYN"),
        types.InlineKeyboardButton("🇯🇵 JPY → BYN", callback_data="conv_JPY_BYN"),
        types.InlineKeyboardButton("🇵🇱 PLN → BYN", callback_data="conv_PLN_BYN"),
        types.InlineKeyboardButton("🔄 BYN → RUB", callback_data="conv_BYN_RUB"),
        types.InlineKeyboardButton("💎 Другая валюта", callback_data="other_currency")
    ]
    keyboard.add(*buttons)
    return keyboard

def get_rates_inline():
    """Inline-клавиатура для курсов"""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton("🇷🇺 RUB/BYN", callback_data="rate_RUB_BYN"),
        types.InlineKeyboardButton("🇺🇸 USD/BYN", callback_data="rate_USD_BYN"),
        types.InlineKeyboardButton("🇪🇺 EUR/BYN", callback_data="rate_EUR_BYN"),
        types.InlineKeyboardButton("🇬🇧 GBP/BYN", callback_data="rate_GBP_BYN"),
        types.InlineKeyboardButton("🇨🇳 CNY/BYN", callback_data="rate_CNY_BYN"),
        types.InlineKeyboardButton("🇯🇵 JPY/BYN", callback_data="rate_JPY_BYN"),
        types.InlineKeyboardButton("🇵🇱 PLN/BYN", callback_data="rate_PLN_BYN"),
        types.InlineKeyboardButton("🔄 BYN/RUB", callback_data="rate_BYN_RUB"),
        types.InlineKeyboardButton("💶 EUR/USD", callback_data="rate_EUR_USD"),
        types.InlineKeyboardButton("💷 GBP/USD", callback_data="rate_GBP_USD"),
    ]
    keyboard.add(*buttons)
    return keyboard

def get_news_inline():
    """Inline-клавиатура для новостей"""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton("🔄 Обновить новости", callback_data="refresh_news"),
        types.InlineKeyboardButton("📰 Больше новостей", callback_data="more_news")
    ]
    keyboard.add(*buttons)
    return keyboard

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@bot.message_handler(commands=['start'])
def send_welcome(message):
    """Приветственное сообщение"""
    bot.send_message(
        message.chat.id,
        "✨ *Привет! Я бот-конвертер валют* ✨\n\n"
        "Я умею конвертировать валюты, особенно актуальные для Беларуси!\n\n"
        "🇷🇺 *Основные направления:*\n"
        "• Российский рубль → Белорусский рубль (RUB → BYN)\n"
        "• Белорусский рубль → Российский рубль (BYN → RUB)\n"
        "• Доллар, евро и другие валюты → BYN\n\n"
        "📰 *Новости:*\n"
        "• Свежие новости о курсах валют\n"
        "• Аналитика финансовых рынков\n\n"
        "📝 *Как использовать:*\n"
        "• Напиши `100 rub in byn`\n"
        "• Или `50 eur to byn`\n"
        "• Или `200 byn in rub`\n"
        "• Используй кнопки в меню\n\n"
        "💡 *Доступные валюты:*\n"
        "RUB, BYN, USD, EUR, GBP, CNY, JPY, PLN",
        parse_mode='Markdown',
        reply_markup=get_main_keyboard()
    )

@bot.message_handler(commands=['help'])
def send_help(message):
    """Справка"""
    help_text = """
ℹ️ *Как пользоваться ботом:*

*1. Конвертация валют:*
   `100 rub in byn` - российские в белорусские
   `50 byn in rub` - белорусские в российские
   `100 usd in byn` - доллары в белорусские
   `50 eur to byn` - евро в белорусские

*2. Курсы валют:*
   Нажми «📊 Курсы валют» → выбери пару

*3. Новости:*
   Нажми «📰 Новости валют» → получи свежие новости

*4. Кнопки:*
   💱 Конвертировать - выбор валют
   📊 Курсы валют - просмотр курсов
   📰 Новости валют - финансовая аналитика

*Доступные валюты:*
🇷🇺 RUB - Российский рубль
🇧🇾 BYN - Белорусский рубль
🇺🇸 USD - Доллар США
🇪🇺 EUR - Евро
🇬🇧 GBP - Фунт стерлингов
🇨🇳 CNY - Китайский юань
🇯🇵 JPY - Японская иена
🇵🇱 PLN - Польский злотый
"""
    bot.send_message(message.chat.id, help_text, parse_mode='Markdown')

@bot.message_handler(commands=['news'])
def cmd_news(message):
    """Команда для получения новостей"""
    bot.send_chat_action(message.chat.id, 'typing')
    news_list = fetch_currency_news()
    news_text = format_news_for_telegram(news_list, limit=5)
    bot.send_message(
        message.chat.id, 
        news_text, 
        parse_mode='Markdown',
        disable_web_page_preview=True,
        reply_markup=get_news_inline()
    )

@bot.message_handler(func=lambda message: message.text == "💱 Конвертировать")
def convert_button(message):
    """Обработка кнопки конвертации"""
    bot.send_message(
        message.chat.id,
        "Выбери валютную пару для конвертации:",
        reply_markup=get_currency_inline()
    )

@bot.message_handler(func=lambda message: message.text == "📊 Курсы валют")
def rates_button(message):
    """Обработка кнопки курсов"""
    bot.send_message(
        message.chat.id,
        "Выбери валютную пару для просмотра курса:",
        reply_markup=get_rates_inline()
    )

@bot.message_handler(func=lambda message: message.text == "📰 Новости валют")
def news_button(message):
    """Обработка кнопки новостей"""
    bot.send_chat_action(message.chat.id, 'typing')
    news_list = fetch_currency_news()
    news_text = format_news_for_telegram(news_list, limit=5)
    bot.send_message(
        message.chat.id, 
        news_text, 
        parse_mode='Markdown',
        disable_web_page_preview=True,
        reply_markup=get_news_inline()
    )

@bot.message_handler(func=lambda message: message.text == "❓ Помощь")
def help_button(message):
    """Обработка кнопки помощи"""
    send_help(message)

# ========== ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ (КОНВЕРТАЦИЯ) ==========
@bot.message_handler(func=lambda message: True)
def handle_text(message):
    """Обработка текстовых сообщений с конвертацией"""
    text = message.text.lower().strip()
    
    # Паттерн: "100 rub in byn" или "50 eur to byn" или "200 byn in rub"
    pattern = r'^(\d+(?:\.\d+)?)\s+([a-z]{3})\s+(?:in|to)\s+([a-z]{3})$'
    match = re.match(pattern, text)
    
    if match:
        amount = float(match.group(1))
        from_cur = match.group(2).upper()
        to_cur = match.group(3).upper()
        
        # Отправляем статус "печатает"
        bot.send_chat_action(message.chat.id, 'typing')
        
        # Конвертируем
        result, rate = run_async(converter.convert(amount, from_cur, to_cur))
        
        if result:
            # Добавляем эмодзи для валют
            currency_emojis = {
                'RUB': '🇷🇺', 'BYN': '🇧🇾', 'USD': '🇺🇸', 
                'EUR': '🇪🇺', 'GBP': '🇬🇧', 'CNY': '🇨🇳', 
                'JPY': '🇯🇵', 'PLN': '🇵🇱'
            }
            from_emoji = currency_emojis.get(from_cur, '💰')
            to_emoji = currency_emojis.get(to_cur, '💰')
            
            response = (
                f"✅ *Результат конвертации:*\n\n"
                f"{from_emoji} {amount:,.2f} {from_cur} = "
                f"{to_emoji} *{result:,.2f} {to_cur}*\n\n"
                f"📈 *Курс:* 1 {from_cur} = {rate} {to_cur}\n"
                f"🔄 1 {to_cur} = {round(1/rate, 4)} {from_cur}"
            )
            bot.send_message(message.chat.id, response, parse_mode='Markdown')
        else:
            bot.send_message(
                message.chat.id,
                f"❌ Не удалось получить курс {from_cur}/{to_cur}\n"
                "Проверь правильность валюты или попробуй позже"
            )
    else:
        # Если сообщение не похоже на конвертацию
        bot.send_message(
            message.chat.id,
            "🤔 Не понимаю формат.\n"
            "Напиши что-то вроде: `100 rub in byn`\n"
            "Или нажми /help",
            parse_mode='Markdown'
        )

# ========== ОБРАБОТКА INLINE-КНОПОК ==========
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """Обработка нажатий на inline-кнопки"""
    
    # Конвертация
    if call.data.startswith("conv_"):
        _, from_cur, to_cur = call.data.split("_")
        
        # Добавляем подсказку для популярной пары RUB/BYN
        hint = ""
        if from_cur == "RUB" and to_cur == "BYN":
            hint = "\n\n💡 *Подсказка:* Примерное соотношение 3.3 RUB = 1 BYN"
        elif from_cur == "BYN" and to_cur == "RUB":
            hint = "\n\n💡 *Подсказка:* Примерное соотношение 1 BYN = 3.3 RUB"
        
        # Запрашиваем сумму у пользователя
        msg = bot.send_message(
            call.message.chat.id,
            f"💱 Введи сумму в *{from_cur}* для конвертации в *{to_cur}*:{hint}",
            parse_mode='Markdown'
        )
        # Регистрируем следующий шаг
        bot.register_next_step_handler(msg, process_amount, from_cur, to_cur)
        bot.answer_callback_query(call.id)
    
    # Показ курса
    elif call.data.startswith("rate_"):
        _, from_cur, to_cur = call.data.split("_")
        
        bot.send_chat_action(call.message.chat.id, 'typing')
        rate = run_async(converter.get_rate(from_cur, to_cur))
        
        if rate:
            # Добавляем эмодзи
            currency_emojis = {
                'RUB': '🇷🇺', 'BYN': '🇧🇾', 'USD': '🇺🇸', 
                'EUR': '🇪🇺', 'GBP': '🇬🇧', 'CNY': '🇨🇳', 
                'JPY': '🇯🇵', 'PLN': '🇵🇱'
            }
            from_emoji = currency_emojis.get(from_cur, '💰')
            to_emoji = currency_emojis.get(to_cur, '💰')
            
            response = (
                f"📊 *Курс {from_emoji} {from_cur}/{to_cur} {to_emoji}*\n\n"
                f"1 {from_cur} = {rate} {to_cur}\n"
                f"1 {to_cur} = {round(1/rate, 4)} {from_cur}"
            )
            bot.send_message(call.message.chat.id, response, parse_mode='Markdown')
        else:
            bot.send_message(
                call.message.chat.id,
                f"❌ Не удалось получить курс {from_cur}/{to_cur}"
            )
        bot.answer_callback_query(call.id)
    
    # Новости
    elif call.data == "refresh_news":
        # Очищаем кэш новостей и получаем свежие
        global news_cache_time
        news_cache_time = None
        bot.send_chat_action(call.message.chat.id, 'typing')
        news_list = fetch_currency_news()
        news_text = format_news_for_telegram(news_list, limit=5)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=news_text,
            parse_mode='Markdown',
            disable_web_page_preview=True,
            reply_markup=get_news_inline()
        )
        bot.answer_callback_query(call.id, "✅ Новости обновлены!")
    
    elif call.data == "more_news":
        bot.send_chat_action(call.message.chat.id, 'typing')
        news_list = fetch_currency_news()
        news_text = format_news_for_telegram(news_list, limit=10)  # Показываем больше новостей
        bot.send_message(
            call.message.chat.id,
            news_text,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        bot.answer_callback_query(call.id)
    
    # Другая валюта
    elif call.data == "other_currency":
        msg = bot.send_message(
            call.message.chat.id,
            "💰 Введи конвертацию в формате:\n\n"
            "`100 rub in byn` - рубли РФ → белорусские\n"
            "`50 byn in rub` - белорусские → рубли РФ\n"
            "`100 usd in byn` - доллары → белорусские\n"
            "`50 eur to byn` - евро → белорусские\n\n"
            "Доступные валюты: RUB, BYN, USD, EUR, GBP, CNY, JPY, PLN",
            parse_mode='Markdown'
        )
        bot.answer_callback_query(call.id)

def process_amount(message, from_cur, to_cur):
    """Обработка введённой суммы после выбора валют"""
    try:
        amount = float(message.text.replace(',', '.'))
        
        bot.send_chat_action(message.chat.id, 'typing')
        result, rate = run_async(converter.convert(amount, from_cur, to_cur))
        
        if result:
            currency_emojis = {
                'RUB': '🇷🇺', 'BYN': '🇧🇾', 'USD': '🇺🇸', 
                'EUR': '🇪🇺', 'GBP': '🇬🇧', 'CNY': '🇨🇳', 
                'JPY': '🇯🇵', 'PLN': '🇵🇱'
            }
            from_emoji = currency_emojis.get(from_cur, '💰')
            to_emoji = currency_emojis.get(to_cur, '💰')
            
            response = (
                f"✅ *Результат конвертации:*\n\n"
                f"{from_emoji} {amount:,.2f} {from_cur} = "
                f"{to_emoji} *{result:,.2f} {to_cur}*\n\n"
                f"📈 *Курс:* 1 {from_cur} = {rate} {to_cur}\n"
                f"🔄 1 {to_cur} = {round(1/rate, 4)} {from_cur}"
            )
            bot.send_message(message.chat.id, response, parse_mode='Markdown')
        else:
            bot.send_message(
                message.chat.id,
                f"❌ Не удалось получить курс {from_cur}/{to_cur}\n"
                "Попробуй позже или выбери другую пару"
            )
    except ValueError:
        bot.send_message(message.chat.id, "❌ Ошибка! Введи число (например: 100 или 15.50)")

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def run_async(coro):
    """Запускает асинхронную функцию в синхронном коде telebot"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)

# Создаём экземпляр конвертера
converter = CurrencyConverter()

# ========== ЗАПУСК БОТА ==========
if __name__ == "__main__":
    print("🤖 Бот-конвертер валют с новостями запущен!")
    print("📰 RSS-источники новостей подключены")
    print("Нажми Ctrl+C для остановки")
    bot.infinity_polling()