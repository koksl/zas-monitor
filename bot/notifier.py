"""
Telegram-бот — уведомления о новых заказах + управление черновиками.
"""
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import config
from db.storage import mark_seen

logger = logging.getLogger(__name__)


def _build_project_keyboard(project_id: str, project_url: str) -> InlineKeyboardMarkup:
    """Кнопки первичного уведомления: принять или отказаться."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять",      callback_data=f"accept:{project_id}"),
            InlineKeyboardButton(text="❌ Отказаться",   callback_data=f"skip:{project_id}"),
        ],
        [
            InlineKeyboardButton(text="🔗 Открыть заказ", url=project_url),
        ],
    ])


def _build_draft_keyboard(draft_id: int, project_url: str) -> InlineKeyboardMarkup:
    """Кнопки под черновиком отклика."""
    buttons = [
        [
            InlineKeyboardButton(text="🔄 Перегенерировать", callback_data=f"regen:{draft_id}"),
        ],
    ]
    if project_url:
        buttons[0].append(
            InlineKeyboardButton(text="🔗 Открыть заказ", url=project_url)
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def send_project_notification(bot: Bot, project, draft_text: str = "") -> None:
    """Отправить уведомление о новом релевантном заказе."""
    source = getattr(project, "source", "kwork.ru")
    source_icons = {"fl.ru": "🟠", "habr.freelance": "🟣", "kwork.ru": "🟢"}
    icon = source_icons.get(source, "🔔")

    budget_str   = f"💰 {project.budget_raw}" if getattr(project, "budget_raw", "") else "💰 не указан"
    category_str = f"📂 {project.category}\n" if getattr(project, "category", "") else ""
    published    = getattr(project, "published_at", "")
    time_str     = f"🕐 {published}\n" if published else ""

    desc = project.description[:400]
    if len(project.description) > 400:
        desc += "..."

    text = (
        f"{icon} *Новый заказ \\| {_esc(source)}*\n\n"
        f"*{_esc(project.title)}*\n\n"
        f"{category_str}"
        f"{budget_str}\n"
        f"{time_str}\n"
        f"{_esc(desc)}"
    )

    keyboard = _build_project_keyboard(project.project_id, project.url)

    try:
        await bot.send_message(
            chat_id=config.MY_TELEGRAM_ID,
            text=text,
            parse_mode="Markdown",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        logger.info(f"Notification sent: {project.project_id}")
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")


def _esc(text: str) -> str:
    """Экранировать спецсимволы Markdown V1."""
    for ch in ["_", "*", "[", "]", "`"]:
        text = text.replace(ch, f"\\{ch}")
    return text
