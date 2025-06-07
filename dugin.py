import os
import re
import logging
import sqlite3
from datetime import datetime, timedelta
from collections import Counter

from telethon import TelegramClient, events
from telethon.tl.types import Message
import openai
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio

# ——————— CONFIGURATION ———————
# Direct credentials as provided
API_ID = 25534203
API_HASH = '5d4c95aab680f35d59a72381fa486650'
BOT_TOKEN = '8025194294:AAFLCgZb47b2b7fi2d6gpa7-YbaXupFDJRk'
TARGET_CHANNEL = '@duginneuro'
SOURCE_CHANNELS = ['@novosti_efir', '@toporlive']  # extend as needed
OPENAI_API_KEY = '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'
ERROR_NOTIFY_CHAT = 'https://t.me/+JirC1V_rgXo3N2Uy'
FETCH_INTERVAL_MINUTES = 60  # run every 1 hour
MENTION_THRESHOLD = 2       # minimum number of channel mentions

# ——————— SETUP ———————
openai.api_key = OPENAI_API_KEY
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')

# Initialize Telethon client
client = TelegramClient('session_name', API_ID, API_HASH)

# SQLite for history
DB_PATH = 'bot_history.db'
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute('''
    CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        news_text TEXT UNIQUE,
        generated_comment TEXT,
        published_at TIMESTAMP
    )
''')
conn.commit()

# Scheduler
scheduler = AsyncIOScheduler()

# ——————— TEXT CLEANING ———————
URL_PATTERN = re.compile(r'http\S+')
EMOJI_PATTERN = re.compile("[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF]+", flags=re.UNICODE)

def clean_text(text: str) -> str:
    text = URL_PATTERN.sub('', text)
    text = EMOJI_PATTERN.sub('', text)
    return text.strip()

# ——————— PROMPT TEMPLATE ———————
FEW_SHOT_EXAMPLES = [
    { 'news': 'В Совбез ООН внесён проект резолюции по Сирии',
      'comment': 'Предложенная резолюция может усилить влияние России в регионе, демонстрируя дипломатическую инициативу.' },
    { 'news': 'США ввели новые санкции против российских банков',
      'comment': 'Эти меры угрожают финансовой стабильности, но дадут Москве повод искать альтернативные расчётные схемы.' },
    { 'news': 'Иран и Китай подписали стратегическое соглашение',
      'comment': 'Укрепление иранско-китайского тандема расширяет антироссийское давление на Ближнем Востоке.' }
]
SYSTEM_INSTRUCTION = (
    "Ты — Нейро-Александр Дугин, эксперт по геополитике. "
    "Пиши 1–4 предложения короткого аналитического комментария без эмодзи и ссылок, упоминая лишь суть новости."
)

def build_prompt(news_text: str) -> str:
    prompt = SYSTEM_INSTRUCTION + "\n\n"
    for ex in FEW_SHOT_EXAMPLES:
        prompt += f"Новость: {ex['news']}\nКомментарий: {ex['comment']}\n\n"
    prompt += f"Новость: {news_text}\nКомментарий:"
    return prompt

# ——————— NEWS FETCHING & SELECTION ———————
async def fetch_popular_news():
    now = datetime.utcnow()
    since = now - timedelta(hours=2)
    texts = []
    for channel in SOURCE_CHANNELS:
        async for msg in client.iter_messages(channel, offset_date=since, limit=200):
            if msg.text and not msg.fwd_from:
                texts.append(clean_text(msg.text))
    counts = Counter(texts)
    popular = [txt for txt, cnt in counts.items() if cnt >= MENTION_THRESHOLD]
    return popular

# ——————— CONTENT GENERATION ———————
async def generate_comment(news_text: str) -> str:
    prompt = build_prompt(news_text)
    resp = openai.ChatCompletion.create(
        model='gpt-4o-mini',
        messages=[{'role': 'system', 'content': SYSTEM_INSTRUCTION},
                  {'role': 'user', 'content': prompt}],
        max_tokens=100,
        temperature=0.7
    )
    return resp.choices[0].message.content.strip()

# ——————— PUBLISH & LOGGING ———————
async def publish_comment(comment: str):
    await client.send_message(TARGET_CHANNEL, comment)

def log_post(news: str, comment: str):
    c.execute(
        'INSERT OR IGNORE INTO posts(news_text, generated_comment, published_at) VALUES (?, ?, ?)',
        (news, comment, datetime.utcnow())
    )
    conn.commit()

async def notify_error(err: Exception):
    text = f"[Ошибка бота] {type(err).__name__}: {err}"
    try:
        await client.send_message(ERROR_NOTIFY_CHAT, text)
    except Exception as e:
        logging.error(f"Failed to send error notification: {e}")

# ——————— MAIN JOB ———————
@scheduler.scheduled_job('interval', minutes=FETCH_INTERVAL_MINUTES)
async def job_fetch_and_post():
    try:
        popular_news = await fetch_popular_news()
        for news in popular_news:
            c.execute('SELECT 1 FROM posts WHERE news_text = ?', (news,))
            if c.fetchone():
                continue
            comment = await generate_comment(news)
            await publish_comment(comment)
            log_post(news, comment)
            logging.info(f"Posted comment for news: {news[:30]}...")
    except Exception as e:
        logging.exception("Error in scheduled job")
        await notify_error(e)

# ——————— STARTUP ———————
async def main():
    await client.start(bot_token=BOT_TOKEN)
    scheduler.start()
    logging.info("Bot started. Scheduler running...")
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
