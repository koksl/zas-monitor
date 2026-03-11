"""
Мониторинг Telegram-чатов предпринимателей через Telethon (MTProto).
Ищет сообщения с запросами на разработку ботов/автоматизации.

Требует: личный аккаунт Telegram + API ID/Hash с my.telegram.org
"""
import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from telethon import TelegramClient, events
from telethon.tl.types import Message

import config
from scraper.filter import is_relevant

logger = logging.getLogger(__name__)

# ─── PERSISTENCE: кэш обработанных ID ────────────────────────────────────────

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "tg_seen.db")

def _init_seen_db():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    con = sqlite3.connect(_DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS seen_messages (msg_id INTEGER PRIMARY KEY, ts INTEGER DEFAULT (strftime('%s','now')))")
    con.execute("DELETE FROM seen_messages WHERE ts < strftime('%s','now') - 86400 * 7")  # TTL 7 дней
    con.commit()
    con.close()

def _is_msg_seen(msg_id: int) -> bool:
    con = sqlite3.connect(_DB_PATH)
    row = con.execute("SELECT 1 FROM seen_messages WHERE msg_id=?", (msg_id,)).fetchone()
    con.close()
    return row is not None

def _mark_msg_seen(msg_id: int):
    con = sqlite3.connect(_DB_PATH)
    con.execute("INSERT OR IGNORE INTO seen_messages(msg_id) VALUES(?)", (msg_id,))
    con.commit()
    con.close()


# ─── ЧАТЫ ДЛЯ МОНИТОРИНГА ───────────────────────────────────────────────────
# Публичные чаты предпринимателей и IT — добавляй/убирай по необходимости

CHATS_TO_MONITOR = [
    # ── Предприниматели ──────────────────────────────────────────────────────
    "https://t.me/business_ru",          # Бизнес RU
    "https://t.me/biznes_ru",
    "https://t.me/msp_russia",           # МСП Россия
    "https://t.me/predprinimatel_chat",
    "https://t.me/opora_russia_chat",
    "https://t.me/malyi_biznes_chat",

    # ── IT / разработка ──────────────────────────────────────────────────────
    "https://t.me/freelance_it_ru",
    "https://t.me/it_freelance_ru",
    "https://t.me/python_jobs",
    "https://t.me/tg_dev",              # Telegram разработка

    # ── ИИ / нейросети ───────────────────────────────────────────────────────
    "https://t.me/ai_ru_chat",
    "https://t.me/gpt_chat_ru",
    "https://t.me/neural_networks_ru",
    "https://t.me/chatgpt_russia",

    # ── Маркетинг / автоматизация ────────────────────────────────────────────
    "https://t.me/marketing_ru_chat",
    "https://t.me/smm_ru_chat",
    "https://t.me/crm_chat_ru",
    "https://t.me/automation_business",
]

# Минимальная длина сообщения для обработки (фильтр спама)
MIN_MESSAGE_LENGTH = 50

# Хранение уже обработанных сообщений (в памяти, сбрасывается при рестарте)
_processed_message_ids: set = set()


class TelegramChatMonitor:
    def __init__(self, api_id: int, api_hash: str, notify_callback):
        """
        api_id, api_hash — с my.telegram.org
        notify_callback — async функция(project_like_obj) для отправки уведомления
        """
        self.api_id = api_id
        self.api_hash = api_hash
        self.notify_callback = notify_callback
        self.client = None
        self.session_path = "./data/tg_session"
        _init_seen_db()

    async def start(self):
        """Запустить мониторинг с автоматическим reconnect."""
        retry_delay = 5
        while True:
            try:
                self.client = TelegramClient(
                    self.session_path,
                    self.api_id,
                    self.api_hash,
                )
                tg_phone = os.environ.get("TG_PHONE", "")
                await self.client.start(phone=tg_phone if tg_phone else None)
                logger.info("Telegram client started")

                self.client.add_event_handler(
                    self._handle_message,
                    events.NewMessage(chats=CHATS_TO_MONITOR),
                )

                logger.info(f"Monitoring {len(CHATS_TO_MONITOR)} Telegram chats")
                retry_delay = 5  # сброс после успешного подключения
                await self.client.run_until_disconnected()

            except Exception as e:
                logger.warning(f"TG client disconnected: {e}. Reconnecting in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 300)  # экспоненциальный backoff до 5 мин

    async def _handle_message(self, event: events.NewMessage.Event):
        """Обработать новое сообщение из чата."""
        msg: Message = event.message
        if not msg.text:
            return
        if len(msg.text) < MIN_MESSAGE_LENGTH:
            return
        if _is_msg_seen(msg.id):
            return

        _mark_msg_seen(msg.id)

        # Проверяем релевантность через тот же фильтр что и для площадок
        fake_project = _FakeProject(
            title=msg.text[:100],
            description=msg.text,
        )

        if not is_relevant(fake_project):
            return

        # Получаем информацию о чате
        try:
            chat = await event.get_chat()
            chat_title = getattr(chat, "title", "Telegram чат")
            chat_username = getattr(chat, "username", None)
            chat_url = f"https://t.me/{chat_username}/{msg.id}" if chat_username else "https://t.me"
        except Exception:
            chat_title = "Telegram"
            chat_url = "https://t.me"

        # Получаем отправителя
        try:
            sender = await event.get_sender()
            sender_name = getattr(sender, "first_name", "") + " " + getattr(sender, "last_name", "")
            sender_name = sender_name.strip() or "Аноним"
        except Exception:
            sender_name = "Аноним"

        logger.info(f"TG match in [{chat_title}]: {msg.text[:60]}")

        # Оборачиваем в объект, совместимый с нашим drafter
        tg_project = TGLeadProject(
            project_id=f"tg_{msg.id}",
            title=msg.text[:150],
            description=msg.text,
            budget=0,
            budget_raw="не указан",
            url=chat_url,
            source="telegram",
            chat_name=chat_title,
            sender_name=sender_name,
        )

        await self.notify_callback(tg_project)

    async def scan_recent(self, hours: int = 24, limit: int = 100):
        """
        Разовое сканирование последних сообщений в чатах.
        Полезно при первом запуске чтобы не пропустить старые посты.
        """
        if not self.client:
            logger.error("Client not started")
            return

        found = 0
        since = datetime.now() - timedelta(hours=hours)

        for chat_url in CHATS_TO_MONITOR:
            try:
                async for msg in self.client.iter_messages(
                    chat_url, limit=limit, offset_date=since, reverse=True
                ):
                    if not msg.text or len(msg.text) < MIN_MESSAGE_LENGTH:
                        continue
                    fake = _FakeProject(title=msg.text[:100], description=msg.text)
                    if is_relevant(fake):
                        found += 1
                        logger.info(f"Historic match in {chat_url}: {msg.text[:60]}")
            except Exception as e:
                logger.debug(f"Can't access {chat_url}: {e}")

        logger.info(f"Historic scan complete. Found {found} relevant messages")
        return found


class _FakeProject:
    """Минимальный объект для передачи в фильтр."""
    def __init__(self, title: str, description: str):
        self.title = title
        self.description = description
        self.budget = 0
        self.budget_raw = ""


class TGLeadProject:
    """Проект из Telegram-чата — совместим с drafter и notifier."""
    def __init__(self, project_id, title, description, budget,
                 budget_raw, url, source, chat_name, sender_name):
        self.project_id = project_id
        self.title = title
        self.description = description
        self.budget = budget
        self.budget_raw = budget_raw
        self.url = url
        self.source = source
        self.category = f"Из чата: {chat_name}"
        self.chat_name = chat_name
        self.sender_name = sender_name
