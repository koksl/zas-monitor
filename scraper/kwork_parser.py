"""
Парсер kwork.ru/projects — requests + BeautifulSoup.
Без Playwright: данные берём из SSR-JSON в script-тегах или из HTML напрямую.
"""
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://kwork.ru/",
}

_session = requests.Session()
_session.headers.update(HEADERS)


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
    """Загрузить страницу проектов Kwork (без браузера)."""
    url = f"{config.KWORK_PROJECTS_URL}?c=all&page={page_num}"
    try:
        r = _session.get(url, timeout=config.REQUEST_TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"Kwork fetch error: {e}")
        return []

    projects = _try_parse_json(r.text)
    if projects:
        logger.info(f"Kwork page {page_num}: {len(projects)} projects (JSON)")
        return projects

    projects = _parse_html(r.text)
    logger.info(f"Kwork page {page_num}: {len(projects)} projects (HTML)")
    return projects


# ─── JSON-парсинг (SSR / window.__INITIAL_STATE__) ───────────────────────────

def _try_parse_json(html: str) -> List[KworkProject]:
    """Ищем JSON-данные проектов в script-тегах страницы."""
    # Kwork прячет данные в window.__INITIAL_STATE__ или window.KWORK_DATA
    for pattern in [
        r"window\.__INITIAL_STATE__\s*=\s*(\{.+?\});\s*</script>",
        r"window\.KWORK_DATA\s*=\s*(\{.+?\});\s*</script>",
        r"\"wants\"\s*:\s*(\[.+?\])\s*[,}]",
        r"\"projects\"\s*:\s*(\[.+?\])\s*[,}]",
    ]:
        m = re.search(pattern, html, re.DOTALL)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
            projects = _extract_from_json(data)
            if projects:
                return projects
        except (json.JSONDecodeError, Exception):
            continue

    # Ищем все script-теги с большим JSON
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script"):
        text = script.string or ""
        if len(text) < 200 or "title" not in text.lower():
            continue
        # Ищем массивы объектов с полем title
        m = re.search(r"(\[{.+?\"title\".+?}\])", text, re.DOTALL)
        if m:
            try:
                items = json.loads(m.group(1))
                projects = _extract_from_json(items)
                if projects:
                    return projects
            except Exception:
                continue

    return []


def _extract_from_json(data) -> List[KworkProject]:
    """Извлекаем проекты из произвольной JSON-структуры."""
    projects = []

    # Если это список объектов
    items = data if isinstance(data, list) else []

    # Если это словарь, ищем списки внутри
    if isinstance(data, dict):
        for key in ("wants", "projects", "data", "items", "list"):
            if key in data and isinstance(data[key], list):
                items = data[key]
                break
        if not items:
            # Рекурсивно ищем вложенные списки
            for v in data.values():
                if isinstance(v, list) and v and isinstance(v[0], dict) and "title" in v[0]:
                    items = v
                    break

    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get("name") or item.get("title") or ""
        if not title:
            continue
        project_id = str(item.get("id") or item.get("project_id") or "")
        if not project_id:
            project_id = "kw_" + hashlib.md5(title.encode()).hexdigest()[:10]
        desc = item.get("description") or item.get("desc") or ""
        budget_raw = str(item.get("price") or item.get("budget") or item.get("cost") or "не указан")
        budget = _parse_budget(budget_raw)
        url = item.get("url") or f"https://kwork.ru/projects/{project_id}"
        if url.startswith("/"):
            url = "https://kwork.ru" + url
        published_at = str(item.get("created") or item.get("date") or item.get("published_at") or "")
        category = str(item.get("category") or item.get("cat") or "")

        projects.append(KworkProject(
            project_id=project_id,
            title=title,
            description=desc,
            budget=budget,
            budget_raw=budget_raw,
            url=url,
            category=category,
            published_at=published_at,
        ))

    return projects


# ─── HTML-парсинг (BeautifulSoup fallback) ───────────────────────────────────

def _parse_html(html: str) -> List[KworkProject]:
    soup = BeautifulSoup(html, "html.parser")
    projects = []

    # Kwork: карточки проектов имеют классы want-card, wants-card, b-post и пр.
    cards = (
        soup.select("div.want-card")
        or soup.select("[class*='want-card']")
        or soup.select("[class*='wants-card']")
        or soup.select("article.project")
        or soup.select("div.b-post")
    )

    for card in cards:
        try:
            p = _parse_card(card)
            if p:
                projects.append(p)
        except Exception as e:
            logger.debug(f"Kwork card error: {e}")

    return projects


def _parse_card(card) -> Optional[KworkProject]:
    # Title + URL
    title_el = card.select_one(
        ".want-card__header-title a, .wants-card__header-title a, "
        "h2 a, h3 a, [class*='title'] a, [class*='name'] a"
    )
    if not title_el:
        return None
    title = title_el.get_text(strip=True)
    if not title:
        return None

    href = title_el.get("href", "")
    url = f"https://kwork.ru{href}" if href.startswith("/") else href or "https://kwork.ru/projects"

    # Project ID
    project_id = ""
    m = re.search(r"/projects/(\d+)", url)
    if m:
        project_id = m.group(1)
    if not project_id:
        # Try data attribute on card
        project_id = card.get("data-id", "") or card.get("id", "").replace("project-", "")
    if not project_id:
        project_id = "kw_" + hashlib.md5(title.encode()).hexdigest()[:10]

    # Description
    desc_el = card.select_one("[class*='desc'], [class*='description'], p")
    description = desc_el.get_text(strip=True) if desc_el else ""

    # Budget
    budget_el = card.select_one("[class*='price'], [class*='budget'], [class*='cost']")
    budget_raw = budget_el.get_text(strip=True) if budget_el else "не указан"
    budget = _parse_budget(budget_raw)

    # Category
    cat_el = card.select_one("[class*='category'], [class*='cat']")
    category = cat_el.get_text(strip=True) if cat_el else ""

    # Published at
    time_el = card.select_one(
        "time, [class*='date'], [class*='time'], [class*='ago'], [class*='created'], [class*='publish']"
    )
    published_at = ""
    if time_el:
        published_at = time_el.get_text(strip=True) or time_el.get("datetime", "")

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


# ─── Вспомогательные ─────────────────────────────────────────────────────────

def _parse_budget(text: str) -> int:
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else 0
