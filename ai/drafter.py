"""
Генерация персонализированного ответа на конкретный заказ через Claude API.
"""
import logging

import anthropic

import config
from scraper.kwork_parser import KworkProject

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

DRAFT_PROMPT = """Ты — опытный фрилансер на Kwork.ru. Тебе нужно написать отклик на конкретный заказ так, чтобы клиент почувствовал: ты реально прочёл его задание, а не скопировал шаблон.

МОЙ ПРОФИЛЬ (используй только то, что реально нужно под этот заказ):
{my_profile}

━━━━━━━━━━━━━━━━━
ЗАКАЗ:
Заголовок: {title}
Описание: {description}
Бюджет: {budget}
━━━━━━━━━━━━━━━━━

ПРАВИЛА написания отклика:

1. ПЕРВОЕ ПРЕДЛОЖЕНИЕ — только про задачу клиента. Перефразируй своими словами что именно он хочет, покажи что понял суть. Никаких "Здравствуйте, меня зовут...".

2. ВТОРОЕ ПРЕДЛОЖЕНИЕ — конкретное решение: какой стек ты используешь именно под эту задачу и почему. Называй конкретные технологии из профиля (например: aiogram + Claude API, или n8n + webhook, или LangChain + ChromaDB — только то, что реально подходит).

3. ТРЕТЬЕ ПРЕДЛОЖЕНИЕ — если в ТЗ есть конкретные детали (интеграция с чем-то, особое требование, платформа) — покажи что заметил это. Если деталей нет — пропусти.

4. ЧЕТВЁРТОЕ ПРЕДЛОЖЕНИЕ — срок и уверенность: "Реализую за X дней, готов приступить сразу после уточнения деталей."

5. ПЯТОЕ ПРЕДЛОЖЕНИЕ — CTA: предложи написать в переписке, задать вопросы. Без давления.

ЗАПРЕЩЕНО:
- Шаблонные фразы: "Здравствуйте", "Рад помочь", "Ваша задача интересна", "Опыт 5 лет"
- Перечислять всё из профиля подряд — только то что нужно под заказ
- Обещать невозможное или называть цену
- Длина больше 6 предложений

Язык: русский. Напиши ТОЛЬКО текст отклика."""


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
            max_tokens=600,
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
