"""
Парсер freelance.habr.com — статичный HTML.
Одна из лучших площадок для IT-заказов в РФ.
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

BASE_URL = "https://freelance.habr.com"


@dataclass
class HabrProject:
    project_id: str
    title: str
    description: str
    budget: int
    budget_raw: str
    url: str
    source: str = "habr.freelance"


def fetch_habr_projects(page: int = 1) -> List[HabrProject]:
    """Получить список проектов с Habr Freelance."""
    url = f"{BASE_URL}/tasks?page={page}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"Habr freelance fetch error: {e}")
        return []

    return _parse_habr(r.text)


def _parse_habr(html: str) -> List[HabrProject]:
    soup = BeautifulSoup(html, "html.parser")
    projects = []

    # Habr Freelance: карточки задач
    cards = (
        soup.select("article.task")
        or soup.select("div.task")
        or soup.select("li.task")
        or soup.select("[class*='task_item']")
    )

    for card in cards:
        try:
            p = _parse_habr_card(card)
            if p:
                projects.append(p)
        except Exception as e:
            logger.debug(f"Habr card error: {e}")

    logger.info(f"Habr Freelance: parsed {len(projects)} projects")
    return projects


def _parse_habr_card(card) -> Optional[HabrProject]:
    # Title + URL
    title_el = card.select_one("h2 a, h3 a, .task__title a, a.task__title")
    if not title_el:
        return None

    title = title_el.get_text(strip=True)
    href = title_el.get("href", "")
    url = BASE_URL + href if href.startswith("/") else href

    # ID from URL
    m = re.search(r"/tasks/(\d+)", url)
    project_id = "habr_" + (m.group(1) if m else __import__("hashlib").md5(title.encode()).hexdigest()[:10])

    # Description
    desc_el = card.select_one(".task__description, .task__text, p")
    description = desc_el.get_text(strip=True) if desc_el else ""

    # Budget
    budget_el = card.select_one(".task__price, [class*='price'], [class*='budget']")
    budget_raw = budget_el.get_text(strip=True) if budget_el else "не указан"
    digits = re.sub(r"[^\d]", "", budget_raw)
    budget = int(digits) if digits else 0

    return HabrProject(
        project_id=str(project_id),
        title=title,
        description=description,
        budget=budget,
        budget_raw=budget_raw,
        url=url,
    )
