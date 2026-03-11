"""
Kwork Auto-Responder — авторизация и автоматическая подача откликов на проекты.

Использует сессионные куки Kwork через requests.
Нужны переменные: KWORK_EMAIL, KWORK_PASSWORD
"""
import logging
import os
import time
import random
import re
import requests

logger = logging.getLogger(__name__)

KWORK_BASE = "https://kwork.ru"

_session: requests.Session | None = None
_session_expires: float = 0
_SESSION_TTL = 3600 * 5  # Обновляем сессию каждые 5 часов


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://kwork.ru/",
        "Origin": "https://kwork.ru",
    })
    return s


def _get_token(session: requests.Session) -> str:
    """Получаем CSRF-токен с главной страницы."""
    try:
        r = session.get(f"{KWORK_BASE}/", timeout=10)
        # Ищем _token или csrf в HTML/cookies
        m = re.search(r'"_token"\s*:\s*"([^"]+)"', r.text)
        if m:
            return m.group(1)
        m = re.search(r'csrf[_-]token["\s]+content="([^"]+)"', r.text, re.IGNORECASE)
        if m:
            return m.group(1)
        # Из куки
        csrf = session.cookies.get("XSRF-TOKEN", "")
        return csrf
    except Exception as e:
        logger.warning(f"Could not get CSRF token: {e}")
        return ""


def login() -> bool:
    """Авторизация на Kwork. Возвращает True при успехе."""
    global _session, _session_expires

    email = os.getenv("KWORK_EMAIL", "")
    password = os.getenv("KWORK_PASSWORD", "")

    if not email or not password:
        logger.warning("KWORK_EMAIL / KWORK_PASSWORD not set — auto-respond disabled")
        return False

    _session = _make_session()
    token = _get_token(_session)

    try:
        payload = {
            "login": email,
            "password": password,
            "reauth": "1",
        }
        if token:
            payload["_token"] = token

        r = _session.post(
            f"{KWORK_BASE}/api/login",
            json=payload,
            timeout=15,
        )

        if r.status_code == 200:
            data = r.json()
            # Kwork возвращает {"success": 1, "response": {...}} при успехе
            if data.get("success") or data.get("status") == "success":
                _session_expires = time.time() + _SESSION_TTL
                logger.info(f"Kwork login OK: {email}")
                return True
            else:
                logger.error(f"Kwork login failed: {data}")
                return False
        else:
            # Пробуем альтернативный endpoint
            r2 = _session.post(
                f"{KWORK_BASE}/login",
                data={"login": email, "password": password},
                allow_redirects=True,
                timeout=15,
            )
            # Если попали на /projects или / — залогинились
            if "/projects" in r2.url or r2.url.rstrip("/") == KWORK_BASE:
                _session_expires = time.time() + _SESSION_TTL
                logger.info(f"Kwork login OK (redirect): {email}")
                return True
            logger.error(f"Kwork login HTTP {r.status_code}")
            return False

    except Exception as e:
        logger.error(f"Kwork login error: {e}")
        return False


def _ensure_session() -> bool:
    """Проверяем сессию, при необходимости перелогиниваемся."""
    global _session, _session_expires
    if _session is None or time.time() > _session_expires:
        return login()
    return True


def submit_response(project_id: str, draft_text: str) -> bool:
    """
    Подать отклик на проект Kwork.
    project_id — числовой ID из URL /projects/{id}/...
    Возвращает True при успехе.
    """
    if not _ensure_session():
        return False

    # Небольшая случайная пауза — выглядим как человек
    time.sleep(random.uniform(60, 180))

    try:
        # Endpoint 1: REST API
        r = _session.post(
            f"{KWORK_BASE}/api/wants/{project_id}/kwork",
            json={"description": draft_text},
            timeout=20,
        )
        if r.status_code in (200, 201):
            data = r.json()
            if data.get("success") or data.get("status") == "success":
                logger.info(f"Response submitted to project {project_id}")
                return True
            logger.warning(f"Kwork API response: {data}")

        # Endpoint 2: Альтернативный формат
        r2 = _session.post(
            f"{KWORK_BASE}/api/projects/{project_id}/respond",
            json={"text": draft_text},
            timeout=20,
        )
        if r2.status_code in (200, 201):
            data2 = r2.json()
            if data2.get("success") or data2.get("status") == "success":
                logger.info(f"Response submitted (endpoint 2) to project {project_id}")
                return True

        logger.error(
            f"submit_response failed for {project_id}: "
            f"ep1={r.status_code} ep2={r2.status_code}"
        )
        return False

    except Exception as e:
        logger.error(f"submit_response error: {e}")
        return False


def revoke_response(project_id: str) -> bool:
    """Отозвать отклик на проект (если прошло мало времени)."""
    if not _ensure_session():
        return False
    try:
        r = _session.delete(
            f"{KWORK_BASE}/api/wants/{project_id}/kwork",
            timeout=15,
        )
        if r.status_code in (200, 204):
            logger.info(f"Response revoked for project {project_id}")
            return True
        logger.warning(f"revoke_response HTTP {r.status_code} for {project_id}")
        return False
    except Exception as e:
        logger.error(f"revoke_response error: {e}")
        return False


def is_available() -> bool:
    """Проверить, настроены ли и работают ли креды Kwork."""
    return bool(os.getenv("KWORK_EMAIL")) and bool(os.getenv("KWORK_PASSWORD"))
