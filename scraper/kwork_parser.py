"""
Парсер kwork.ru/projects — Playwright (headless Chromium).
Kwork рендерит проекты через Vue.js, поэтому нужен браузер.
"""
import asyncio
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

from playwright.async_api import async_playwright

import config

logger = logging.getLogger(__name__)


@dataclass
class KworkProject:
    project_id: str
    title: str
    description: str
    budget: int
    budget_raw: str
    url: str
    category: str = ""
    published_at: str = ""
    source: str = "kwork.ru"


async def fetch_projects(page_num: int = 1) -> List[KworkProject]:
    """Загрузить страницу проектов Kwork через headless браузер."""
    url = f"{config.KWORK_PROJECTS_URL}?c=all&page={page_num}"
    projects = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        # Устанавливаем реальный User-Agent
        await page.set_extra_http_headers({
            "Accept-Language": "ru-RU,ru;q=0.9",
        })

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)

            # Ждём пока загрузятся карточки проектов
            try:
                await page.wait_for_selector(".want-card", timeout=15000)
            except Exception:
                logger.warning("Selector .want-card not found, trying alternatives...")

            # Парсим HTML через Playwright
            cards = await page.query_selector_all(".want-card")

            if not cards:
                # Fallback селекторы
                cards = await page.query_selector_all("[class*='want-card']")

            logger.info(f"Found {len(cards)} project cards on page {page_num}")

            for card in cards:
                try:
                    project = await _parse_card(card, page)
                    if project:
                        projects.append(project)
                except Exception as e:
                    logger.debug(f"Card parse error: {e}")

        except Exception as e:
            logger.error(f"Kwork fetch error: {e}")
        finally:
            await browser.close()

    return projects


async def _parse_card(card, page) -> Optional[KworkProject]:
    # --- Title & URL ---
    title_el = await card.query_selector(".want-card__header-title a, .wants-card__header-title a, h2 a, [class*='title'] a")
    if not title_el:
        return None

    title = (await title_el.inner_text()).strip()
    if not title:
        return None

    href = await title_el.get_attribute("href") or ""
    url = f"https://kwork.ru{href}" if href.startswith("/") else href or "https://kwork.ru/projects"

    # --- Project ID ---
    project_id = ""
    if "/projects/" in url:
        m = re.search(r"/projects/(\d+)", url)
        if m:
            project_id = m.group(1)
    if not project_id:
        import hashlib
        project_id = hashlib.md5(title.encode()).hexdigest()[:12]

    # --- Description ---
    desc_el = await card.query_selector("[class*='desc'], [class*='description'], p")
    description = (await desc_el.inner_text()).strip() if desc_el else ""

    # --- Budget ---
    budget_el = await card.query_selector("[class*='price'], [class*='budget'], [class*='cost']")
    budget_raw = (await budget_el.inner_text()).strip() if budget_el else "не указан"
    budget = _parse_budget(budget_raw)

    # --- Category ---
    cat_el = await card.query_selector("[class*='category'], [class*='cat']")
    category = (await cat_el.inner_text()).strip() if cat_el else ""

    # --- Published at ---
    time_el = await card.query_selector(
        "time, [class*='date'], [class*='time'], [class*='ago'], [class*='created'], [class*='publish']"
    )
    published_at = ""
    if time_el:
        published_at = (await time_el.inner_text()).strip()
        if not published_at:
            published_at = await time_el.get_attribute("datetime") or ""

    return KworkProject(
        project_id=str(project_id),
        title=title,
        description=description,
        budget=budget,
        budget_raw=budget_raw,
        url=url,
        category=category,
        published_at=published_at,
    )


def _parse_budget(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else 0


def fetch_projects_sync(page_num: int = 1) -> List[KworkProject]:
    """Синхронная обёртка для вызова из не-async контекста."""
    return asyncio.run(fetch_projects(page_num))
