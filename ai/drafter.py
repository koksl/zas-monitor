"""
Генерация персонализированного ответа на конкретный заказ через Claude API.
"""
import logging

import anthropic

import config
from scraper.kwork_parser import KworkProject

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

DRAFT_PROMPT = """Ты — опытный фрилансер на Kwork.ru, специализируешься на разработке ИИ-ботов и автоматизации для бизнеса.

МОЙ ПРОФИЛЬ:
{my_profile}

ЗАКАЗ КЛИЕНТА:
Заголовок: {title}
Описание: {description}
Бюджет: {budget}

ЗАДАЧА: Напиши отклик на этот заказ. Требования:
- Длина: 4-6 предложений, не больше
- Первое предложение — конкретно про задачу клиента (не шаблон "здравствуйте, меня зовут...")
- Упомяни свой конкретный стек, который подходит под этот заказ
- Укажи примерный срок (5-7 дней)
- Заканчивай мягким CTA: предложи уточнить детали в переписке
- Тон: профессиональный, деловой, без лести и пустых слов
- Язык: русский

Напиши ТОЛЬКО текст отклика, без кавычек и пояснений."""


def generate_draft(project: KworkProject) -> str:
    """Сгенерировать персонализированный отклик на заказ."""
    prompt = DRAFT_PROMPT.format(
        my_profile=config.MY_PROFILE,
        title=project.title,
        description=project.description[:1500],  # Ограничиваем длину
        budget=project.budget_raw,
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        draft = response.content[0].text.strip()
        logger.info(f"Draft generated for project {project.project_id}: {len(draft)} chars")
        return draft

    except anthropic.APIError as e:
        logger.error(f"Claude API error: {e}")
        return _fallback_draft(project)


def _fallback_draft(project: KworkProject) -> str:
    """Шаблонный ответ если API недоступен."""
    return (
        f"Добрый день! Вижу вашу задачу по теме «{project.title[:60]}» — "
        f"это именно мой стек. Разрабатываю ИИ-боты и системы автоматизации "
        f"на Python + Claude/GPT API, срок 5-7 дней. "
        f"Готов обсудить детали в переписке — напишите, что именно нужно реализовать."
    )
