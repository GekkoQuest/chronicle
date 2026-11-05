"""Boundary objects and the small internal vocabulary shared by the pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from typing import Any


# Use the evaluator's classes when it supplies them. Standalone use has no kit dependency.
try:
    from contract import Answer, Conflict, Page, Snapshot, Source
except ImportError:
    @dataclass(frozen=True)
    class Page:
        url: str
        title: str
        markdown: str
        fetched_at: str

    @dataclass(frozen=True)
    class Snapshot:
        snapshot_id: str
        taken_on: str
        pages: list[Page]

    @dataclass
    class Source:
        url: str
        snapshot_id: str | None = None
        quote: str | None = None

    @dataclass
    class Answer:
        text: str
        sources: list[Source] = field(default_factory=list)
        confident: bool = True
        conflict_note: str | None = None

    @dataclass
    class Conflict:
        topic: str
        values: list[str]
        sources: list[Source]
        resolution: str | None = None
        reason: str | None = None


def identity(*parts: Any) -> str:
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class Evidence:
    url: str
    snapshot_id: str
    fetched_at: str
    title: str
    heading: str
    quote: str
    line: int
    authority: int
    published_on: str | None = None
    self_links: int = 0

    def source(self) -> Source:
        return Source(url=self.url, snapshot_id=self.snapshot_id, quote=self.quote)


@dataclass
class Offer:
    """One table row, with its semantic dimensions kept separate from its measures.

    All numbers remain decimal strings. A row's identity excludes its price and
    provenance, so the resolver can compare equivalent offers across documents.
    """
    family: str
    product: str
    dimensions: dict[str, str]
    measures: dict[str, str]
    evidence: Evidence
    effective_on: str | None = None
    expires_on: str | None = None

    @property
    def key(self) -> str:
        return identity(self.family, self.product, self.dimensions)

    @property
    def id(self) -> str:
        return identity(self.key, self.measures, self.evidence.snapshot_id,
                        self.evidence.url, self.evidence.line)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> Offer:
        value = dict(value)
        value["evidence"] = Evidence(**value["evidence"])
        return cls(**value)


@dataclass(frozen=True)
class Passage:
    text: str
    evidence: Evidence


@dataclass
class Decision:
    """An inspectable result, also used by the UI; never hidden chain-of-thought."""
    question: str
    requested_date: str | None
    snapshot_id: str | None
    answer: Answer
    intent: dict = field(default_factory=dict)
    selected: list[dict] = field(default_factory=list)
    alternatives: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
