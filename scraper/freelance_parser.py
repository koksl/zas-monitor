"""
Парсер freelance.ru/projects — статичный HTML.
"""
import hashlib
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}
BASE_URL = "https://freelance.ru"


@dataclass
class FreelanceProject:
    project_id: str
    title: str
    description: str
    budget: int
    budget_raw: str
    url: str
    source: str = "freelance.ru"
    published_at: str = ""


def fetch_freelance_projects(page: int = 1) -> List[FreelanceProject]:
    url = f"{BASE_URL}/projects/?category=all&page={page}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"Freelance.ru fetch error: {e}")
        return []
    return _parse(r.text)


def _parse(html: str) -> List[FreelanceProject]:
    soup = BeautifulSoup(html, "html.parser")
    projects = []

    cards = (
        soup.select("div.project-item")
        or soup.select("[class*='project-item']")
        or soup.select("div.b-post")
        or soup.select("article.project")
        or soup.select("li.project")
    )

    for card in cards:
        try:
            p = _parse_card(card)
            if p:
                projects.append(p)
        except Exception as e:
            logger.debug(f"Freelance card error: {e}")

    logger.info(f"Freelance.ru: parsed {len(projects)} projects (page {1})")
    return projects


def _parse_card(card) -> Optional[FreelanceProject]:
    title_el = card.select_one("h2 a, h3 a, [class*='title'] a, [class*='name'] a, a.project-title")
    if not title_el:
        return None
    title = title_el.get_text(strip=True)
    if not title:
        return None

    href = title_el.get("href", "")
    url = BASE_URL + href if href.startswith("/") else href

    project_id = card.get("data-id") or card.get("id", "").replace("project-", "")
    if not project_id:
        m = re.search(r"/projects?/(\d+)", url)
        project_id = m.group(1) if m else "fr_" + hashlib.md5(title.encode()).hexdigest()[:10]

    desc_el = card.select_one("[class*='desc'], [class*='text'], [class*='body'], p")
    description = desc_el.get_text(strip=True) if desc_el else ""

    budget_el = card.select_one("[class*='price'], [class*='budget'], [class*='cost'], [class*='salary']")
    budget_raw = budget_el.get_text(strip=True) if budget_el else "не указан"
    digits = re.sub(r"[^\d]", "", budget_raw)
    budget = int(digits) if digits else 0

    time_el = card.select_one("time, [class*='date'], [class*='ago'], [class*='time']")
    published_at = ""
    if time_el:
        published_at = time_el.get_text(strip=True) or time_el.get("datetime", "")

    return FreelanceProject(
        project_id=str(project_id),
        title=title,
        description=description,
        budget=budget,
        budget_raw=budget_raw,
        url=url,
        published_at=published_at,
    )
