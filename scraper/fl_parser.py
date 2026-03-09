"""
Парсер fl.ru/projects — статичный HTML, не нужен браузер.
"""
import logging
import re
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

BASE_URL = "https://www.fl.ru"


@dataclass
class FLProject:
    project_id: str
    title: str
    description: str
    budget: int
    budget_raw: str
    url: str
    source: str = "fl.ru"


def fetch_fl_projects(page: int = 1) -> List[FLProject]:
    """Получить список проектов с fl.ru."""
    url = f"{BASE_URL}/projects/?kind=1&page={page}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"FL.ru fetch error: {e}")
        return []

    return _parse_fl(r.text)


def _parse_fl(html: str) -> List[FLProject]:
    soup = BeautifulSoup(html, "html.parser")
    projects = []

    # FL.ru использует div с классом project
    cards = soup.select("div.b-post.b-post_regular") or soup.select("[id^='project-']") or soup.select("div.project")

    for card in cards:
        try:
            p = _parse_fl_card(card)
            if p:
                projects.append(p)
        except Exception as e:
            logger.debug(f"FL card error: {e}")

    logger.info(f"FL.ru: parsed {len(projects)} projects")
    return projects


def _parse_fl_card(card) -> Optional[FLProject]:
    # ID
    project_id = card.get("id", "").replace("project-", "") or card.get("data-id", "")

    # Title + URL
    title_el = card.select_one("h2 a, h3 a, .b-post__title a, a.b-post__link")
    if not title_el:
        return None
    title = title_el.get_text(strip=True)
    href = title_el.get("href", "")
    url = BASE_URL + href if href.startswith("/") else href

    if not project_id:
        import hashlib
        project_id = "fl_" + hashlib.md5(title.encode()).hexdigest()[:10]

    # Description
    desc_el = card.select_one("div.b-post__body, div.b-post__text, p")
    description = desc_el.get_text(strip=True) if desc_el else ""

    # Budget
    budget_el = card.select_one("[class*='price'], [class*='budget'], .b-post__price")
    budget_raw = budget_el.get_text(strip=True) if budget_el else "не указан"
    digits = re.sub(r"[^\d]", "", budget_raw)
    budget = int(digits) if digits else 0

    return FLProject(
        project_id=str(project_id),
        title=title,
        description=description,
        budget=budget,
        budget_raw=budget_raw,
        url=url,
    )
