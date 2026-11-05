"""Transactional snapshot storage and local full-text retrieval.

The store is private runtime data. It is deliberately separate from source code
and can be rebuilt from the supplied snapshots without a network connection.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
from threading import RLock
from urllib.parse import urlsplit

from .extract import parse_page
from .models import Evidence, Offer, Passage, Snapshot, identity
from .text import tokens, valid_iso_date


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.lock = RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY, taken_on TEXT NOT NULL UNIQUE,
                digest TEXT NOT NULL, page_count INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS pages (
                snapshot_id TEXT NOT NULL REFERENCES snapshots(id), url TEXT NOT NULL,
                digest TEXT NOT NULL, PRIMARY KEY(snapshot_id, url));
            CREATE TABLE IF NOT EXISTS offers (
                id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES snapshots(id),
                family TEXT NOT NULL, semantic_key TEXT NOT NULL, body TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS offers_snapshot ON offers(snapshot_id, family);
            CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(
                snapshot_id UNINDEXED, text, context, evidence UNINDEXED,
                tokenize='porter unicode61');
        """)
        self._offer_cache: dict[str, list[Offer]] = {}

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def ingest(self, snapshot: Snapshot) -> None:
        valid_iso_date(snapshot.taken_on)
        if not snapshot.snapshot_id or not snapshot.pages:
            raise ValueError("A snapshot needs an ID and at least one page.")
        urls = [p.url for p in snapshot.pages]
        for page in snapshot.pages:
            parsed = urlsplit(page.url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or not isinstance(page.markdown, str):
                raise ValueError("Pages need an HTTP(S) source URL and plain Markdown text.")
        if len(set(urls)) != len(urls):
            raise ValueError("Duplicate URLs in a full snapshot are ambiguous.")
        digest = identity(sorted((p.url, p.title, p.markdown, p.fetched_at) for p in snapshot.pages))
        with self.lock:
            same_day = self.db.execute("SELECT id FROM snapshots WHERE taken_on=?", (snapshot.taken_on,)).fetchone()
            if same_day and same_day["id"] != snapshot.snapshot_id:
                raise ValueError("Two different full snapshots on the same date are ambiguous.")
            existing = self.db.execute("SELECT digest, taken_on FROM snapshots WHERE id=?", (snapshot.snapshot_id,)).fetchone()
            if existing:
                if existing["digest"] == digest and existing["taken_on"] == snapshot.taken_on:
                    return
                raise ValueError("Snapshot IDs are immutable; use a new ID for different content.")
            offers = []
            passages = []
            for page in sorted(snapshot.pages, key=lambda p: p.url):
                found, blocks = parse_page(page, snapshot.snapshot_id)
                offers.extend(found)
                passages.extend(blocks)
            # Repeated navigation and boilerplate should not dominate retrieval.
            counts = Counter(p.text for p in passages)
            with self.db:
                self.db.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?)",
                                (snapshot.snapshot_id, snapshot.taken_on, digest, len(snapshot.pages)))
                self.db.executemany("INSERT INTO pages VALUES (?, ?, ?)",
                                    [(snapshot.snapshot_id, p.url, identity(p.markdown)) for p in snapshot.pages])
                self.db.executemany("INSERT OR IGNORE INTO offers VALUES (?, ?, ?, ?, ?)",
                                    [(o.id, snapshot.snapshot_id, o.family, o.key, json.dumps(o.to_dict())) for o in offers])
                seen = set()
                rows = []
                for p in passages:
                    key = (p.evidence.url, p.text)
                    if key in seen or counts[p.text] > 40:
                        continue
                    seen.add(key)
                    context = " ".join([p.evidence.title, p.evidence.heading, p.evidence.url])
                    rows.append((snapshot.snapshot_id, p.text, context, json.dumps(asdict(p.evidence))))
                self.db.executemany("INSERT INTO passages VALUES (?, ?, ?, ?)", rows)
            self._offer_cache.pop(snapshot.snapshot_id, None)

    def snapshots(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT id, taken_on, page_count FROM snapshots ORDER BY taken_on")]

    def at(self, as_of: str | None) -> dict | None:
        if as_of is not None:
            valid_iso_date(as_of)
        with self.lock:
            row = self.db.execute("SELECT id, taken_on, page_count FROM snapshots WHERE taken_on <= ? ORDER BY taken_on DESC LIMIT 1",
                                  (as_of or "9999-12-31",)).fetchone()
            return dict(row) if row else None

    def offers(self, snapshot_id: str, family: str | None = None) -> list[Offer]:
        with self.lock:
            if snapshot_id not in self._offer_cache:
                rows = self.db.execute("SELECT body FROM offers WHERE snapshot_id=? ORDER BY id", (snapshot_id,))
                self._offer_cache[snapshot_id] = [Offer.from_dict(json.loads(r[0])) for r in rows]
            return [o for o in self._offer_cache[snapshot_id] if family is None or o.family == family]

    def search(self, snapshot_id: str, query: str, limit: int = 60) -> list[tuple[Passage, float]]:
        words = sorted(set(tokens(query)))[:35]
        if not words:
            return []
        expression = " OR ".join('"' + word + '"' for word in words)
        with self.lock:
            rows = self.db.execute("""SELECT text, evidence, bm25(passages, 0, 1.0, 0.4) AS rank
                FROM passages WHERE passages MATCH ? AND snapshot_id=?
                ORDER BY rank, evidence LIMIT ?""", (expression, snapshot_id, limit)).fetchall()
        return [(Passage(r["text"], Evidence(**json.loads(r["evidence"]))), -r["rank"]) for r in rows]

    def changes(self, snapshot_id: str) -> dict:
        """Document changes and semantic offer changes, with stable identities."""
        snapshots = self.snapshots()
        position = next((i for i, s in enumerate(snapshots) if s["id"] == snapshot_id), None)
        if position is None:
            raise ValueError("Unknown snapshot.")
        previous = snapshots[position - 1]["id"] if position else None
        with self.lock:
            current_pages = dict(self.db.execute("SELECT url, digest FROM pages WHERE snapshot_id=?", (snapshot_id,)))
            old_pages = dict(self.db.execute("SELECT url, digest FROM pages WHERE snapshot_id=?", (previous,))) if previous else {}
        before: dict[str, set[str]] = {}
        after: dict[str, set[str]] = {}
        for sid, mapping in ((previous, before), (snapshot_id, after)):
            if sid:
                for offer in self.offers(sid):
                    mapping.setdefault(offer.key, set()).add(json.dumps(offer.measures, sort_keys=True))
        changed = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
        return {"snapshot_id": snapshot_id, "previous_snapshot_id": previous,
                "pages_added": sorted(current_pages.keys() - old_pages.keys()),
                "pages_removed": sorted(old_pages.keys() - current_pages.keys()),
                "pages_changed": sorted(u for u in current_pages.keys() & old_pages.keys() if current_pages[u] != old_pages[u]),
                "offers_added": len(after.keys() - before.keys()), "offers_removed": len(before.keys() - after.keys()),
                "offer_keys_changed": changed}

    def passages_for(self, snapshot_id: str, url: str) -> list[Passage]:
        with self.lock:
            rows = self.db.execute("SELECT text, evidence FROM passages WHERE snapshot_id=? AND json_extract(evidence, '$.url')=?", (snapshot_id, url)).fetchall()
        return sorted([Passage(r["text"], Evidence(**json.loads(r["evidence"]))) for r in rows], key=lambda p: p.evidence.line)
