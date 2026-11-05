"""The public knowledge system: ingestion, temporal answers and conflict inspection."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import re
from urllib.parse import urlsplit

from .models import Answer, Conflict, Decision, Offer, Snapshot
from .query import interpret
from .resolve import applicable, comparable, disagree, group_for_query, priority, resolve
from .store import Store


def _percent(value: str) -> str:
    return "–".join(format(Decimal(part), ".2f") + "%" for part in value.split("-"))


def describe(offer: Offer, metric: str | None = None) -> str:
    dims = offer.dimensions
    label = offer.product.title()
    if offer.family == "card" and dims.get("rate_type") == "introductory":
        label += " introductory"
        if dims.get("transaction"):
            label += " " + dims["transaction"]
        if dims.get("duration_months"):
            label += " (" + dims["duration_months"] + " months)"
    if offer.family == "certificate":
        label = ("Jumbo " if dims.get("jumbo") == "true" else "") + "Certificate"
        if dims.get("special") == "true":
            label += " Special"
    if "term_months" in dims:
        months = int(dims["term_months"])
        term = f"{months // 12}-year" if offer.family == "mortgage" and months % 12 == 0 else f"{months}-month"
        label = term + " " + label
    if offer.family == "mortgage":
        label += " (" + dims.get("purpose", "purchase")
        if "loan_max" in dims:
            label += f", up to ${Decimal(dims['loan_max']):,.0f}"
        label += ")"
    if "model_min" in dims or "model_max" in dims:
        label += f" (model years {dims.get('model_min', 'earlier')}–{dims.get('model_max', 'later')})"
    if "balance_min" in dims:
        low = f"${Decimal(dims['balance_min']):,.2f}"
        high = f"${Decimal(dims['balance_max']):,.2f}" if "balance_max" in dims else "and above"
        label += f" ({low}–{high})"
    # For deposits, an unqualified 'rate' answer includes the yield and dividend
    # rate explicitly. For loans, quote APR when no nominal rate is published.
    metrics = [metric] if metric and metric in offer.measures else [m for m in ("rate", "apr", "apy") if m in offer.measures]
    values = [f"{_percent(offer.measures[m])} {'dividend rate' if m == 'rate' and offer.family in ('savings','checking','certificate') else m.upper()}" for m in metrics]
    result = label + ": " + ", ".join(values)
    if offer.family == "certificate" and "minimum_earn" in offer.measures:
        result += f"; ${Decimal(offer.measures['minimum_earn']):,.0f} minimum"
    return result + "."


class KnowledgeSystem:
    def __init__(self, database: str | Path = ":memory:"):
        self.store = Store(database)

    def close(self) -> None:
        self.store.close()

    def ingest(self, snapshot: Snapshot) -> None:
        self.store.ingest(snapshot)

    def answer(self, question: str, as_of: str | None = None) -> Answer:
        return self.explain(question, as_of).answer

    def explain(self, question: str, as_of: str | None = None) -> Decision:
        if not isinstance(question, str) or not question.strip() or len(question) > 8000:
            return Decision(str(question)[:200], as_of, None, Answer("Please provide a specific question.", confident=False))
        query = interpret(question, as_of)
        try:
            snapshot = self.store.at(query.as_of)
        except ValueError:
            return Decision(question, as_of, None, Answer("I cannot confirm an answer for that date. Use YYYY-MM-DD.", confident=False))
        if not snapshot:
            return Decision(question, query.as_of, None, Answer("I don't have a snapshot from that date or earlier.", confident=False))
        if query.product_words and query.family in ("savings", "checking"):
            institution_names = {urlsplit(o.evidence.url).hostname.removeprefix("www.").split(".")[0]
                                 for o in self.store.offers(snapshot["id"], query.family)}
            query.product_words = [word for word in query.product_words if word not in institution_names]
        decision = Decision(question, query.as_of, snapshot["id"], Answer("I don't know.", confident=False), query.to_dict())
        date = query.as_of or snapshot["taken_on"]
        if query.kind == "change":
            snapshots = self.store.snapshots()
            start = self.store.at(query.compare_from) if query.compare_from else snapshots[0]
            end = self.store.at(query.compare_to or query.as_of)
            if not start or not end or start["id"] == end["id"]:
                decision.answer = Answer("I need two available snapshots to establish a change over that period.", confident=False)
                return decision
            base_question = re.sub(r"\b(?:changed?|changes|difference|compare[ds]?|since|between)\b", "", question, flags=re.I)
            if not re.search(r"rate|apr|apy|interest", base_question, re.I):
                base_question += " rate"
            before = self.explain(base_question, start["taken_on"])
            after = self.explain(base_question, end["taken_on"])
            decision.answer = Answer(
                f"As of {start['taken_on']}:\n{before.answer.text}\n\nAs of {end['taken_on']}:\n{after.answer.text}",
                before.answer.sources + after.answer.sources, before.answer.confident and after.answer.confident)
            decision.selected = before.selected + after.selected
            decision.reasons = ["Compared independently resolved answers from the two available snapshots; no intermediate effective date is inferred."]
            return decision
        if query.kind == "rate" and query.family:
            offers = [o for o in self.store.offers(snapshot["id"], query.family) if applicable(o, query, date)]
            # Where amount bands overlap, select the smallest published cap that
            # contains the requested amount, separately for purchase/refinance.
            if query.family == "mortgage":
                if "purpose" not in query.constraints:
                    offers = [o for o in offers if o.dimensions.get("purpose") == "purchase"]
                if "loan_amount" in query.constraints:
                    cap = min((Decimal(o.dimensions["loan_max"]) for o in offers if "loan_max" in o.dimensions), default=None)
                    if cap is not None:
                        offers = [o for o in offers if Decimal(o.dimensions.get("loan_max", str(cap))) == cap]
            winners = []
            unresolved = False
            for group in group_for_query(offers, query):
                resolution = resolve(group)
                decision.reasons.append(resolution.reason)
                if resolution.winner:
                    winners.append(resolution.winner)
                    decision.selected.append(resolution.winner.to_dict())
                else:
                    unresolved = True
                decision.alternatives.extend({**o.to_dict(), "reason": resolution.reason} for o in resolution.candidates if o is not resolution.winner)
            if query.superlative and winners:
                metric = query.metric or ("apy" if query.family in ("savings", "checking", "certificate") else "apr")
                measured = [o for o in winners if metric in o.measures]
                if measured:
                    value = (max if query.superlative == "max" else min)(Decimal(o.measures[metric].split("-")[-1]) for o in measured)
                    winners = [o for o in measured if Decimal(o.measures[metric].split("-")[-1]) == value]
                    decision.selected = [o.to_dict() for o in winners]
            if unresolved:
                decision.answer = Answer("I can't confirm a single reliable rate because equally authoritative sources disagree. Please contact the credit union.", confident=False,
                                         sources=[o.evidence.source() for o in offers[:4]], conflict_note="Unresolved disagreement between applicable sources.")
            elif winners:
                winners.sort(key=lambda o: (o.product, int(o.dimensions.get("term_months", 0)), o.dimensions.get("jumbo", ""), Decimal(o.dimensions.get("loan_max", "0")), Decimal(o.dimensions.get("balance_min", "0"))))
                text = "\n".join(describe(o, query.metric) for o in winners)
                sources = { (o.evidence.url, o.evidence.quote): o.evidence.source() for o in winners }
                conflicts = any(disagree(o, other) for o in winners for other in offers if comparable(o, other))
                decision.answer = Answer(text, list(sources.values()), True,
                                         "An applicable product rate source takes precedence over stale or lower-authority material." if conflicts else None)
            else:
                decision.answer = Answer("I can't find a published rate for those exact product terms in the available snapshot. Please contact the credit union.", confident=False)
            decision.reasons = list(dict.fromkeys(decision.reasons))
            return decision
        from .narrative import answer_narrative
        return answer_narrative(self.store, query, snapshot, decision)

    def conflicts(self) -> list[Conflict]:
        latest = self.store.at(None)
        if not latest:
            return []
        reports = []
        observed_on = {s["id"]: s["taken_on"] for s in self.store.snapshots()}
        current = [o for o in self.store.offers(latest["id"])
                   if (not o.effective_on or o.effective_on <= latest["taken_on"])
                   and (not o.expires_on or o.expires_on >= latest["taken_on"])]
        history = [o for s in self.store.snapshots() for o in self.store.offers(s["id"])
                   if not o.effective_on or o.effective_on <= s["taken_on"]]
        bases = {}
        for offer in sorted(current, key=lambda o: (priority(o), o.id), reverse=True):
            if offer.key in bases:
                continue
            # Old model-year ranges on calculators must not create extra copies
            # of the same contradiction for every pair of duplicated pages.
            if any(comparable(offer, b) and priority(b)[0] > priority(offer)[0] for b in bases.values()):
                continue
            bases[offer.key] = offer
        for base in bases.values():
            current_group = [o for o in current if comparable(base, o)]
            history_group = [o for o in history if comparable(base, o)]
            resolution = resolve(current_group)
            for metric in ("rate", "apr", "apy"):
                values = sorted({o.measures[metric] for o in history_group if metric in o.measures})
                if len(values) < 2:
                    continue
                current_values = {o.measures[metric] for o in current_group if metric in o.measures}
                kind = "Active source disagreement" if len(current_values) > 1 else "Historical value change"
                # Keep one representative citation for each value, preferably
                # from the newest snapshot and most authoritative source.
                representatives = {}
                for o in sorted(history_group, key=lambda o: (observed_on[o.evidence.snapshot_id], priority(o), o.evidence.self_links), reverse=True):
                    if metric in o.measures:
                        representatives.setdefault(o.measures[metric], o)
                winner = resolution.winner
                reports.append(Conflict(
                    topic=describe(base).split(":")[0] + " " + metric.upper(),
                    values=[_percent(v) for v in values],
                    sources=[representatives[v].evidence.source() for v in values],
                    resolution=_percent(winner.measures[metric]) if winner and metric in winner.measures else None,
                    reason=kind + ". " + resolution.reason + " Resolution is as of " + latest["taken_on"] +
                           ("; comparisons apply only to overlapping model-year eligibility." if base.family == "auto" else ".")))
        from .narrative import narrative_conflicts
        reports.extend(narrative_conflicts(self.store, latest))
        return sorted(reports, key=lambda c: (c.topic, c.values))

    def timeline(self, question: str) -> list[dict]:
        return [{"snapshot": snapshot, "decision": self.explain(question, snapshot["taken_on"]).to_dict()}
                for snapshot in self.store.snapshots()]
