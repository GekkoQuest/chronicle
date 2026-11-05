from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
import io
import json
import socket
from threading import Thread
import unittest
from unittest.mock import patch
import zipfile

from chronicle.demo import demo_snapshots
from chronicle.engine import KnowledgeSystem
from chronicle.io import _from_zip, load_snapshots
from chronicle.models import Page, Snapshot
from chronicle.server import make_server
from tests.test_system import QUESTION, auto


class ArchiveTests(unittest.TestCase):
    def archive(self, files):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            for name, content in files.items():
                z.writestr(name, content)
        stream.seek(0)
        return zipfile.ZipFile(stream)

    def test_zip_traversal_rejected_without_extraction(self):
        for name in ("../evil.py", "/absolute.py", "C:/evil.py", "dir/../../evil.py", "..\\evil.py"):
            with self.archive({name: "pass"}) as z:
                with self.assertRaises(ValueError):
                    _from_zip(z)

    def test_manifest_escape_rejected(self):
        manifest = {"snapshot_id": "first", "taken_on": "2026-01-01", "pages": [{"url": "https://sample.example", "file": "../private.md"}]}
        with self.archive({"s/manifest.json": json.dumps(manifest), "private.md": "secret"}) as z:
            with self.assertRaises(ValueError):
                _from_zip(z)

    def test_read_archive_as_data(self):
        manifest = {"snapshot_id": "first", "taken_on": "2026-01-01", "pages": [{"url": "https://sample.example", "title": "Example", "file": "pages/a.md"}]}
        with self.archive({"s/manifest.json": json.dumps(manifest), "s/pages/a.md": "# Example\nA harmless page.", "starter/solution.py": "raise RuntimeError('must never run')"}) as z:
            snapshots = _from_zip(z)
        self.assertEqual(snapshots[0].pages[0].markdown, "# Example\nA harmless page.")


class NarrativeTests(unittest.TestCase):
    def setUp(self):
        self.ks = KnowledgeSystem()
        self.addCleanup(self.ks.close)
        for snapshot in demo_snapshots():
            self.ks.ingest(snapshot)

    def test_fictional_branch_address(self):
        answer = self.ks.answer("What is the address of the Elm Branch?")
        self.assertTrue(answer.confident)
        self.assertIn("410 Elm Street", answer.text)

    def test_unlisted_branch_hours_refuse(self):
        self.assertFalse(self.ks.answer("What are the lobby hours for the Unicorn Branch?").confident)

    def test_unknown_named_deposit_is_not_another_product(self):
        self.assertFalse(self.ks.answer("What APY does unicorn savings pay?").confident)

    def test_unknown_rate_is_not_illustrative_percentage(self):
        self.assertFalse(self.ks.answer("What is the construction loan interest rate?").confident)

    def test_historical_comparison(self):
        answer = self.ks.answer("How has the APR for a 60-month auto loan on a 2025 vehicle changed between January 2026 and May 2026?")
        self.assertIn("6.20%", answer.text)
        self.assertIn("5.40%", answer.text)
        self.assertIn("2026-01-15", answer.text)
        self.assertIn("2026-05-15", answer.text)

    def test_fee_table_retains_subject_and_amount(self):
        page = Page("https://sample.example/fees", "Fee Schedule", "# Fee Schedule\n\n| Service | Fee |\n| --- | --- |\n| Lost debit card replacement | $8.25 |\n| Wire transfer | $19.50 |", "2026-06-01")
        self.ks.ingest(Snapshot("fees", "2026-06-01", [page]))
        answer = self.ks.answer("How much is the fee for replacing a lost debit card?")
        self.assertIn("$8.25", answer.text)
        self.assertNotIn("$19.50", answer.text)

    def test_engine_requires_no_network(self):
        with patch.object(socket, "socket", side_effect=AssertionError("Unexpected network call")):
            self.ks.answer(QUESTION)
            self.ks.answer("Which student loans have flexible repayment?")
            self.ks.conflicts()

    def test_concurrent_answers_are_stable(self):
        expected = self.ks.answer(QUESTION).text
        with ThreadPoolExecutor(max_workers=8) as pool:
            answers = list(pool.map(lambda _: self.ks.answer(QUESTION).text, range(40)))
        self.assertEqual(set(answers), {expected})

    def test_changes_distinguish_removed_pages(self):
        self.ks.ingest(Snapshot("removed", "2026-06-01", [auto()]))
        self.assertTrue(self.ks.store.changes("removed")["pages_removed"])


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ks = KnowledgeSystem()
        for snapshot in demo_snapshots():
            cls.ks.ingest(snapshot)
        cls.server = make_server(cls.ks, port=0, demo=True)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.ks.close()

    def get(self, path, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_static_assets_and_security_headers(self):
        for path in ("/", "/app.js", "/style.css"):
            status, headers, body = self.get(path)
            self.assertEqual(status, 200)
            self.assertTrue(body)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_private_files_cannot_be_served(self):
        for path in ("/../solution.py", "/%2e%2e/solution.py", "/chronicle/store.py"):
            self.assertEqual(self.get(path)[0], 404)

    def test_dns_rebinding_host_rejected(self):
        self.assertEqual(self.get("/api/status", {"Host": "untrusted.example"})[0], 403)

    def test_api_errors_and_routes(self):
        self.assertEqual(self.get("/api/answer")[0], 400)
        for path in ("/api/status", "/api/answer?q=auto+loan+APR", "/api/timeline?q=auto+loan+APR", "/api/conflicts", "/api/changes"):
            status, _, body = self.get(path)
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body))


if __name__ == "__main__":
    unittest.main()
