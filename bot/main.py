"""
Главный файл — Telegram-бот + планировщик проверок Kwork.
"""
import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from db.storage import init_db, get_draft, update_draft_status, get_stats, mark_seen, is_seen
from scraper.kwork_parser import fetch_projects  # async function
from scraper.fl_parser import fetch_fl_projects
from scraper.habr_parser import fetch_habr_projects
from scraper.filter import filter_projects
from ai.drafter import generate_draft
from bot.notifier import send_project_notification

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()


# ─── КОМАНДЫ ─────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message):
    if message.from_user.id != config.MY_TELEGRAM_ID:
        return
    await message.answer(
        "✅ *Kwork Monitor запущен*\n\n"
        f"Проверяю заказы каждые *{config.CHECK_INTERVAL_MINUTES} минут*.\n\n"
        "Команды:\n"
        "/check — проверить прямо сейчас\n"
        "/stats — статистика\n"
        "/stop — остановить мониторинг\n",
        parse_mode="Markdown",
    )


@dp.message(Command("check"))
async def cmd_check(message: Message):
    if message.from_user.id != config.MY_TELEGRAM_ID:
        return
    await message.answer("🔍 Проверяю заказы...")
    count = await check_kwork(bot)
    await message.answer(
        f"✅ Проверка завершена. Новых релевантных заказов: *{count}*",
        parse_mode="Markdown",
    )


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != config.MY_TELEGRAM_ID:
        return
    s = get_stats()
    await message.answer(
        f"📊 *Статистика*\n\n"
        f"Просмотрено заказов: {s['total_seen']}\n"
        f"Откликов отправлено: {s['sent']}\n"
        f"Пропущено: {s['skipped']}",
        parse_mode="Markdown",
    )


# ─── CALLBACK КНОПКИ ─────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("copy:"))
async def cb_copy(callback: CallbackQuery):
    draft_id = int(callback.data.split(":")[1])
    draft = get_draft(draft_id)
    if not draft:
        await callback.answer("Черновик не найден", show_alert=True)
        return

    # Отправляем чистый текст для копирования
    await callback.message.answer(
        f"📋 *Скопируй и вставь на Kwork:*\n\n"
        f"`{draft['draft_text']}`",
        parse_mode="Markdown",
    )
    update_draft_status(draft_id, "sent")
    await callback.answer("Текст отправлен ниже — нажми чтобы скопировать")


@dp.callback_query(F.data.startswith("skip:"))
async def cb_skip(callback: CallbackQuery):
    draft_id = int(callback.data.split(":")[1])
    update_draft_status(draft_id, "skipped")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Заказ пропущен")


@dp.callback_query(F.data.startswith("regen:"))
async def cb_regen(callback: CallbackQuery):
    """Перегенерировать отклик для этого заказа."""
    draft_id = int(callback.data.split(":")[1])
    draft = get_draft(draft_id)
    if not draft:
        await callback.answer("Черновик не найден", show_alert=True)
        return

    await callback.answer("Генерирую новый вариант...")

    # Получаем project_id из черновика и перегенерируем
    # (используем сохранённые данные — нет смысла парсить заново)
    from db.storage import save_draft as save_new_draft

    # Простая перегенерация с другим seed через повторный вызов drafter
    # Для этого нам нужен project объект — создаём минимальный из БД данных
    from scraper.kwork_parser import KworkProject
    # Берём данные из seen_projects
    import sqlite3
    with sqlite3.connect("./data/monitor.db") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM seen_projects WHERE project_id = ?",
            (draft["project_id"],)
        ).fetchone()

    if row:
        project = KworkProject(
            project_id=row["project_id"],
            title=row["title"],
            description="(описание недоступно — введи правки вручную)",
            budget=row["budget"] or 0,
            budget_raw=str(row["budget"]) + " ₽" if row["budget"] else "не указан",
            url=f"https://kwork.ru/projects/{row['project_id']}",
        )
        new_draft_text = generate_draft(project)
        new_draft_id = save_new_draft(project.project_id, new_draft_text)

        from bot.notifier import _build_keyboard
        await callback.message.answer(
            f"✏️ *Новый вариант отклика:*\n\n{new_draft_text}",
            parse_mode="Markdown",
            reply_markup=_build_keyboard(new_draft_id, project.url),
        )
    else:
        await callback.message.answer(
            "Не удалось найти данные заказа для перегенерации. "
            "Отредактируй черновик вручную."
        )


# ─── ОСНОВНАЯ ПРОВЕРКА ───────────────────────────────────────────────────────

async def _process_projects(bot: Bot, projects: list, new_count: int) -> int:
    """Отфильтровать, дедуплицировать и отправить уведомления."""
    relevant = filter_projects(projects)
    for project in relevant:
        if is_seen(project.project_id):
            continue
        mark_seen(project.project_id, project.title, project.budget)
        logger.info(f"Generating draft: {project.title[:60]}")
        draft_text = generate_draft(project)
        await send_project_notification(bot, project, draft_text)
        new_count += 1
        await asyncio.sleep(1)
    return new_count


async def check_all_platforms(bot: Bot) -> int:
    """Проверить все платформы на новые релевантные заказы."""
    logger.info("Starting multi-platform check...")
    new_count = 0

    # ── Kwork (Playwright, 2 страницы) ──────────────────────────────────────
    logger.info("Checking Kwork...")
    for page_num in range(1, 3):
        projects = await fetch_projects(page_num=page_num)
        if not projects:
            break
        new_count = await _process_projects(bot, projects, new_count)
        await asyncio.sleep(config.REQUEST_DELAY_SECONDS)

    # ── FL.ru (requests, 2 страницы) ─────────────────────────────────────────
    logger.info("Checking FL.ru...")
    for page_num in range(1, 3):
        fl_projects = fetch_fl_projects(page=page_num)
        if not fl_projects:
            break
        new_count = await _process_projects(bot, fl_projects, new_count)
        await asyncio.sleep(config.REQUEST_DELAY_SECONDS)

    # ── Habr Freelance (requests, 2 страницы) ────────────────────────────────
    # Habr Freelance закрылся в 2025 (HTTP 410), пропускаем
    # logger.info("Checking Habr Freelance...")

    logger.info(f"Multi-platform check complete. New: {new_count}")
    return new_count


# Алиас для обратной совместимости с планировщиком
async def check_kwork(bot: Bot) -> int:
    return await check_all_platforms(bot)


# ─── ЗАПУСК ──────────────────────────────────────────────────────────────────

async def main():
    init_db()

    # Планировщик
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_kwork,
        trigger="interval",
        minutes=config.CHECK_INTERVAL_MINUTES,
        args=[bot],
        id="kwork_check",
    )
    scheduler.start()
    logger.info(f"Scheduler started — checking every {config.CHECK_INTERVAL_MINUTES} min")

    # Уведомление о старте
    try:
        await bot.send_message(
            config.MY_TELEGRAM_ID,
            f"🚀 Kwork Monitor запущен!\nПроверка каждые {config.CHECK_INTERVAL_MINUTES} минут.\n/check — проверить сейчас",
        )
    except Exception as e:
        logger.warning(f"Could not send start notification: {e}")

    # Первая проверка сразу при старте
    await check_kwork(bot)

    # ── Telegram-мониторинг (если настроен) ──────────────────────────────────
    tg_api_id = int(os.getenv("TG_API_ID") or "0")
    tg_api_hash = os.getenv("TG_API_HASH", "")

    if tg_api_id and tg_api_hash:
        from scraper.tg_monitor import TelegramChatMonitor

        async def tg_notify(tg_project):
            """Колбэк: получили лида из TG → генерируем отклик → уведомляем."""
            from ai.drafter import generate_draft
            from bot.notifier import send_project_notification
            from db.storage import is_seen, mark_seen
            if is_seen(tg_project.project_id):
                return
            mark_seen(tg_project.project_id, tg_project.title, 0)
            draft = generate_draft(tg_project)
            await send_project_notification(bot, tg_project, draft)

        tg_monitor = TelegramChatMonitor(tg_api_id, tg_api_hash, tg_notify)

        # Запускаем TG-мониторинг параллельно с ботом
        await asyncio.gather(
            tg_monitor.start(),
            dp.start_polling(bot),
        )
    else:
        logger.info("TG_API_ID/TG_API_HASH not set — Telegram chat monitoring disabled")
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
