"""
SQLite storage:
- seen_projects: чтобы не присылать одно и то же дважды
- drafts: сохранённые черновики ответов
"""
import sqlite3
import logging
from pathlib import Path
from datetime import datetime

DB_PATH = "./data/monitor.db"
logger = logging.getLogger(__name__)


def _conn() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS seen_projects (
                project_id  TEXT PRIMARY KEY,
                title       TEXT,
                budget      INTEGER,
                seen_at     TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS drafts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id  TEXT NOT NULL,
                draft_text  TEXT NOT NULL,
                status      TEXT DEFAULT 'pending',  -- pending / sent / skipped
                created_at  TEXT DEFAULT (datetime('now'))
            );
        """)
    logger.info("DB initialized")


def is_seen(project_id: str) -> bool:
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM seen_projects WHERE project_id = ?", (project_id,)
        ).fetchone()
    return row is not None


def mark_seen(project_id: str, title: str, budget: int):
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO seen_projects (project_id, title, budget) VALUES (?,?,?)",
            (project_id, title, budget),
        )


def save_draft(project_id: str, draft_text: str) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO drafts (project_id, draft_text) VALUES (?,?)",
            (project_id, draft_text),
        )
        return cur.lastrowid


def get_draft(draft_id: int):
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
    return dict(row) if row else None


def update_draft_status(draft_id: int, status: str):
    with _conn() as c:
        c.execute(
            "UPDATE drafts SET status = ? WHERE id = ?", (status, draft_id)
        )


def get_stats() -> dict:
    with _conn() as c:
        total_seen = c.execute("SELECT COUNT(*) FROM seen_projects").fetchone()[0]
        sent = c.execute("SELECT COUNT(*) FROM drafts WHERE status='sent'").fetchone()[0]
        skipped = c.execute("SELECT COUNT(*) FROM drafts WHERE status='skipped'").fetchone()[0]
    return {"total_seen": total_seen, "sent": sent, "skipped": skipped}
