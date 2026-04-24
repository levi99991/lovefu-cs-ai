"""
cs-handoff — Handoff 持久化層（Cloudflare D1）

生產環境：使用 Cloudflare D1（SQLite-at-edge）
開發/測試：使用 in-memory dict（零依賴）

環境變數：
  HANDOFF_STORE_BACKEND=d1|memory  (default: memory)
  CF_ACCOUNT_ID
  CF_API_TOKEN
  CF_D1_DATABASE_ID
"""
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional
from abc import ABC, abstractmethod

logger = logging.getLogger("lovefu.handoff.store")

BACKEND = os.getenv("HANDOFF_STORE_BACKEND", "memory").lower()


class HandoffStore(ABC):
    """Abstract base class for handoff persistence."""

    @abstractmethod
    def save(self, handoff: dict) -> None:
        """Save or update a handoff record."""
        pass

    @abstractmethod
    def get(self, handoff_id: str) -> Optional[dict]:
        """Retrieve a handoff by ID."""
        pass

    @abstractmethod
    def get_active_by_uid(self, line_uid: str) -> Optional[dict]:
        """Get the active handoff for a user (pending or acknowledged)."""
        pass

    @abstractmethod
    def update(self, handoff_id: str, updates: dict) -> bool:
        """Update a handoff; returns True if successful."""
        pass

    @abstractmethod
    def list_pending(self, store_id: Optional[str] = None) -> list[dict]:
        """List all pending handoffs, optionally filtered by store."""
        pass

    @abstractmethod
    def list_missed(self, hours: int = 24) -> list[dict]:
        """List all missed handoffs resolved in the past N hours."""
        pass

    @abstractmethod
    def remove_active(self, line_uid: str, handoff_id: str) -> None:
        """Remove active index for a user when handoff is resolved."""
        pass


class MemoryStore(HandoffStore):
    """In-memory implementation for dev/test."""

    def __init__(self):
        self._handoffs: dict[str, dict] = {}
        self._active_by_uid: dict[str, str] = {}

    def save(self, handoff):
        self._handoffs[handoff["handoff_id"]] = handoff
        if handoff["status"] in ("pending", "acknowledged"):
            self._active_by_uid[handoff["line_uid"]] = handoff["handoff_id"]

    def get(self, handoff_id):
        return self._handoffs.get(handoff_id)

    def get_active_by_uid(self, line_uid):
        hid = self._active_by_uid.get(line_uid)
        return self._handoffs.get(hid) if hid else None

    def update(self, handoff_id, updates):
        h = self._handoffs.get(handoff_id)
        if not h:
            return False
        h.update(updates)
        return True

    def list_pending(self, store_id=None):
        out = [h for h in self._handoffs.values() if h["status"] == "pending"]
        if store_id:
            out = [h for h in out if h.get("store_id") == store_id]
        out.sort(key=lambda h: (h.get("priority", "P1"), h.get("created_at", "")))
        return out

    def list_missed(self, hours=24):
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return [
            h
            for h in self._handoffs.values()
            if h["status"] == "missed"
            and datetime.fromisoformat(h.get("resolved_at", "2000-01-01")) > cutoff
        ]

    def remove_active(self, line_uid, handoff_id):
        if self._active_by_uid.get(line_uid) == handoff_id:
            self._active_by_uid.pop(line_uid, None)


class D1Store(HandoffStore):
    """Cloudflare D1 backend via REST API."""

    def __init__(self):
        self.account_id = os.getenv("CF_ACCOUNT_ID", "")
        self.api_token = os.getenv("CF_API_TOKEN", "")
        self.db_id = os.getenv("CF_D1_DATABASE_ID", "")
        self.base_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
            f"/d1/database/{self.db_id}"
        )
        self._ensure_table()

    def _query(self, sql, params=None):
        """Execute a D1 query via HTTP API."""
        try:
            import httpx
        except ImportError:
            logger.error("httpx not installed; D1Store requires httpx")
            return []

        r = httpx.post(
            f"{self.base_url}/query",
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
            json={"sql": sql, "params": params or []},
            timeout=10.0,
        )
        data = r.json()
        if not data.get("success"):
            logger.error(f"D1 query failed: {data}")
            return []
        results = data.get("result", [{}])
        return results[0].get("results", []) if results else []

    def _ensure_table(self):
        """Create handoffs table if not exists."""
        self._query(
            """
            CREATE TABLE IF NOT EXISTS handoffs (
                handoff_id TEXT PRIMARY KEY,
                line_uid TEXT NOT NULL,
                signal_type TEXT,
                priority TEXT DEFAULT 'P1',
                reason TEXT,
                intent TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                acknowledged_at TEXT,
                acknowledged_by TEXT,
                resolved_at TEXT,
                outcome TEXT,
                target_type TEXT,
                store_id TEXT,
                store_name TEXT,
                customer_display TEXT,
                data_json TEXT,
                INDEX idx_status (status),
                INDEX idx_uid (line_uid)
            )
            """
        )

    def _to_row(self, handoff):
        """Convert handoff dict to D1 row, storing extras in data_json."""
        core_keys = [
            "handoff_id",
            "line_uid",
            "signal_type",
            "priority",
            "reason",
            "intent",
            "status",
            "created_at",
            "acknowledged_at",
            "acknowledged_by",
            "resolved_at",
            "outcome",
            "target_type",
            "store_id",
            "store_name",
            "customer_display",
        ]
        core = {k: handoff.get(k) for k in core_keys}
        extras = {k: v for k, v in handoff.items() if k not in core_keys}
        core["data_json"] = json.dumps(extras, ensure_ascii=False, default=str)
        return core

    def _from_row(self, row):
        """Reconstruct handoff dict from D1 row."""
        h = dict(row)
        extra = json.loads(h.pop("data_json", "{}") or "{}")
        h.update(extra)
        return h

    def save(self, handoff):
        """Insert or replace a handoff."""
        row = self._to_row(handoff)
        cols = ", ".join(row.keys())
        placeholders = ", ".join(["?" for _ in row])
        self._query(
            f"INSERT OR REPLACE INTO handoffs ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )

    def get(self, handoff_id):
        """Retrieve a handoff by ID."""
        rows = self._query("SELECT * FROM handoffs WHERE handoff_id = ?", [handoff_id])
        return self._from_row(rows[0]) if rows else None

    def get_active_by_uid(self, line_uid):
        """Get active handoff (pending or acknowledged) for a user."""
        rows = self._query(
            "SELECT * FROM handoffs WHERE line_uid = ? AND status IN ('pending','acknowledged') ORDER BY created_at DESC LIMIT 1",
            [line_uid],
        )
        return self._from_row(rows[0]) if rows else None

    def update(self, handoff_id, updates):
        """Update a handoff; returns True if successful."""
        existing = self.get(handoff_id)
        if not existing:
            return False
        existing.update(updates)
        self.save(existing)
        return True

    def list_pending(self, store_id=None):
        """List pending handoffs, optionally filtered by store."""
        if store_id:
            rows = self._query(
                "SELECT * FROM handoffs WHERE status = 'pending' AND store_id = ? ORDER BY priority, created_at",
                [store_id],
            )
        else:
            rows = self._query(
                "SELECT * FROM handoffs WHERE status = 'pending' ORDER BY priority, created_at"
            )
        return [self._from_row(r) for r in rows]

    def list_missed(self, hours=24):
        """List missed handoffs resolved in the past N hours."""
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        rows = self._query(
            "SELECT * FROM handoffs WHERE status = 'missed' AND resolved_at > ? ORDER BY resolved_at DESC",
            [cutoff],
        )
        return [self._from_row(r) for r in rows]

    def remove_active(self, line_uid, handoff_id):
        """Remove active index (no-op for D1; uses query-based lookup)."""
        pass


def get_store() -> HandoffStore:
    """Factory function to get the configured store backend."""
    if BACKEND == "d1":
        return D1Store()
    return MemoryStore()


# Singleton instance
store = get_store()
