"""Explicit evidence ordering. Retrieval relevance never decides financial truth."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import Offer
from .query import Query


@dataclass
class Resolution:
    winner: Offer | None
    candidates: list[Offer]
    reason: str


def includes(dimensions: dict[str, str], prefix: str, value: str) -> bool:
    n = Decimal(value)
    return (prefix + "_min" not in dimensions or n >= Decimal(dimensions[prefix + "_min"])) and (
        prefix + "_max" not in dimensions or n <= Decimal(dimensions[prefix + "_max"]))


def applicable(offer: Offer, query: Query, date: str) -> bool:
    if offer.effective_on and offer.effective_on > date:
        return False
    if offer.expires_on and offer.expires_on < date:
        return False
    if any(word not in offer.product for word in query.product_words):
        return False
    for key, value in query.constraints.items():
        interval = {"model_year": "model", "balance": "balance", "loan_amount": "loan"}.get(key)
        if interval:
            if not includes(offer.dimensions, interval, value):
                return False
        elif offer.dimensions.get(key) != value:
            return False
    return True


def priority(offer: Offer) -> tuple[int, str]:
    # Fetching an old document today must not make its claims newer.
    return offer.evidence.authority, offer.effective_on or "0000-00-00"


def resolve(candidates: list[Offer]) -> Resolution:
    ordered = sorted(candidates, key=lambda o: (priority(o), o.evidence.url, o.id), reverse=True)
    if not ordered:
        return Resolution(None, [], "No applicable published offer was found.")
    best = priority(ordered[0])
    leaders = [o for o in ordered if priority(o) == best]
    metrics = {m for o in leaders for m in o.measures if m in ("rate", "apr", "apy")}
    disagreement = any(len({o.measures[m] for o in leaders if m in o.measures}) > 1 for m in metrics)
    if disagreement:
        return Resolution(None, ordered, "Equally authoritative evidence with the same effective date disagrees.")
    winner = sorted(leaders, key=lambda o: (-len(o.measures), -o.evidence.self_links, len(o.evidence.url), o.evidence.url, o.id))[0]
    if any(o.measures != winner.measures for o in ordered):
        reason = "Selected the source with higher authority for this product, then the later explicit effective date."
    else:
        reason = "Applicable sources agree on the published value."
    return Resolution(winner, ordered, reason)


def comparable(a: Offer, b: Offer) -> bool:
    """Comparable tiers may overlap after model-year ranges are refreshed."""
    if (a.family, a.product) != (b.family, b.product):
        return False
    if a.family != "auto":
        return a.dimensions == b.dimensions
    other_a = {k: v for k, v in a.dimensions.items() if not k.startswith("model_")}
    other_b = {k: v for k, v in b.dimensions.items() if not k.startswith("model_")}
    if other_a != other_b:
        return False
    return max(int(a.dimensions.get("model_min", 0)), int(b.dimensions.get("model_min", 0))) <= min(
        int(a.dimensions.get("model_max", 9999)), int(b.dimensions.get("model_max", 9999)))


def disagree(a: Offer, b: Offer) -> bool:
    return any(a.measures[k] != b.measures[k] for k in a.measures.keys() & b.measures.keys() if k in ("rate", "apr", "apy"))


def group_for_query(offers: list[Offer], query: Query) -> list[list[Offer]]:
    groups: dict[tuple, list[Offer]] = {}
    for offer in offers:
        dims = dict(offer.dimensions)
        if "model_year" in query.constraints:
            dims.pop("model_min", None); dims.pop("model_max", None)
        # A stated balance/loan amount selects its tier. Keep loan caps distinct:
        # overlapping 'up to' bands are resolved separately, then choose the tightest.
        key = (offer.product, tuple(sorted(dims.items())))
        groups.setdefault(key, []).append(offer)
    return list(groups.values())
