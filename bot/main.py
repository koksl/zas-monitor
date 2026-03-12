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
from db.storage import (
    DB_PATH, init_db, get_draft, get_project,
    update_draft_status, get_stats, mark_seen, is_seen, save_draft,
)
from scraper.kwork_parser import fetch_projects, KworkProject
from scraper.filter import filter_projects
from scraper import kwork_api
from ai.drafter import generate_draft
from bot.notifier import send_project_notification, _build_draft_keyboard, _esc

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
        await message.answer(f"Твой Telegram ID: {message.from_user.id}")
        return
    await message.answer(
        "✅ *Kwork Monitor запущен*\n\n"
        f"Проверяю заказы каждые *{config.CHECK_INTERVAL_MINUTES} минут*.\n\n"
        "Команды:\n"
        "/check — проверить прямо сейчас\n"
        "/stats — статистика\n",
        parse_mode="Markdown",
    )


@dp.message(Command("check"))
async def cmd_check(message: Message):
    if message.from_user.id != config.MY_TELEGRAM_ID:
        await message.answer(
            f"⛔ Нет доступа. Твой ID: {message.from_user.id}\n"
            f"Ожидается: {config.MY_TELEGRAM_ID}\n\n"
            f"Исправь MY_TELEGRAM_ID в Railway Variables."
        )
        return
    await message.answer("🔍 Проверяю заказы...")
    try:
        count = await check_all_platforms(bot)
        await message.answer(
            f"✅ Проверка завершена. Новых релевантных заказов: *{count}*",
            parse_mode="Markdown",
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка при проверке:\n`{e}`", parse_mode="Markdown")
        logger.exception("check_all_platforms failed")


@dp.message(Command("debug"))
async def cmd_debug(message: Message):
    if message.from_user.id != config.MY_TELEGRAM_ID:
        return
    await message.answer("🔍 Диагностика парсера...")
    import requests
    try:
        r = requests.get(
            f"{config.KWORK_PROJECTS_URL}?c=all&page=1",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        html_preview = r.text[:500].replace("<", "&lt;")
        # Считаем вхождения want-card
        card_count = r.text.count("want-card")
        await message.answer(
            f"HTTP: {r.status_code}\n"
            f"Размер HTML: {len(r.text)} символов\n"
            f"Вхождений 'want-card': {card_count}\n\n"
            f"Первые 500 символов:\n<pre>{html_preview}</pre>",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != config.MY_TELEGRAM_ID:
        await message.answer(
            f"⛔ Нет доступа. Твой ID: {message.from_user.id}\n"
            f"Ожидается: {config.MY_TELEGRAM_ID}\n\n"
            f"Исправь MY_TELEGRAM_ID в Railway Variables."
        )
        return
    s = get_stats()
    await message.answer(
        f"📊 *Статистика*\n\n"
        f"Просмотрено заказов: {s['total_seen']}\n"
        f"Откликов отправлено: {s['sent']}\n"
        f"Пропущено: {s['skipped']}",
        parse_mode="Markdown",
    )


# ─── CALLBACK: ПРИНЯТЬ ────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("accept:"))
async def cb_accept(callback: CallbackQuery):
    project_id = callback.data.split(":", 1)[1]
    row = get_project(project_id)

    if not row:
        await callback.answer("Данные заказа не найдены", show_alert=True)
        return

    await callback.answer("Генерирую отклик...")

    # Убираем кнопки с исходного сообщения
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Восстанавливаем объект проекта для drafter
    project = KworkProject(
        project_id=row["project_id"],
        title=row["title"],
        description=row["description"] or "(описание недоступно)",
        budget=row["budget"] or 0,
        budget_raw=row["budget_raw"] or "",
        url=row["url"] or f"https://kwork.ru/projects/{row['project_id']}",
        source=row["source"] or "kwork.ru",
    )

    draft_text = generate_draft(project)
    draft_id = save_draft(project.project_id, draft_text)
    update_draft_status(draft_id, "pending")

    await callback.message.answer(
        f"✅ *Принято!* Готовый отклик:\n\n"
        f"`{_esc(draft_text)}`\n\n"
        f"_Нажми на текст — он скопируется_",
        parse_mode="Markdown",
        reply_markup=_build_draft_keyboard(draft_id, row["url"] or ""),
        disable_web_page_preview=True,
    )


# ─── CALLBACK: ОТОЗВАТЬ АВТО-ОТКЛИК ─────────────────────────────────────────

@dp.callback_query(F.data.startswith("revoke:"))
async def cb_revoke(callback: CallbackQuery):
    project_id = callback.data.split(":", 1)[1]
    await callback.answer("Отзываю отклик...")
    success = await asyncio.get_event_loop().run_in_executor(
        None, kwork_api.revoke_response, project_id
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if success:
        await callback.message.answer(f"✅ Отклик на заказ {project_id} отозван.")
    else:
        await callback.message.answer(
            f"⚠️ Не удалось отозвать отклик. Возможно, прошло слишком много времени.\n"
            f"Отзови вручную: https://kwork.ru/projects/{project_id}"
        )


# ─── CALLBACK: ОТКАЗАТЬСЯ ────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("skip:"))
async def cb_skip(callback: CallbackQuery):
    project_id = callback.data.split(":", 1)[1]
    # Убираем кнопки
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer("Заказ отклонён")
    logger.info(f"Project skipped: {project_id}")


# ─── CALLBACK: ПЕРЕГЕНЕРИРОВАТЬ ───────────────────────────────────────────────

@dp.callback_query(F.data.startswith("regen:"))
async def cb_regen(callback: CallbackQuery):
    draft_id = int(callback.data.split(":", 1)[1])
    draft = get_draft(draft_id)

    if not draft:
        await callback.answer("Черновик не найден", show_alert=True)
        return

    await callback.answer("Генерирую новый вариант...")

    row = get_project(draft["project_id"])
    if not row:
        await callback.message.answer("Не удалось найти данные заказа. Отредактируй отклик вручную.")
        return

    project = KworkProject(
        project_id=row["project_id"],
        title=row["title"],
        description=row["description"] or "(описание недоступно)",
        budget=row["budget"] or 0,
        budget_raw=row["budget_raw"] or "",
        url=row["url"] or f"https://kwork.ru/projects/{row['project_id']}",
        source=row["source"] or "kwork.ru",
    )

    new_draft_text = generate_draft(project)
    new_draft_id = save_draft(project.project_id, new_draft_text)

    # Заменяем текст в том же сообщении
    try:
        await callback.message.edit_text(
            f"✅ *Принято!* Готовый отклик:\n\n"
            f"`{_esc(new_draft_text)}`\n\n"
            f"_Нажми на текст — он скопируется_",
            parse_mode="Markdown",
            reply_markup=_build_draft_keyboard(new_draft_id, row["url"] or ""),
            disable_web_page_preview=True,
        )
    except Exception:
        # Если редактирование не вышло — шлём новым сообщением
        await callback.message.answer(
            f"✅ *Новый вариант:*\n\n"
            f"`{_esc(new_draft_text)}`",
            parse_mode="Markdown",
            reply_markup=_build_draft_keyboard(new_draft_id, row["url"] or ""),
        )


# ─── ОСНОВНАЯ ПРОВЕРКА ───────────────────────────────────────────────────────

async def _process_projects(bot: Bot, projects: list, new_count: int) -> int:
    relevant = filter_projects(projects)
    for project in relevant:
        if is_seen(project.project_id):
            continue
        mark_seen(
            project.project_id,
            project.title,
            project.budget,
            budget_raw=getattr(project, "budget_raw", ""),
            url=getattr(project, "url", ""),
            description=getattr(project, "description", ""),
            published_at=getattr(project, "published_at", ""),
            source=getattr(project, "source", "kwork.ru"),
        )
        logger.info(f"New project: {project.title[:60]}")

        source = getattr(project, "source", "kwork.ru")

        # ── Авто-отклик на Kwork ────────────────────────────────────────────
        if source == "kwork.ru" and kwork_api.is_available():
            asyncio.create_task(_auto_respond(bot, project))
        else:
            await send_project_notification(bot, project)

        new_count += 1
        await asyncio.sleep(1)
    return new_count


async def _auto_respond(bot: Bot, project) -> None:
    """Генерировать отклик и авто-отправить на Kwork, уведомить в TG."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    try:
        draft_text = generate_draft(project)
        draft_id = save_draft(project.project_id, draft_text)

        success = await asyncio.get_event_loop().run_in_executor(
            None, kwork_api.submit_response, project.project_id, draft_text
        )

        if success:
            update_draft_status(draft_id, "sent")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отозвать отклик", callback_data=f"revoke:{project.project_id}")],
                [InlineKeyboardButton(text="🔗 Смотреть заказ", url=project.url or f"https://kwork.ru/projects/{project.project_id}")],
            ])
            await bot.send_message(
                config.MY_TELEGRAM_ID,
                f"🤖 *Авто-отклик отправлен!*\n\n"
                f"📋 {project.title[:80]}\n"
                f"💰 {project.budget_raw or '—'}\n\n"
                f"_Отклик:_\n`{_esc(draft_text[:300])}`\n\n"
                f"_Нажми «Отозвать» если не нужен (10 мин)_",
                parse_mode="Markdown",
                reply_markup=kb,
                disable_web_page_preview=True,
            )
        else:
            # Авто-отклик не сработал — отправляем обычное уведомление
            update_draft_status(draft_id, "failed")
            logger.warning(f"Auto-respond failed for {project.project_id}, sending manual notification")
            await send_project_notification(bot, project)
    except Exception as e:
        logger.error(f"_auto_respond error: {e}")
        await send_project_notification(bot, project)


async def check_all_platforms(bot: Bot) -> int:
    new_count = 0

    # ── Kwork ──────────────────────────────────────────────────────────────
    logger.info("Checking Kwork...")
    for page_num in range(1, 3):
        projects = await fetch_projects(page_num=page_num)
        if not projects:
            break
        new_count = await _process_projects(bot, projects, new_count)
        await asyncio.sleep(config.REQUEST_DELAY_SECONDS)

    # Habr Freelance отключён — площадка вернула 410 Gone (URL устарел)

    # ── Freelance.ru ───────────────────────────────────────────────────────
    logger.info("Checking Freelance.ru...")
    try:
        from scraper.freelance_parser import fetch_freelance_projects
        loop = asyncio.get_event_loop()
        for page_num in range(1, 3):
            fr_projects = await loop.run_in_executor(None, fetch_freelance_projects, page_num)
            if not fr_projects:
                break
            new_count = await _process_projects(bot, fr_projects, new_count)
            await asyncio.sleep(config.REQUEST_DELAY_SECONDS)
    except Exception as e:
        logger.error(f"Freelance.ru check error: {e}")

    # ── Weblancer ──────────────────────────────────────────────────────────
    logger.info("Checking Weblancer...")
    try:
        from scraper.weblancer_parser import fetch_weblancer_projects
        loop = asyncio.get_event_loop()
        for page_num in range(1, 3):
            wl_projects = await loop.run_in_executor(None, fetch_weblancer_projects, page_num)
            if not wl_projects:
                break
            new_count = await _process_projects(bot, wl_projects, new_count)
            await asyncio.sleep(config.REQUEST_DELAY_SECONDS)
    except Exception as e:
        logger.error(f"Weblancer check error: {e}")

    logger.info(f"Check complete. New: {new_count}")
    return new_count


# ─── ЗАПУСК ──────────────────────────────────────────────────────────────────

async def main():
    init_db()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_all_platforms,
        trigger="interval",
        minutes=config.CHECK_INTERVAL_MINUTES,
        args=[bot],
        id="platform_check",
    )
    scheduler.start()
    logger.info(f"Scheduler started — checking every {config.CHECK_INTERVAL_MINUTES} min")

    try:
        await bot.send_message(
            config.MY_TELEGRAM_ID,
            f"🚀 Kwork Monitor запущен!\nПроверка каждые {config.CHECK_INTERVAL_MINUTES} минут.\n/check — проверить сейчас",
        )
    except Exception as e:
        logger.warning(f"Could not send start notification: {e}")

    await check_all_platforms(bot)

    tg_api_id   = int(os.getenv("TG_API_ID") or "0")
    tg_api_hash = os.getenv("TG_API_HASH", "")

    if tg_api_id and tg_api_hash:
        from scraper.tg_monitor import TelegramChatMonitor

        async def tg_notify(tg_project):
            if is_seen(tg_project.project_id):
                return
            mark_seen(
                tg_project.project_id, tg_project.title, 0,
                url=getattr(tg_project, "url", ""),
                description=getattr(tg_project, "description", ""),
                source="telegram",
            )
            await send_project_notification(bot, tg_project)

        tg_monitor = TelegramChatMonitor(tg_api_id, tg_api_hash, tg_notify)
        await asyncio.gather(tg_monitor.start(), dp.start_polling(bot))
    else:
        logger.info("TG monitoring disabled")
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
