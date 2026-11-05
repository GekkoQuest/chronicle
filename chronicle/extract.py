"""Parse Markdown into table offers and bounded narrative passages.

No website-specific values or question IDs belong here. Context is carried into
each row because a number without its column, product and tier is not a fact.
"""
from __future__ import annotations

import re
from .models import Evidence, Offer, Page, Passage
from .text import clean, decimal_string, numeric_terms, parse_date, percentages, source_authority


def family_for(text: str) -> str | None:
    text = clean(text).lower()
    families = (
        ("certificate", r"certificate|\bcds?\b|term.share"),
        ("auto", r"auto|vehicle|motorcycle|\bcar loan"),
        ("mortgage", r"mortgage|home.loan|conventional|\barm\b|construction loan"),
        ("home_equity", r"home.equity|heloc|home.improvement"),
        ("card", r"credit.card|\bvisa\b"),
        ("checking", r"checking"),
        ("savings", r"saving|money.market"),
        ("recreational", r"recreational|\brv\b|boat loan"),
        ("student", r"student.loan"),
        ("personal", r"personal[ -]+loan|signature.loan|unsecured"),
    )
    for family, pattern in families:
        if re.search(pattern, text):
            return family
    return None


def _cells(line: str) -> list[str]:
    return [clean(cell) for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _interval(text: str, prefix: str) -> dict[str, str]:
    text = clean(text).lower()
    numbers = re.findall(r"\d[\d,]*(?:\.\d+)?", text)
    if not numbers:
        return {}
    values = [decimal_string(n) for n in numbers]
    if len(values) > 1:
        return {prefix + "_min": values[0], prefix + "_max": values[1]}
    if any(s in text for s in ("less", "under", "up to", "below")):
        return {prefix + "_min": "0", prefix + "_max": values[0]}
    if any(s in text for s in ("+", "over", "above", "up", "older")):
        return {prefix + "_min": values[0]}
    return {prefix + "_min": values[0], prefix + "_max": values[0]}


def _model_years(text: str) -> dict[str, str]:
    match = re.search(r"\b(20\d{2})\s*-\s*(\d{2,4})", clean(text))
    if match:
        end = match[2] if len(match[2]) == 4 else match[1][:2] + match[2]
        return {"model_min": match[1], "model_max": end}
    match = re.search(r"\b(20\d{2})", text)
    if match:
        if "older" in text.lower():
            return {"model_max": match[1]}
        return {"model_min": match[1], "model_max": match[1]}
    return {}


def _make_offer(headers: list[str], cells: list[str], context: str, evidence: Evidence,
                effective: str | None) -> Offer | None:
    if len(headers) != len(cells):
        return None
    row = dict(zip((h.lower() for h in headers), cells))
    measures: dict[str, str] = {}
    dimensions: dict[str, str] = {}
    label = cells[0]
    heading_family = next((family_for(h) for h in reversed(evidence.heading.split(" > ")) if family_for(h)), None)
    family = family_for(label) or family_for(" ".join(headers)) or heading_family or family_for(context)
    if not family:
        return None
    if re.match(r"^\$?\d", label) and re.search(r"balance|tier", headers[0], re.I):
        # Older tables omit the product column and put the product above the table.
        names = [h for h in evidence.heading.split(" > ") if re.fullmatch(r"(?:[A-Za-z0-9'-]+\s+){0,4}(?:Checking|Savings|Money Market)", h, re.I)]
        if names:
            label = names[-1]
        else:
            return None
    for header, cell in row.items():
        values = percentages(cell)
        metric = None
        if "apy" in header or "annual percentage yield" in header:
            metric = "apy"
        elif "apr" in header or "annual percentage rate" in header:
            metric = "apr"
        elif "rate" in header and values:
            metric = "rate"
        if metric and values:
            measures[metric] = "-".join(values)
        if "balance" in header or "tier" in header:
            dimensions.update(_interval(cell, "balance"))
        if "min" in header and ("earn" in header or "open" in header):
            match = re.search(r"\$?([\d,]+(?:\.\d+)?)", cell)
            if match:
                measures["minimum_" + ("earn" if "earn" in header else "open")] = decimal_string(match[1])
    if not any(k in measures for k in ("rate", "apr", "apy")):
        return None
    term_text = " ".join(c for h, c in row.items() if "term" in h or "certificate" in h or "program" in h)
    term = re.search(r"\b(\d+)\s*-?\s*(month|year)", term_text or label, re.I)
    if term:
        dimensions["term_months"] = str(int(term[1]) * (12 if term[2].lower() == "year" else 1))
    if family == "auto":
        dimensions.update(_model_years(label))
        product = "motorcycle loan" if "motorcycle" in label.lower() else "auto loan"
    elif family == "mortgage":
        dimensions.update(_interval(label[label.find("$"):], "loan") if "$" in label else {})
        dimensions["purpose"] = "refinance" if "refinance" in label.lower() else "purchase"
        product = "adjustable mortgage" if re.search(r"\barm\b|adjustable", label, re.I) else "fixed mortgage"
        arm = re.search(r"\b(\d+)/(\d+)\b", label)
        if arm:
            dimensions.update(arm_initial_years=arm[1], arm_adjustment_years=arm[2])
        program = re.search(r"\b(jumbo|fha|va|conventional)\b", label, re.I)
        if program:
            dimensions["loan_program"] = program[1].lower()
    elif family == "certificate":
        dimensions["jumbo"] = str("jumbo" in label.lower()).lower()
        dimensions["special"] = str("special" in label.lower()).lower()
        product = "certificate"
    elif family == "personal":
        product = "personal loan"
    else:
        product = clean(label).lower()
    if family in ("card", "checking", "savings", "certificate"):
        dimensions["audience"] = "business" if "business" in label.lower() else "personal"
        if "/business/" in evidence.url:
            dimensions["audience"] = "business"
    if family == "card":
        dimensions["rate_type"] = "standard"
    return Offer(family, product, dimensions, measures, evidence, effective)


def parse_page(page: Page, snapshot_id: str) -> tuple[list[Offer], list[Passage]]:
    lines = page.markdown.splitlines()
    offers: list[Offer] = []
    passages: list[Passage] = []
    headings: list[tuple[int, str]] = []
    published = None
    for line in lines[:15]:
        plain = clean(line)
        if re.fullmatch(r"(?:(?:published|posted|updated)(?: on)?\s+)?(?:[A-Za-z]+ \d{1,2},? \d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})", plain, re.I):
            published = parse_date(plain) or published
    authority = source_authority(page.url)
    self_links = page.markdown.count(page.url.rstrip("/"))

    def evidence(quote: str, line: int) -> Evidence:
        return Evidence(page.url, snapshot_id, page.fetched_at, page.title,
                        " > ".join(h for _, h in headings), quote, line, authority, published, self_links)

    i = 0
    while i < len(lines):
        line = lines[i]
        heading = re.match(r"^(#{1,6})\s+(.+)", line)
        if not heading and re.fullmatch(r"(?:[A-Za-z0-9'-]+\s+){0,4}(?:Checking|Savings|Money Market|Certificate Specials|Loan Rates)", line.strip()):
            headings = [(n, h) for n, h in headings if n < 7]
            headings.append((7, clean(line)))
            i += 1
            continue
        if not heading and re.fullmatch(r"\*\*[^*]{2,70}\*\*", line.strip()):
            headings = [(n, h) for n, h in headings if n < 7]
            headings.append((7, clean(line)))
            i += 1
            continue
        if heading:
            level = len(heading[1])
            headings = [(n, h) for n, h in headings if n < level]
            headings.append((level, clean(heading[2])))
            i += 1
            continue
        if "|" in line and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            headers = _cells(line)
            start = i
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append((i, lines[i])); i += 1
            # An effective date belongs to this table only up to the next table/heading.
            after = []
            for following in lines[i:i + 12]:
                if following.startswith("#") or "|" in following:
                    break
                after.append(following)
            effective = None
            for following in after:
                if "effective" in following.lower():
                    effective = parse_date(following)
                    if effective:
                        break
            context = " ".join([page.url, page.title, *(h for _, h in headings), *lines[max(0,start-4):start]])
            for index, raw in rows:
                quote = line + "\n" + raw
                cells = _cells(raw)
                proof = evidence(quote, index + 1)
                if len(headers) == len(cells):
                    passages.append(Passage("; ".join(f"{h}: {c}" for h, c in zip(headers, cells)), proof))
                offer = _make_offer(headers, cells, context, proof, effective)
                if offer:
                    for following in after:
                        if re.search(r"expires|valid through|ends on", following, re.I):
                            offer.expires_on = parse_date(following)
                            break
                    offers.append(offer)
            continue
        if line.strip():
            start = i
            paragraph = []
            while i < len(lines) and lines[i].strip() and not lines[i].startswith("#"):
                if "|" in lines[i] and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i+1]):
                    break
                paragraph.append(lines[i]); i += 1
            raw = "\n".join(paragraph)
            text = clean(raw)
            useful_short = bool(re.search(r"\d{3}[- ]\d{3}[- ]\d{4}|\[[^\]]+\]\(https?://", raw))
            if (len(text) >= 25 or useful_short) and not text.startswith(("Continue", "Select a Branch")):
                proof = evidence(raw, start + 1)
                passages.append(Passage(text, proof))
                # Some rate sheets are short labeled statements rather than
                # tables. Only accept a direct product/metric/value assertion;
                # illustrative payment examples are not offers.
                statement = re.fullmatch(r"(.{3,120}?)\s+(APY|APR)\s*:?\s*(\d+(?:\.\d+)?%)\s*(?:\(([^)]*)\))?\.?", numeric_terms(text), re.I)
                if statement and not re.search(r"example|illustrat|borrow", statement[1], re.I):
                    label, metric, value = statement[1], statement[2].upper(), statement[3]
                    statement_family = family_for(label)
                    if statement_family:
                        first_column = "Certificate" if statement_family == "certificate" else "Loan Program"
                        offer = _make_offer([first_column, metric], [label, value], proof.heading, proof, None)
                        if offer:
                            if statement[4] and re.search(r"ended|expired|expires|through", statement[4], re.I):
                                offer.expires_on = parse_date(statement[4])
                            offers.append(offer)
                # Card landing pages often express purchase APRs in bullets, not tables.
                # Restrict extraction to named card sections and explicit ongoing rates;
                # an introductory balance-transfer rate is a different offer.
                card_heading = next((h for _, h in reversed(headings) if re.search(r"\bVisa\b|credit card", h, re.I)), "")
                if card_heading and re.search(r"ongoing|competitive rate|standard.*APR", text, re.I):
                    rate_line = next((part for part in paragraph if re.search(r"ongoing|competitive rate", part, re.I)), "")
                    rates = percentages(rate_line)
                    if rates:
                        audience = "business" if "business" in card_heading.lower() else "personal"
                        offers.append(Offer("card", card_heading.lower(), {"audience": audience, "rate_type": "standard"},
                                            {"apr": "-".join(rates[:2])}, proof))
                if card_heading:
                    for part in paragraph:
                        if not re.search(r"intro(?:ductory)?", part, re.I):
                            continue
                        rates = percentages(part)
                        if not rates:
                            continue
                        dimensions = {"audience": "business" if "business" in card_heading.lower() else "personal", "rate_type": "introductory"}
                        if re.search(r"transfer", part, re.I):
                            dimensions["transaction"] = "balance transfer"
                        duration = re.search(r"(?:for|first)\s+(\d+)\s+months?", part, re.I)
                        if duration:
                            dimensions["duration_months"] = duration[1]
                        offers.append(Offer("card", card_heading.lower(), dimensions, {"apr": rates[0]}, proof))
            if i == start:
                i += 1
        else:
            i += 1
    return offers, passages
