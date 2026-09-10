import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from booktracker import database
from booktracker.app import app
from booktracker.config import DEAL_PAGES
from tests.test_scan import book
from tests.test_wishlist import product, URL


class ExtensionTests(unittest.TestCase):
    def test_legacy_scan_is_removed_and_pages_link_to_extension(self):
        self.assertEqual(self.client.post("/scan").status_code, 404)
        for path in ("/", "/wishlist"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'href="/extension"', response.data)
            self.assertNotIn(b'Check Kobo now', response.data)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        db_patch = patch.object(database, "DB_FILE", str(Path(directory.name) / "test.db"))
        db_patch.start()
        self.addCleanup(db_patch.stop)
        database.init_db()
        self.client = app.test_client()
        self.headers = {"Authorization": "Bearer " + app.config["EXTENSION_TOKEN"]}
        database.add_to_wishlist(book("wish", url=URL))

    def test_plan_requires_token_and_local_connection(self):
        self.assertEqual(self.client.get("/api/extension/plan").status_code, 401)
        self.assertEqual(self.client.get("/api/extension/plan", headers=self.headers, environ_overrides={"REMOTE_ADDR": "192.0.2.1"}).status_code, 403)
        self.assertEqual(self.client.get("/extension", headers={"Host": "evil.example"}).status_code, 403)
        jobs = self.client.get("/api/extension/plan", headers=self.headers).json["jobs"]
        self.assertEqual([job["kind"] for job in jobs], ["deals"] * len(DEAL_PAGES) + ["wishlist"])

    def test_plan_includes_every_wishlist_book(self):
        database.add_to_wishlist(book("second", url=URL + "-second"))
        jobs = self.client.get("/api/extension/plan", headers=self.headers).json["jobs"]
        self.assertEqual({job["id"] for job in jobs if job["kind"] == "wishlist"}, {"wish", "second"})

    def test_full_page_continues_and_repeated_page_stops(self):
        job = self.client.get("/api/extension/plan", headers=self.headers).json["jobs"][0]
        html = ''.join(f'<article><a href="/gb/en/ebook/test-{i}">Book {i}</a><span>£0.99</span></article>' for i in range(20))
        first = self.client.post("/api/extension/page", headers=self.headers, json={"url": job["url"], "html": html}).json
        self.assertEqual(first["next_job"]["url"], job["url"] + "?pageNumber=2")
        second = self.client.post("/api/extension/page", headers=self.headers, json={"url": first["next_job"]["url"], "html": html, "seen_signatures": [first["signature"]]}).json
        self.assertIsNone(second["next_job"])
        self.assertIn("repeated", second["message"])

    def test_page_limit_reports_incomplete_list(self):
        job = self.client.get("/api/extension/plan", headers=self.headers).json["jobs"][0]
        html = '<article><a href="/gb/en/ebook/test">Test</a><span>£0.99</span></article>'
        with patch("booktracker.extension_api.MAX_PAGES_PER_LIST", 1), patch("booktracker.extension_api.MIN_BOOKS_ON_FULL_DEAL_PAGE", 1):
            result = self.client.post("/api/extension/page", headers=self.headers, json={"url": job["url"], "html": html}).json
        self.assertIsNone(result["next_job"])
        self.assertIn("Page limit", result["message"])

    def test_empty_follow_on_page_finishes_without_changing_saved_books(self):
        database.save_scan([book("existing")])
        url = next(iter(DEAL_PAGES.values())) + "?pageNumber=5"
        response = self.client.post("/api/extension/page", headers=self.headers, json={
            "url": url, "html": "<html><title>99 for 99p | Kobo.com</title><body><main>No books</main></body></html>",
            "seen_signatures": ["previous-page"],
        })
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json["next_job"])
        self.assertIn("list finished", response.json["message"])
        self.assertEqual(database.get_book("existing")["is_active"], 1)

    def test_empty_first_page_challenge_and_unreadable_cards_still_fail(self):
        base = next(iter(DEAL_PAGES.values()))
        for url, html in (
            (base, '<title>Kobo</title><body>No books</body>'),
            (base + '?pageNumber=5', '<title>Just a moment...</title><body></body>'),
            (base + '?pageNumber=5', '<title>Kobo</title><body>Something went wrong</body>'),
            (base + '?pageNumber=5', '<title>Kobo</title><body><a href="/ebook/test">Book without price</a></body>'),
            (base + '?pageNumber=5', '<html></html>'),
        ):
            with self.subTest(html=html):
                response = self.client.post("/api/extension/page", headers=self.headers, json={
                    "url": url, "html": html, "seen_signatures": ["previous-page"],
                })
                self.assertEqual(response.status_code, 422)

    def test_product_page_updates_wishlist_price(self):
        response = self.client.post("/api/extension/page", headers=self.headers, json={"url": URL, "html": product()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(database.get_wishlist()[0]["current_price"], 4.99)

    def test_wrong_edition_and_challenge_preserve_price(self):
        database.save_wishlist_check("wish", 3.99)
        for html in (product(url=URL + "-wrong"), "<title>Just a moment...</title>"):
            response = self.client.post("/api/extension/page", headers=self.headers, json={"url": URL, "html": html})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(database.get_wishlist()[0]["current_price"], 3.99)

    def test_deal_page_import_does_not_end_unseen_books(self):
        database.save_scan([book("existing")])
        job = self.client.get("/api/extension/plan", headers=self.headers).json["jobs"][0]
        html = '<article><a href="/gb/en/ebook/test">Test book</a><span>£0.99</span></article>'
        response = self.client.post("/api/extension/page", headers=self.headers, json={"url": job["url"], "html": html})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Saved 1 deals", response.json["message"])
        self.assertIsNone(response.json["next_job"])
        self.assertEqual(database.get_book("existing")["is_active"], 1)

    def test_unplanned_url_is_rejected(self):
        response = self.client.post("/api/extension/page", headers=self.headers, json={"url": "https://example.com", "html": product()})
        self.assertEqual(response.status_code, 400)
