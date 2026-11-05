from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import random
import tempfile
import unittest

from chronicle.demo import demo_snapshots
from chronicle.engine import KnowledgeSystem
from chronicle.models import Page, Snapshot


def auto(rate="5.17", url="https://sample.example/personal/auto-loans", effective="2026-01-01", extra=""):
    return Page(url, "Auto loans", f"""# Auto Loans

| Model Year | Term | APR |
| --- | --- | --- |
| 2024-27 Model Year | 60 Months | {rate}%1 |

Rates are effective as of {effective}.
{extra}
""", "2026-01-05T00:00:00Z")


QUESTION = "What is the APR for a 60-month auto loan on a 2025 vehicle?"


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.ks = KnowledgeSystem()
        self.addCleanup(self.ks.close)

    def ingest(self, pages, sid="first", date="2026-01-05"):
        self.ks.ingest(Snapshot(sid, date, pages))

    def test_future_ingestion_does_not_rewrite_past(self):
        self.ingest([auto("5.17")])
        before = asdict(self.ks.answer(QUESTION, "2026-01-20"))
        self.ingest([auto("4.63", effective="2026-02-01")], "second", "2026-02-05")
        self.assertEqual(before, asdict(self.ks.answer(QUESTION, "2026-01-20")))
        self.assertIn("4.63%", self.ks.answer(QUESTION).text)

    def test_unknown_before_first_snapshot(self):
        self.ingest([auto()])
        self.assertFalse(self.ks.answer(QUESTION, "2025-12-31").confident)

    def test_stale_duplicate_cannot_override_product_page(self):
        self.ingest([auto("5.17"), auto("3.21", "https://sample.example/calculators/auto", "2026-01-04")])
        answer = self.ks.answer(QUESTION)
        self.assertIn("5.17%", answer.text)
        self.assertNotIn("3.21%", answer.text)
        self.assertIsNotNone(answer.conflict_note)
        self.assertTrue(any("5.17%" == c.resolution for c in self.ks.conflicts()))

    def test_equal_evidence_disagreement_abstains(self):
        self.ingest([auto("5.17"), auto("3.21", "https://sample.example/personal/auto-rates")])
        answer = self.ks.answer(QUESTION)
        self.assertFalse(answer.confident)
        self.assertTrue(answer.sources)
        self.assertIsNone(self.ks.conflicts()[0].resolution)

    def test_future_effective_offer_not_used_early(self):
        self.ingest([auto(effective="2026-02-01")])
        self.assertFalse(self.ks.answer(QUESTION).confident)
        self.assertIn("5.17%", self.ks.answer(QUESTION, "2026-02-02").text)

    def test_expired_offer_abstains(self):
        self.ingest([auto(extra="This offer expires January 04, 2026.")])
        self.assertFalse(self.ks.answer(QUESTION).confident)

    def test_removed_page_is_not_current(self):
        self.ingest([auto()])
        self.ingest([Page("https://sample.example/about", "About", "# About\nWe are a member-owned credit union.", "2026-02-01")], "second", "2026-02-01")
        self.assertFalse(self.ks.answer(QUESTION).confident)
        self.assertTrue(self.ks.answer(QUESTION, "2026-01-05").confident)

    def test_repeated_ingestion_is_idempotent(self):
        snapshot = Snapshot("first", "2026-01-05", [auto()])
        self.ks.ingest(snapshot)
        before = self.ks.explain(QUESTION).to_dict()
        self.ks.ingest(snapshot)
        self.assertEqual(before, self.ks.explain(QUESTION).to_dict())
        self.assertEqual(len(self.ks.store.snapshots()), 1)

    def test_changed_snapshot_id_rejected_atomically(self):
        self.ingest([auto()])
        with self.assertRaises(ValueError):
            self.ingest([auto("8.23")])
        self.assertIn("5.17%", self.ks.answer(QUESTION).text)

    def test_page_order_does_not_change_answer(self):
        pages = [auto(), auto("3.21", "https://sample.example/calculators/auto")]
        self.ingest(pages)
        other = KnowledgeSystem()
        self.addCleanup(other.close)
        other.ingest(Snapshot("first", "2026-01-05", list(reversed(pages))))
        self.assertEqual(asdict(self.ks.answer(QUESTION)), asdict(other.answer(QUESTION)))

    def test_vehicle_tiers_are_not_conflicts(self):
        p = auto()
        text = p.markdown.replace("Rates are", "| 2020-23 Model Year | 60 Months | 8.12% |\n\nRates are")
        # Keep the second row within the same table.
        text = text.replace("5.17%1 |\n\n|", "5.17%1 |\n|")
        self.ingest([Page(p.url, p.title, text, p.fetched_at)])
        self.assertIn("8.12%", self.ks.answer(QUESTION.replace("2025", "2022")).text)
        self.assertEqual(self.ks.conflicts(), [])

    def test_unknown_vehicle_tier_abstains(self):
        self.ingest([auto()])
        self.assertFalse(self.ks.answer(QUESTION.replace("2025", "2010")).confident)

    def test_source_quote_contains_unchanged_row_and_header(self):
        p = auto()
        self.ingest([p])
        for source in self.ks.answer(QUESTION).sources:
            self.assertEqual(source.snapshot_id, "first")
            for line in source.quote.splitlines():
                self.assertIn(line, p.markdown)

    def test_effective_date_is_not_a_publication_date(self):
        self.ingest([auto()])
        selected = self.ks.explain(QUESTION).selected[0]
        self.assertEqual(selected["effective_on"], "2026-01-01")
        self.assertIsNone(selected["evidence"]["published_on"])

    def test_random_values_are_read_from_evidence(self):
        rng = random.Random(723)
        for i in range(8):
            rate = f"{rng.randrange(2, 10)}.{rng.randrange(10, 99)}"
            sid, date = f"s{i}", f"2026-02-{i+1:02d}"
            self.ingest([auto(rate)], sid, date)
            self.assertIn(rate + "%", self.ks.answer(QUESTION).text)

    def test_paraphrases_agree(self):
        self.ingest([auto()])
        for question in ["APR for a 2025 vehicle over 60 months?", "For 60 months, what auto loan APR applies to a 2025 car?", QUESTION]:
            self.assertIn("5.17%", self.ks.answer(question).text)

    def test_spelled_out_duration(self):
        self.ingest([auto()])
        self.assertIn("5.17%", self.ks.answer("What is the APR on a sixty month auto loan for a 2025 vehicle?").text)

    def test_plaintext_rate_statements(self):
        page = Page("https://sample.example/rates/certificates", "Certificates", "# Certificates\n\nSix-month certificate APY: 3.27%\n\n12-month certificate APY: 4.31%", "2026-01-05")
        self.ingest([page])
        answer = self.ks.answer("What is the six month certificate APY?")
        self.assertIn("3.27%", answer.text)
        self.assertNotIn("4.31%", answer.text)

    def test_historical_change_is_labeled_and_consolidated(self):
        self.ingest([auto("5.17")])
        self.ingest([auto("4.63")], "second", "2026-02-01")
        conflicts = self.ks.conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertTrue(conflicts[0].reason.startswith("Historical value change"))
        self.assertEqual(conflicts[0].resolution, "4.63%")

    def test_malformed_date_is_a_controlled_refusal(self):
        self.ingest([auto()])
        for date in ("yesterday", "2026-02-30", "2026-1-1"):
            self.assertFalse(self.ks.answer(QUESTION, date).confident)

    def test_search_input_cannot_inject_sql(self):
        self.ingest([auto()])
        self.ks.answer("'; DROP TABLE offers; --")
        self.assertIn("5.17%", self.ks.answer(QUESTION).text)

    def test_empty_question_and_empty_store(self):
        self.assertFalse(self.ks.answer("").confident)
        self.assertFalse(self.ks.answer(QUESTION).confident)

    def test_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "knowledge.sqlite"
            first = KnowledgeSystem(path)
            first.ingest(Snapshot("first", "2026-01-05", [auto()]))
            before = asdict(first.answer(QUESTION))
            first.close()
            second = KnowledgeSystem(path)
            try:
                self.assertEqual(before, asdict(second.answer(QUESTION)))
            finally:
                second.close()

    def test_demo_has_temporal_answers_and_changes(self):
        for snapshot in demo_snapshots():
            self.ks.ingest(snapshot)
        self.assertIn("5.40%", self.ks.answer(QUESTION).text)
        self.assertIn("6.20%", self.ks.answer(QUESTION, "2026-01-31").text)
        self.assertEqual(len(self.ks.timeline(QUESTION)), 3)
        self.assertTrue(self.ks.store.changes("demo-may")["offer_keys_changed"])


class DepositTests(unittest.TestCase):
    def setUp(self):
        self.ks = KnowledgeSystem()
        self.addCleanup(self.ks.close)
        for snapshot in demo_snapshots():
            self.ks.ingest(snapshot)

    def test_apy_is_distinct_from_dividend_rate(self):
        q = "What is the APY for the 12-month special certificate?"
        answer = self.ks.answer(q)
        self.assertIn("3.90%", answer.text)
        self.assertIn("4.10%", answer.text)
        self.assertNotIn("3.20%", answer.text)

    def test_jumbo_qualification(self):
        answer = self.ks.answer("What is the APY for the 12-month jumbo certificate?")
        self.assertIn("4.10%", answer.text)
        self.assertNotIn("3.90%", answer.text)

    def test_balance_boundary(self):
        for balance, rate in (("9,999.99", "0.10%"), ("10,000", "0.25%")):
            answer = self.ks.answer(f"What APY does Share Savings pay on ${balance}?")
            self.assertIn(rate, answer.text)

    def test_highest_savings_yield(self):
        answer = self.ks.answer("What is the highest Share Savings APY?")
        self.assertIn("0.25%", answer.text)
        self.assertNotIn("0.10%", answer.text)

    def test_month_as_of_selects_snapshot_not_future(self):
        answer = self.ks.answer("What was the APY for a 12-month special certificate as of January 2026?")
        self.assertIn("3.40%", answer.text)
        self.assertNotIn("3.90%", answer.text)


class CardTests(unittest.TestCase):
    def setUp(self):
        self.ks = KnowledgeSystem()
        self.addCleanup(self.ks.close)
        page = Page("https://sample.example/personal/credit-cards", "Credit Cards", """# Credit Cards

### Platinum Visa

- Lower ongoing rate: 11.70%-17.30% APR.
- New cardmember offer: 1.23% intro APR on transferred balances for 6 months (after that, 11.70%-17.30% APR).
""", "2026-01-05")
        self.ks.ingest(Snapshot("first", "2026-01-05", [page]))

    def test_introductory_and_standard_apr_are_distinct(self):
        intro = self.ks.answer("What is the introductory APR for Platinum Visa?")
        standard = self.ks.answer("What is the standard purchase APR for Platinum Visa?")
        self.assertIn("1.23%", intro.text)
        self.assertIn("6 months", intro.text)
        self.assertNotIn("11.70%", intro.text)
        self.assertIn("11.70%", standard.text)
        self.assertNotIn("1.23%", standard.text)
        self.assertEqual(self.ks.conflicts(), [])

    def test_unpublished_cash_advance_apr_does_not_use_purchase_rate(self):
        self.assertFalse(self.ks.answer("What is the cash advance APR for Platinum Visa?").confident)


if __name__ == "__main__":
    unittest.main()
