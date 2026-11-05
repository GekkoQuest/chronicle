"""Evidence-bounded answers for facts that do not live in rate tables.

Intent handlers select and shorten source claims. They never supply institution
names, monetary values, branch addresses, URLs or dates from program constants.
The generic fallback requires meaningful topic coverage and returns an excerpt.
"""
from __future__ import annotations

from dataclasses import asdict
import re
from urllib.parse import urlsplit

from .models import Answer, Conflict, Decision, Passage
from .query import Query
from .store import Store
from .text import clean, parse_date, tokens


def _sentences(text: str) -> list[str]:
    # Avoid splitting decimal rates, initials, phone numbers and a.m./p.m.
    return re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])|\s+-\s+(?=[A-Z0-9])", text.strip("- "))


def _publish(decision: Decision, selected: list[Passage], text: str, reason: str,
             confident: bool = True) -> Decision:
    sources = {}
    for passage in selected:
        sources[(passage.evidence.url, passage.evidence.quote)] = passage.evidence.source()
    decision.answer = Answer(text, list(sources.values()), confident)
    decision.selected = [{"text": p.text, "evidence": asdict(p.evidence)} for p in selected]
    decision.reasons.append(reason)
    return decision


def _rank(p: Passage, score: float) -> tuple:
    return (p.evidence.authority, p.evidence.published_on or "0000-00-00", score, p.evidence.url)


def _branch_name(question: str) -> str | None:
    match = re.search(r"(?:the|of|for)\s+(?:the\s+)?([a-z][a-z '-]{1,40}?)\s+branch\b", question, re.I)
    if match:
        value = re.split(r"\b(?:the|of|for|at)\s+", match[1].strip().lower())[-1]
        if value not in ("all", "nearest", "closest", "local", "your"):
            return value
    match = re.search(r"\b([a-z]+-[a-z]+)\s+branch\b", question, re.I)
    return match[1].lower() if match else None


def _hours(store: Store, query: Query, snapshot: dict, decision: Decision) -> Decision:
    mode = "drive" if re.search(r"drive|drive-through", query.text, re.I) else "lobby"
    branch = _branch_name(query.text)
    hits = store.search(snapshot["id"], (branch or "") + " " + mode + " hours Monday Friday Saturday", 160)
    candidates = []
    for p, score in hits:
        context = (p.evidence.heading + " " + p.evidence.title + " " + p.text).lower()
        if not re.search(r"\d(?::\d{2})?\s*[ap]\.?(?:m|\.m)", p.text, re.I):
            continue
        if mode not in context:
            continue
        if branch and not all(t in context for t in tokens(branch)):
            continue
        candidates.append((p, score))
    if not candidates:
        return _publish(decision, [], "I can't find a grounded schedule for that branch in the available snapshot. Please contact the credit union.", "No matching branch schedule was found.", False)
    candidates.sort(key=lambda item: _rank(*item), reverse=True)
    first = candidates[0][0]
    blocks = store.passages_for(snapshot["id"], first.evidence.url)
    schedule = [p for p in blocks if mode in p.evidence.heading.lower() and re.search(r"Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday", p.text, re.I)]
    if not schedule:
        schedule = [first]
    text = " ".join(p.text for p in schedule[:7])
    if branch:
        prefix = f"The published schedule for {branch.title()}: "
    else:
        prefix = "Hours vary by branch. One published " + ("drive-up" if mode == "drive" else "lobby") + " schedule is: "
    if first.evidence.published_on:
        suffix = " This schedule comes from a dated branch announcement; confirm current hours with the branch."
    else:
        suffix = ""
    if not branch:
        suffix += " Which branch would you like?"
    if re.search(r"all|same", query.text, re.I):
        others = [p for p, _ in candidates if p.evidence.url != first.evidence.url and p.text not in text]
        if others:
            other = others[0]
            schedule.append(other)
            text += " Another published schedule lists: " + other.text
    return _publish(decision, schedule, prefix + text + suffix, "Selected an explicit schedule and preserved its branch/date limitations.")


def answer_narrative(store: Store, query: Query, snapshot: dict, decision: Decision) -> Decision:
    q = clean(query.text).lower()
    if re.search(r"lobby|drive.up|drive-through", q) and re.search(r"hour|open|close", q):
        return _hours(store, query, snapshot, decision)
    expansions = []
    intent = "excerpt"
    required = None
    if "routing" in q:
        intent, required = "routing", r"routing.{0,35}\b\d{9}\b"
        expansions = ["routing number"]
    elif re.search(r"how many.*branch|number of.*branch", q):
        intent, required = "branch_count", r"\b\d+\s+branch (?:office )?locations\b"
        expansions = ["branch office locations"]
    elif re.search(r"forbes|recognition|awards?", q):
        intent, required = "recognition", r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+\w*)\s+time|recognized|named|ranked"
        expansions = ["recognized time Forbes"] if "forbes" in q else ["recognition awards"]
    elif "volunteer" in q and ("employee" in q or "time off" in q):
        intent, required = "volunteering", r"\d+.*employees used.*(?:benefit|volunteer)"
        expansions = ["employees used Volunteer Time Off"]
    elif re.search(r"eligible|who.*(?:join|member)|membership.*requirements", q):
        intent, required = "membership", r"live or work|charter area|eligible to join|field of membership"
        expansions = ["eligible join charter area live work"]
    elif re.search(r"update|change", q) and re.search(r"contact|address|phone|email", q):
        intent, required = "contact_update", r"(?:update|change).{0,40}(?:contact information|address|phone number|email address)|contact information.{0,40}(?:update|change)"
        expansions = ["update contact information Shine call"]
    elif "address" in q and "branch" in q:
        intent, required = "address", r"\b\d+\s+[A-Za-z].*\b\d{5}\b"
        expansions = [_branch_name(query.text) or "branch address"]
    elif "branch" in q and re.search(r"close|still open|full.service|members go", q):
        intent = "closure_alternative" if re.search(r"members go|full.service|where", q) else "closure"
        required = r"full.service branches|same services" if intent == "closure_alternative" else r"(?:will |scheduled to )?close[sd]?\b.*\d{4}"
        expansions = [(_branch_name(query.text) or "") + " branch closure close"]
    elif "wallet" in q or re.search(r"apple pay|google pay|samsung pay", q):
        intent, required = "wallet", r"Apple Pay.*Google Pay.*Samsung Pay"
        expansions = ["Apple Pay Google Pay Samsung Pay"]
    elif "student" in q or "university" in q:
        intent, required = "student", r"student loan|flexible.*(?:terms|repayment)"
        expansions = ["student loans flexible repayment origination"]
    elif re.search(r"fee|cost|charge|how much", q) and re.search(r"card|replacement|overdraft|wire|atm|stop payment|check", q):
        intent, required = "fee", r"\$\s*\d|no fee|no charge|free"
        expansions = ["replacement" if "lost" in q else "", "fee charge"]
    elif re.search(r"home\$?tart|down payment assistance", q):
        intent, required = "assistance", r"\$[\d,]+.*(?:assistance|down payment|closing costs)"
        expansions = ["Home$tart down payment closing costs assistance"]
    elif query.kind == "link":
        intent = "link"
        expansions = ["Become a member"] if "join" in q else ["branch ATM locator contact"] if "locator" in q else ["certificate savings rates"]
    decision.intent["narrative_intent"] = intent
    hits = store.search(snapshot["id"], " ".join([query.text, *expansions]), 160)
    candidates = []
    branch = _branch_name(query.text)
    for passage, score in hits:
        if required and not re.search(required, passage.text, re.I):
            continue
        context = clean(passage.evidence.title + " " + passage.evidence.heading + " " + passage.text).lower()
        if branch and intent in ("address", "closure", "closure_alternative") and not all(word in context for word in tokens(branch)):
            continue
        if intent == "recognition" and "forbes" in q and "forbes" not in passage.text.lower():
            continue
        if intent == "student" and "student" not in context:
            continue
        if intent == "assistance" and re.search(r"home\$?tart", q) and not re.search(r"home\$?tart", context):
            continue
        if intent == "address":
            institution = urlsplit(passage.evidence.url).hostname.removeprefix("www.").split(".")[0]
            section = passage.evidence.heading.split(" > ")[-1].lower()
            if institution not in section and (not branch or branch not in section):
                continue
        if intent == "fee":
            subjects = [word for word in ("debit", "credit", "overdraft", "wire", "atm", "replacement", "stop payment") if word in q]
            if "lost" in q:
                subjects.append("replacement")
            if subjects and not all(word in context for word in subjects):
                continue
        # Specific-topic evidence is ranked first; publication time resolves
        # equally authoritative statements, never the crawl timestamp.
        candidates.append((passage, score))
    candidates.sort(key=lambda item: _rank(*item), reverse=True)
    if intent == "address":
        candidates.sort(key=lambda item: ("contact" in item[0].evidence.url or "location" in item[0].evidence.url, _rank(*item)), reverse=True)
    if not candidates:
        return _publish(decision, [], "I can't find that information in the available snapshot. Please contact the credit union.", "No passage satisfied the requested subject and evidence requirements.", False)
    first = candidates[0][0]
    chosen = [first]
    text = first.text
    if intent in ("routing", "branch_count"):
        pattern = r"routing.{0,35}\b(\d{9})\b" if intent == "routing" else r"\b(\d+)\s+branch (?:office )?locations\b"
        leader_rank = _rank(first, 0)[:2]
        peers = [p for p, _ in candidates if _rank(p, 0)[:2] == leader_rank]
        values = {re.search(pattern, p.text, re.I)[1] for p in peers}
        if len(values) > 1:
            return _publish(decision, peers[:5], "I can't confirm that reliably because equally authoritative sources disagree. Please contact the credit union.", "Unresolved disagreement between equally ranked sources.", False)
    if intent == "routing":
        match = re.search(r"routing.{0,35}\b(\d{9})\b", text, re.I)
        text = "The published routing number is " + match[1] + "."
    elif intent == "branch_count":
        match = re.search(required, text, re.I)
        text = "The latest applicable source lists " + match[0] + "."
    elif intent == "recognition":
        specific = [(p, s) for p, s in candidates if re.search(r"\btime\b", p.text, re.I)]
        if specific:
            first = specific[0][0]; chosen = [first]
            text = " ".join(s for s in _sentences(first.text) if re.search(r"recognized|named|time", s, re.I))
    elif intent == "volunteering":
        chosen = [p for p, _ in candidates[:2]]
        facts = []
        reported_years = set()
        for p in chosen:
            match = re.search(r"(\d+)\s+[^.]*?employees used.*?\b(20\d{2})\b", p.text, re.I)
            if match:
                reported_years.add(int(match[2]))
                facts.append(f"The published report for {match[2]} says {match[1]} employees used Volunteer Time Off.")
        text = " ".join(facts) if facts else first.text
        if "last year" in q:
            requested_year = int((query.as_of or snapshot["taken_on"])[:4]) - 1
            if requested_year not in reported_years:
                return _publish(decision, chosen, "I can't confirm the count for last year from these reports. " + text,
                                "The reports name their reporting years; none supports the relative year requested.", False)
    elif intent == "membership":
        geography = store.search(snapshot["id"], "membership charter Iowa Illinois Wisconsin counties", 100)
        supplements = [p for p, _ in geography if all(state in p.text for state in ("Iowa", "Illinois", "Wisconsin")) and re.search(r"serves residents|membership|live or work", p.text, re.I)]
        if supplements:
            supplement = max(supplements, key=lambda p: _rank(p, 0))
            chosen.append(supplement)
            sentence = next((s for s in _sentences(supplement.text) if all(state in s for state in ("Iowa", "Illinois", "Wisconsin"))), "")
            text += " " + sentence
    elif intent == "contact_update":
        detailed = [(p, s) for p, s in candidates if re.search(r"call|log in|Shine|sign in", p.text, re.I)]
        if detailed:
            first = detailed[0][0]; chosen = [first]; text = first.text
        else:
            phones = store.search(snapshot["id"], "contact call phone", 100)
            phone_passages = [p for p, _ in phones if re.search(r"\b(?:800|888|877|866|855|844|833)[- ]\d{3}[- ]\d{4}\b", p.text) and "contact" in p.evidence.url]
            if phone_passages:
                phone = phone_passages[0]; chosen.append(phone)
                number = re.search(r"\b(?:800|888|877|866|855|844|833)[- ]\d{3}[- ]\d{4}\b", phone.text)[0]
                text = "For help updating your contact information, please call " + number + "."
    elif intent == "closure":
        text = next((s for s in _sentences(first.text) if re.search(required, s, re.I)), first.text)
        event_date = parse_date(text)
        if event_date and event_date <= (query.as_of or snapshot["taken_on"]):
            text = "The published announcement scheduled this branch to close on " + re.search(r"(?:close|closed)\s+(.+?)(?:\.|$)", text, re.I)[1] + "."
    elif intent == "student":
        complete = [p for p, _ in candidates if "student loan" in p.text.lower() and "flexible" in p.text.lower()]
        if complete:
            chosen = [complete[0]]; text = complete[0].text
        else:
            text = "Student loans: " + text
    elif intent == "link":
        if query.family == "certificate":
            offers = [o for o in store.offers(snapshot["id"], "certificate") if o.dimensions.get("audience") == "personal"]
            if offers:
                offer = max(offers, key=lambda o: (o.evidence.authority, o.effective_on or "", o.evidence.self_links))
                decision.answer = Answer("The published certificate rates are at " + offer.evidence.url + "#rates", [offer.evidence.source()])
                decision.selected = [offer.to_dict()]
                return decision
        links = []
        for p, score in candidates:
            for label, url in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", p.evidence.quote):
                if "join" in q:
                    relevant = bool(re.search(r"become.*member|join|open.*account", label, re.I))
                elif "locator" in q:
                    relevant = bool(re.search(r"branch|location|ATM|find us", label, re.I))
                else:
                    relevant = "rate" in label.lower() and "savings" in url
                if relevant:
                    links.append((p, url, score))
        if links:
            if "join" in q:
                links.sort(key=lambda v: (bool(re.search(r"join\.|apply\.|application", v[1])), _rank(v[0], v[2])), reverse=True)
            else:
                links.sort(key=lambda v: _rank(v[0], v[2]), reverse=True)
            chosen = [links[0][0]]; text = "Use " + links[0][1]
        elif query.family == "certificate":
            offers = store.offers(snapshot["id"], "certificate")
            if offers:
                offer = max(offers, key=lambda o: (o.evidence.authority, o.effective_on or ""))
                decision.answer = Answer("The published certificate rates are at " + offer.evidence.url + "#rates", [offer.evidence.source()])
                decision.selected = [offer.to_dict()]
                return decision
        else:
            return _publish(decision, [], "I can't find a verified link for that request.", "No relevant source link was found.", False)
    elif intent == "excerpt":
        question_words = set(tokens(q))
        candidates = [(p, score) for p, score in candidates if len(question_words & set(tokens(p.text + " " + p.evidence.heading))) >= min(3, max(1, len(question_words) // 2))]
        if not candidates:
            return _publish(decision, [], "I can't find enough evidence to answer that reliably.", "Retrieved text did not cover the requested subject.", False)
        candidates.sort(key=lambda item: (len(question_words & set(tokens(item[0].text))), item[1], _rank(*item)), reverse=True)
        first = candidates[0][0]; chosen = [first]
        sentences = _sentences(first.text)
        sentences.sort(key=lambda s: len(question_words & set(tokens(s))), reverse=True)
        text = " ".join(sentences[:2])
        if re.search(r"rate|\bapr\b|\bapy\b", q) and not re.search(r"\d+(?:\.\d+)?%", text):
            return _publish(decision, [], "I can't find a published rate for that product. Please contact the credit union.", "A product description is not evidence of a rate.", False)
    decision.alternatives = [{"text": p.text, "evidence": asdict(p.evidence), "reason": "Other retrieved evidence; not selected."} for p, _ in candidates[:8] if p not in chosen]
    return _publish(decision, chosen, text, "Selected explicit source statements matching the requested subject; preserved their qualifiers.")


def narrative_conflicts(store: Store, snapshot: dict) -> list[Conflict]:
    """A few well-defined scalar claims; narrative differences alone are not conflicts."""
    reports = []
    patterns = [
        ("Branch office count", "branch office locations", r"\b(\d+)\s+branch (?:office )?locations\b"),
        ("Routing number", "routing number", r"routing.{0,35}\b(\d{9})\b"),
    ]
    for topic, query, pattern in patterns:
        candidates = []
        for p, score in store.search(snapshot["id"], query, 160):
            match = re.search(pattern, p.text, re.I)
            if match:
                candidates.append((p, score, match[1]))
        if len({value for _, _, value in candidates}) < 2:
            continue
        candidates.sort(key=lambda item: _rank(item[0], item[1]), reverse=True)
        best_rank = _rank(candidates[0][0], 0)[:2]
        leaders = {v for p, _, v in candidates if _rank(p, 0)[:2] == best_rank}
        representatives = {}
        for p, _, value in candidates:
            representatives.setdefault(value, p)
        values = sorted(representatives)
        reports.append(Conflict(topic, values, [representatives[v].evidence.source() for v in values],
                                next(iter(leaders)) if len(leaders) == 1 else None,
                                "Selected by source purpose and explicit publication date; a new crawl does not refresh an old claim."
                                if len(leaders) == 1 else "Equally ranked current sources disagree; no reliable winner."))
    return reports
