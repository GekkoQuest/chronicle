"""Small, deterministic text operations; none interpret source text as instructions."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import html
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-", "’": "'", "\u00a0": " "})
STOPWORDS = set("a an the is are was were what which who how when where i me my you your our it of on in at to for from and or with as can do does please tell about have has would like know get".split())


def clean(text: str) -> str:
    text = html.unescape(unicodedata.normalize("NFKC", text)).translate(_DASHES)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?<=%)\d+\b", "", text)
    return re.sub(r"\s+", " ", text.replace("**", "").replace("\\", " ")).strip(" *_")


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", clean(text).lower()) if t not in STOPWORDS]


def numeric_terms(text: str) -> str:
    """Normalize spelled-out durations only, leaving product names untouched."""
    small = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
             "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
             "seventeen": 17, "eighteen": 18, "nineteen": 19}
    tens = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
            "seventy": 70, "eighty": 80, "ninety": 90}
    words = "|".join([*small, *tens])
    pattern = rf"\b({words})(?:[ -]({'|'.join(small)}))?(?=[ -]+(?:month|year)s?\b)"
    def replace(match):
        first, second = match[1].lower(), match[2]
        if second and first not in tens:
            return match[0]
        return str(small.get(first, tens.get(first, 0)) + (small[second.lower()] if second else 0))
    return re.sub(pattern, replace, text, flags=re.I)


def canonical_url(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme.lower(), p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), "", ""))


def decimal_string(value: str) -> str:
    try:
        return format(Decimal(value.replace(",", "").strip()).normalize(), "f")
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal: {value!r}") from exc


def percentages(text: str) -> list[str]:
    return [decimal_string(m) for m in re.findall(r"(\d+(?:\.\d+)?)\s*%", clean(text))]


def parse_date(text: str) -> str | None:
    """Accept only explicit calendar dates; never use the machine's clock."""
    text = clean(text)
    patterns = [r"\b\d{4}-\d{2}-\d{2}\b", r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
                r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+\d{1,2},?\s+\d{4}\b"]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        raw = match[0].replace(",", "").replace(".", "").replace("Sept ", "Sep ")
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(raw, fmt).date().isoformat()
            except ValueError:
                pass
    return None


def valid_iso_date(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Dates must use YYYY-MM-DD.")
    return date.fromisoformat(value).isoformat()


def source_authority(url: str) -> int:
    """A prior about document purpose, not a universal truth ranking."""
    path = urlsplit(url).path.lower()
    if "/calculators/" in path or "calculator" in path:
        return 20
    if "/learn/blog/" in path or "/blog/" in path:
        return 35
    if "/newsroom/" in path or "/news/" in path:
        return 50
    if any(part in path for part in ("/personal/", "/business/", "/rates", "/fees")):
        return 90
    return 65
