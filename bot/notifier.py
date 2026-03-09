"""
Telegram-бот — уведомления о новых заказах + управление черновиками.
"""
import asyncio
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import config
from db.storage import save_draft

logger = logging.getLogger(__name__)


def _build_keyboard(draft_id: int, project_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📋 Скопировать отклик",
                callback_data=f"copy:{draft_id}",
            ),
            InlineKeyboardButton(
                text="🔗 Открыть заказ",
                url=project_url,
            ),
        ],
        [
            InlineKeyboardButton(
                text="✏️ Перегенерировать",
                callback_data=f"regen:{draft_id}",
            ),
            InlineKeyboardButton(
                text="❌ Пропустить",
                callback_data=f"skip:{draft_id}",
            ),
        ],
    ])


async def send_project_notification(
    bot: Bot,
    project,
    draft_text: str,
) -> None:
    """Отправить уведомление о новом релевантном заказе."""
    # Сохраняем черновик в БД
    draft_id = save_draft(project.project_id, draft_text)

    # Источник
    source = getattr(project, "source", "kwork.ru")
    source_icons = {"fl.ru": "🟠", "habr.freelance": "🟣", "kwork.ru": "🟢"}
    icon = source_icons.get(source, "🔔")

    # Формируем сообщение
    budget_str = f"💰 {project.budget_raw}" if project.budget_raw else "💰 бюджет не указан"
    category_str = f"📂 {project.category}\n" if hasattr(project, "category") and project.category else ""

    text = (
        f"{icon} *Новый заказ \\| {source}*\n\n"
        f"*{_escape(project.title)}*\n\n"
        f"{category_str}"
        f"{budget_str}\n\n"
        f"{_escape(project.description[:400])}{'...' if len(project.description) > 400 else ''}\n\n"
        f"─────────────────\n"
        f"✍️ *Готовый отклик:*\n\n"
        f"{_escape(draft_text)}"
    )

    keyboard = _build_keyboard(draft_id, project.url)

    try:
        await bot.send_message(
            chat_id=config.MY_TELEGRAM_ID,
            text=text,
            parse_mode="Markdown",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        logger.info(f"Notification sent for project {project.project_id}")
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")


def _escape(text: str) -> str:
    """Экранировать спецсимволы Markdown."""
    for ch in ["_", "*", "[", "]", "`"]:
        text = text.replace(ch, f"\\{ch}")
    return text
