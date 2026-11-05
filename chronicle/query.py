"""Interpret the question's constraints without consulting expected answers."""
from __future__ import annotations

from calendar import monthrange
from dataclasses import asdict, dataclass, field
from datetime import datetime
import re

from .extract import family_for
from .text import clean, decimal_string, numeric_terms, parse_date


@dataclass
class Query:
    text: str
    family: str | None = None
    kind: str = "narrative"
    metric: str | None = None
    constraints: dict[str, str] = field(default_factory=dict)
    product_words: list[str] = field(default_factory=list)
    as_of: str | None = None
    superlative: str | None = None
    compare_from: str | None = None
    compare_to: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def interpret(question: str, as_of: str | None = None) -> Query:
    q = numeric_terms(clean(question)).lower()
    result = Query(question, family_for(q), as_of=as_of)
    if as_of is None and re.search(r"\bas of\b|\bin (?:january|february|march|april|may|june|july|august|september|october|november|december) \d{4}", q):
        result.as_of = parse_date(q)
        if result.as_of is None:
            match = re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december) (\d{4})", q)
            if match:
                date = datetime.strptime(match[0], "%B %Y")
                result.as_of = date.replace(day=monthrange(date.year, date.month)[1]).date().isoformat()
    if re.search(r"\blink\b|\burl\b|website|join .*online", q):
        result.kind = "link"
    elif result.family and re.search(r"rate|\baprs?\b|\bapys?\b|interest|dividend|pay|earn|yield", q):
        result.kind = "rate"
    if result.family and re.search(r"\bchanged?\b|\bchanges\b|\bcompare\b|\bdifference\b", q):
        result.kind = "change"
        dates = []
        for match in re.finditer(r"\d{4}-\d{2}-\d{2}|(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+(?:\d{1,2},?\s+)?\d{4}", q):
            parsed = parse_date(match[0])
            if parsed is None:
                month = datetime.strptime(match[0], "%B %Y")
                parsed = month.replace(day=monthrange(month.year, month.month)[1]).date().isoformat()
            dates.append(parsed)
        if dates:
            result.compare_from = min(dates)
        if len(dates) > 1:
            result.compare_to = max(dates)
    if re.search(r"\byield\b|\bapy\b", q):
        result.metric = "apy"
    elif re.search(r"annual percentage rate|\bapr\b", q):
        result.metric = "apr"
    elif "dividend rate" in q or ("interest rate" in q and result.family not in ("certificate", "savings", "checking")):
        result.metric = "rate"
    term = re.search(r"\b(\d+)\s*-?\s*(month|year)\b", q)
    if term:
        result.constraints["term_months"] = str(int(term[1]) * (12 if term[2] == "year" else 1))
    if result.family == "auto":
        year = re.search(r"\b(20\d{2})\s+(?:model|vehicle|car)", q) or re.search(r"\b(20\d{2})\b", q)
        if year:
            result.constraints["model_year"] = year[1]
    if result.family == "certificate":
        result.constraints["audience"] = "business" if "business" in q else "personal"
        if "jumbo" in q:
            result.constraints["jumbo"] = "true"
        if "special" in q:
            result.constraints["special"] = "true"
    if result.family == "mortgage":
        if "refinanc" in q:
            result.constraints["purpose"] = "refinance"
        elif "purchase" in q or "buy" in q:
            result.constraints["purpose"] = "purchase"
        if re.search(r"\barm\b|adjustable", q):
            result.product_words = ["adjustable"]
            arm = re.search(r"\b(\d+)/(\d+)\b", q)
            if arm:
                result.constraints.update(arm_initial_years=arm[1], arm_adjustment_years=arm[2])
        program = re.search(r"\b(jumbo|fha|va|conventional)\b", q)
        if program:
            result.constraints["loan_program"] = program[1]
        if "construction" in q:
            result.product_words = ["construction"]
    if result.family == "card":
        result.constraints["rate_type"] = "introductory" if re.search(r"introductory|\bintro\b", q) else "standard"
        if "cash advance" in q:
            result.constraints["transaction"] = "cash advance"
        result.product_words = [word for word in ("platinum", "rewards") if word in q]
        if "business" in q:
            result.constraints["audience"] = "business"
        elif result.product_words or "personal" in q:
            result.constraints["audience"] = "personal"
    if result.family in ("savings", "checking"):
        result.constraints["audience"] = "business" if "business" in q else "personal"
        for name in ("share", "bright", "holiday", "health", "launchpad", "high-yield", "investor", "achieve", "you name", "free", "1st rate"):
            if name in q:
                result.product_words.extend(name.split())
        if not result.product_words:
            named = re.search(r"\b([a-z][a-z-]*)\s+savings\b", q)
            generic = {"the", "your", "my", "any", "all", "highest", "current", "for", "on", "of", "a", "do", "does"}
            if named and named[1] not in generic:
                result.product_words = [named[1]]
        # Product labels may evolve; unknown names should not silently map to Share Savings.
        if not result.product_words and result.family == "savings" and re.search(r"\bshare savings\b", q):
            result.product_words = ["share"]
    amount = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", q)
    if amount and result.family in ("savings", "checking", "mortgage"):
        result.constraints["loan_amount" if result.family == "mortgage" else "balance"] = decimal_string(amount[1])
    if re.search(r"highest|best|maximum|top", q):
        result.superlative = "max"
    elif "lowest" in q:
        result.superlative = "min"
    return result
