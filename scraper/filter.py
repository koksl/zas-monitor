"""
Фильтрация проектов по ключевым словам и бюджету.
"""
import logging
from typing import List

import config

logger = logging.getLogger(__name__)


def is_relevant(project) -> bool:
    """Работает с проектами из любого источника (Kwork, FL, Habr)."""
    """Вернуть True если заказ подходит нам."""
    text = f"{project.title} {project.description}".lower()

    # Проверяем стоп-слова
    for word in config.KEYWORDS_EXCLUDE:
        if word.lower() in text:
            logger.debug(f"Excluded by stop-word '{word}': {project.title[:50]}")
            return False

    # Проверяем ключевые слова (хотя бы одно)
    matched = [kw for kw in config.KEYWORDS_INCLUDE if kw.lower() in text]
    if not matched:
        return False

    # Проверяем бюджет
    if config.MIN_BUDGET > 0 and project.budget > 0:
        if project.budget < config.MIN_BUDGET:
            logger.debug(f"Budget too low ({project.budget}): {project.title[:50]}")
            return False

    logger.info(f"MATCH [{', '.join(matched[:3])}]: {project.title[:60]} | {project.budget_raw}")
    return True


def filter_projects(projects: List) -> List:
    """Отфильтровать список проектов."""
    relevant = [p for p in projects if is_relevant(p)]
    logger.info(f"Filtered: {len(relevant)}/{len(projects)} projects relevant")
    return relevant
