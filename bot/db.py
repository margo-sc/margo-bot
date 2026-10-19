"""Хранилище SQLite: пользователи, ярлыки, заказы, отложенные сообщения, анкеты."""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    source TEXT,
    created_at INTEGER,
    blocked INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tags (
    user_id INTEGER, tag TEXT, created_at INTEGER,
    PRIMARY KEY (user_id, tag)
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, product TEXT, amount REAL,
    status TEXT DEFAULT 'new',
    token TEXT UNIQUE,
    created_at INTEGER, paid_at INTEGER, note TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, chain TEXT, step INTEGER, run_at INTEGER,
    group_key TEXT, params TEXT, done INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_due ON jobs(done, run_at);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, anketa TEXT, payload TEXT, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS relay (
    admin_chat INTEGER, admin_msg INTEGER, user_id INTEGER,
    PRIMARY KEY (admin_chat, admin_msg)
);
CREATE TABLE IF NOT EXISTS files (
    name TEXT PRIMARY KEY, file_id TEXT
);
-- закрытые группы/каналы продуктов: ключ из content.yaml (delivery.access.group) -> чат
CREATE TABLE IF NOT EXISTS groups (
    key TEXT PRIMARY KEY, chat_id INTEGER, title TEXT, bound_at INTEGER
);
-- доступ клиента к группе продукта; expires_at NULL — навсегда
CREATE TABLE IF NOT EXISTS access (
    user_id INTEGER, product TEXT, grp TEXT,
    granted_at INTEGER, expires_at INTEGER,
    status TEXT DEFAULT 'active',      -- active | expired
    reminded INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, product)
);
CREATE INDEX IF NOT EXISTS access_due ON access(status, expires_at);
"""


def now() -> int:
    return int(time.time())


class DB:
    def __init__(self, path: Path, order_start: int = 500000):
        self.path = path
        self.order_start = order_start
        self.conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        # номера заказов начинаем с order_start, чтобы не пересечься со старыми счетами в Робокассе
        cur = await self.conn.execute("SELECT seq FROM sqlite_sequence WHERE name='orders'")
        row = await cur.fetchone()
        if row is None:
            await self.conn.execute(
                "INSERT INTO sqlite_sequence(name, seq) VALUES('orders', ?)", (self.order_start - 1,)
            )
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    # ---------- users ----------
    async def touch_user(self, uid: int, username: str | None, first_name: str | None, source: str | None) -> bool:
        """Создаёт или обновляет пользователя. Возвращает True, если пользователь новый."""
        cur = await self.conn.execute("SELECT id FROM users WHERE id=?", (uid,))
        exists = await cur.fetchone()
        if exists:
            await self.conn.execute(
                "UPDATE users SET username=?, first_name=?, blocked=0 WHERE id=?", (username, first_name, uid)
            )
        else:
            await self.conn.execute(
                "INSERT INTO users(id, username, first_name, source, created_at) VALUES(?,?,?,?,?)",
                (uid, username, first_name, source or "direct", now()),
            )
        await self.conn.commit()
        return not exists

    async def get_user(self, uid: int):
        cur = await self.conn.execute("SELECT * FROM users WHERE id=?", (uid,))
        return await cur.fetchone()

    async def set_blocked(self, uid: int) -> None:
        await self.conn.execute("UPDATE users SET blocked=1 WHERE id=?", (uid,))
        await self.conn.commit()

    async def user_ids(self, tag: str | None = None) -> list[int]:
        if tag:
            cur = await self.conn.execute(
                "SELECT u.id FROM users u JOIN tags t ON t.user_id=u.id WHERE t.tag=? AND u.blocked=0", (tag,)
            )
        else:
            cur = await self.conn.execute("SELECT id FROM users WHERE blocked=0")
        return [r[0] for r in await cur.fetchall()]

    # ---------- tags ----------
    async def add_tag(self, uid: int, tag: str) -> None:
        await self.conn.execute("INSERT OR IGNORE INTO tags VALUES(?,?,?)", (uid, tag, now()))
        await self.conn.commit()

    async def has_tag(self, uid: int, tag: str) -> bool:
        cur = await self.conn.execute("SELECT 1 FROM tags WHERE user_id=? AND tag=?", (uid, tag))
        return await cur.fetchone() is not None

    async def tags_of(self, uid: int) -> list[str]:
        cur = await self.conn.execute("SELECT tag FROM tags WHERE user_id=? ORDER BY created_at", (uid,))
        return [r[0] for r in await cur.fetchall()]

    # ---------- orders ----------
    async def create_order(self, uid: int, product: str, amount: float, note: str = "") -> dict:
        token = secrets.token_urlsafe(12)
        cur = await self.conn.execute(
            "INSERT INTO orders(user_id, product, amount, token, created_at, note) VALUES(?,?,?,?,?,?)",
            (uid, product, amount, token, now(), note),
        )
        await self.conn.commit()
        return {"id": cur.lastrowid, "token": token, "user_id": uid, "product": product, "amount": amount}

    async def order_by_token(self, token: str):
        cur = await self.conn.execute("SELECT * FROM orders WHERE token=?", (token,))
        return await cur.fetchone()

    async def order_by_id(self, oid: int):
        cur = await self.conn.execute("SELECT * FROM orders WHERE id=?", (oid,))
        return await cur.fetchone()

    async def mark_paid(self, oid: int) -> bool:
        """Отмечает заказ оплаченным. True — если это первое уведомление (чтобы не выдать дважды)."""
        cur = await self.conn.execute(
            "UPDATE orders SET status='paid', paid_at=? WHERE id=? AND status!='paid'", (now(), oid)
        )
        await self.conn.commit()
        return cur.rowcount == 1

    async def stats(self, since: int = 0) -> dict:
        res: dict = {}
        cur = await self.conn.execute("SELECT COUNT(*), SUM(blocked) FROM users WHERE created_at>=?", (since,))
        total, blocked = await cur.fetchone()
        res["users"], res["blocked"] = total or 0, blocked or 0
        cur = await self.conn.execute(
            "SELECT source, COUNT(*) FROM users WHERE created_at>=? GROUP BY source ORDER BY 2 DESC", (since,)
        )
        res["sources"] = [(r[0], r[1]) for r in await cur.fetchall()]
        cur = await self.conn.execute(
            "SELECT product, COUNT(*), SUM(amount) FROM orders WHERE status='paid' AND paid_at>=? "
            "GROUP BY product ORDER BY 3 DESC",
            (since,),
        )
        res["paid"] = [(r[0], r[1], r[2] or 0) for r in await cur.fetchall()]
        cur = await self.conn.execute(
            "SELECT u.source, COUNT(*), SUM(o.amount) FROM orders o JOIN users u ON u.id=o.user_id "
            "WHERE o.status='paid' AND o.paid_at>=? GROUP BY u.source ORDER BY 3 DESC",
            (since,),
        )
        res["paid_by_source"] = [(r[0], r[1], r[2] or 0) for r in await cur.fetchall()]
        cur = await self.conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM orders WHERE created_at>=?", (since,)
        )
        res["opened_pay"] = (await cur.fetchone())[0]
        return res

    # ---------- jobs (отложенные сообщения) ----------
    async def schedule(self, uid: int, chain: str, steps: list[tuple[int, int]], group_key: str, params: dict) -> None:
        """steps: [(step_index, run_at_unix)]. Сначала отменяет незавершённые задачи той же группы."""
        await self.conn.execute("UPDATE jobs SET done=2 WHERE user_id=? AND group_key=? AND done=0", (uid, group_key))
        await self.conn.executemany(
            "INSERT INTO jobs(user_id, chain, step, run_at, group_key, params) VALUES(?,?,?,?,?,?)",
            [(uid, chain, i, ts, group_key, json.dumps(params, ensure_ascii=False)) for i, ts in steps],
        )
        await self.conn.commit()

    async def cancel(self, uid: int, group_key: str | None = None, prefix: str | None = None) -> None:
        if group_key:
            await self.conn.execute(
                "UPDATE jobs SET done=2 WHERE user_id=? AND group_key=? AND done=0", (uid, group_key)
            )
        if prefix:
            await self.conn.execute(
                "UPDATE jobs SET done=2 WHERE user_id=? AND group_key LIKE ? AND done=0", (uid, prefix + "%")
            )
        await self.conn.commit()

    async def has_pending(self, uid: int, group_key: str) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM jobs WHERE user_id=? AND group_key=? AND done=0", (uid, group_key)
        )
        return await cur.fetchone() is not None

    async def due_jobs(self, limit: int = 50):
        cur = await self.conn.execute(
            "SELECT * FROM jobs WHERE done=0 AND run_at<=? ORDER BY run_at LIMIT ?", (now(), limit)
        )
        return await cur.fetchall()

    async def job_done(self, jid: int, status: int = 1) -> None:
        await self.conn.execute("UPDATE jobs SET done=? WHERE id=?", (status, jid))
        await self.conn.commit()

    # ---------- answers ----------
    async def save_answers(self, uid: int, anketa: str, payload: dict) -> None:
        await self.conn.execute(
            "INSERT INTO answers(user_id, anketa, payload, created_at) VALUES(?,?,?,?)",
            (uid, anketa, json.dumps(payload, ensure_ascii=False), now()),
        )
        await self.conn.commit()

    # ---------- relay (переписка через бота) ----------
    async def save_relay(self, admin_chat: int, admin_msg: int, uid: int) -> None:
        await self.conn.execute("INSERT OR REPLACE INTO relay VALUES(?,?,?)", (admin_chat, admin_msg, uid))
        await self.conn.commit()

    async def relay_user(self, admin_chat: int, admin_msg: int) -> int | None:
        cur = await self.conn.execute(
            "SELECT user_id FROM relay WHERE admin_chat=? AND admin_msg=?", (admin_chat, admin_msg)
        )
        row = await cur.fetchone()
        return row[0] if row else None

    # ---------- кэш file_id ----------
    async def file_id(self, name: str) -> str | None:
        cur = await self.conn.execute("SELECT file_id FROM files WHERE name=?", (name,))
        row = await cur.fetchone()
        return row[0] if row else None

    async def save_file_id(self, name: str, file_id: str) -> None:
        await self.conn.execute("INSERT OR REPLACE INTO files VALUES(?,?)", (name, file_id))
        await self.conn.commit()

    # ---------- закрытые группы продуктов ----------
    async def bind_group(self, key: str, chat_id: int, title: str) -> None:
        await self.conn.execute("INSERT OR REPLACE INTO groups VALUES(?,?,?,?)", (key, chat_id, title, now()))
        await self.conn.commit()

    async def group(self, key: str):
        cur = await self.conn.execute("SELECT * FROM groups WHERE key=?", (key,))
        return await cur.fetchone()

    async def group_keys_for_chat(self, chat_id: int) -> list[str]:
        cur = await self.conn.execute("SELECT key FROM groups WHERE chat_id=?", (chat_id,))
        return [r[0] for r in await cur.fetchall()]

    async def grant_access(self, uid: int, product: str, grp: str, days: int | None) -> int | None:
        """Открывает или продлевает доступ. Возвращает срок окончания (unix) или None — навсегда.
        Продление считается от текущего окончания, если доступ ещё действует."""
        t = now()
        cur = await self.conn.execute(
            "SELECT expires_at, status FROM access WHERE user_id=? AND product=?", (uid, product))
        row = await cur.fetchone()
        if not days:
            expires = None
        else:
            base = t
            if row and row["status"] == "active" and row["expires_at"] and row["expires_at"] > t:
                base = row["expires_at"]
            elif row and row["status"] == "active" and row["expires_at"] is None:
                base = None  # уже бессрочный — не урезаем
            expires = None if base is None else base + int(days) * 86400
        await self.conn.execute(
            "INSERT INTO access(user_id, product, grp, granted_at, expires_at, status, reminded) "
            "VALUES(?,?,?,?,?,'active',0) "
            "ON CONFLICT(user_id, product) DO UPDATE SET grp=excluded.grp, granted_at=excluded.granted_at, "
            "expires_at=excluded.expires_at, status='active', reminded=0",
            (uid, product, grp, t, expires),
        )
        await self.conn.commit()
        return expires

    async def has_access(self, uid: int, groups: list[str]) -> bool:
        if not groups:
            return False
        marks = ",".join("?" * len(groups))
        cur = await self.conn.execute(
            f"SELECT 1 FROM access WHERE user_id=? AND grp IN ({marks}) AND status='active' "
            f"AND (expires_at IS NULL OR expires_at>?)",
            (uid, *groups, now()),
        )
        return await cur.fetchone() is not None

    async def access_to_remind(self, before: int):
        cur = await self.conn.execute(
            "SELECT * FROM access WHERE status='active' AND reminded=0 AND expires_at IS NOT NULL "
            "AND expires_at<=? AND expires_at>?", (now() + before, now()))
        return await cur.fetchall()

    async def access_expired(self):
        cur = await self.conn.execute(
            "SELECT * FROM access WHERE status='active' AND expires_at IS NOT NULL AND expires_at<=?", (now(),))
        return await cur.fetchall()

    async def set_access(self, uid: int, product: str, **fields) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        await self.conn.execute(f"UPDATE access SET {sets} WHERE user_id=? AND product=?",
                                (*fields.values(), uid, product))
        await self.conn.commit()

    async def access_list(self, product: str | None = None):
        if product:
            cur = await self.conn.execute(
                "SELECT * FROM access WHERE product=? AND status='active' ORDER BY expires_at", (product,))
        else:
            cur = await self.conn.execute("SELECT * FROM access WHERE status='active' ORDER BY product, expires_at")
        return await cur.fetchall()
