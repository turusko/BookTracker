import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from booktracker import database
from booktracker import scraper
from booktracker import app as web
from tests.test_scan import book

URL = "https://www.kobo.com/gb/en/ebook/example"


def product(price="£4.99", old="£7.99", currency="GBP", url=URL):
    config = json.dumps(
        {
            "productType": "Book",
            "priceDetails": {
                "currency": currency,
                "displayPrice": price,
                "listPrice": old,
            },
        }
    )
    return f"""<link rel="canonical" href="{url}">
      <div data-kobo-gizmo-config='{config}'></div>
      <aside>Recommended book £0.99</aside>"""


class WishlistTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        db_patch = patch.object(
            database, "DB_FILE", str(Path(self.directory.name) / "test.db")
        )
        db_patch.start()
        self.addCleanup(db_patch.stop)
        database.init_db()
        self.book = book("wish", url=URL)
        self.client = web.app.test_client()


    def test_membership_survives_deals_and_no_duplicate(self):
        self.assertTrue(database.add_to_wishlist(self.book))
        self.assertFalse(database.add_to_wishlist(self.book))
        database.save_wishlist_check("wish", 4.99)
        database.save_scan([book("other")])
        self.assertEqual(len(database.get_wishlist()), 1)
        self.assertEqual(database.query_books("ended"), [])
        self.assertEqual(self.client.get("/?view=ended").status_code, 200)
        database.remove_from_wishlist("wish")
        self.assertEqual(database.get_wishlist(), [])
        self.assertIsNotNone(database.get_book("wish"))

    def test_drop_sale_failure_and_unchanged_price(self):
        database.add_to_wishlist(self.book)
        self.assertEqual(database.save_wishlist_check("wish", 4.99), (False, False))
        self.assertEqual(database.save_wishlist_check("wish", 0.99, 4.99), (True, True))
        database.save_wishlist_check("wish", error="Timeout")
        row = database.get_wishlist()[0]
        self.assertEqual(row["current_price"], 0.99)
        self.assertEqual(row["baseline_price"], 4.99)
        self.assertEqual(row["check_error"], "Timeout")
        response = self.client.get("/wishlist")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Saved", response.data)
        self.assertEqual(
            database.save_wishlist_check("wish", 0.99, 4.99), (False, False)
        )
        self.assertIsNone(database.get_wishlist()[0]["check_error"])
        self.assertEqual(database.save_wishlist_check("wish", 5.99), (False, False))
        self.assertFalse(database.get_wishlist()[0]["on_sale"])

    def test_product_price_is_scoped_and_validated(self):
        self.assertEqual(scraper.extract_product_price(product(), URL), (4.99, 7.99))
        self.assertEqual(
            scraper.extract_product_price(product("£0.00"), URL), (0, 7.99)
        )
        for html in (
            product(currency="USD"),
            product(url=URL + "-other"),
            "<html>Blocked</html>",
        ):
            with self.assertRaises(ValueError):
                scraper.extract_product_price(html, URL)
        with self.assertRaises(ValueError):
            scraper.wishlist_url("https://example.com/gb/en/ebook/example")


    def test_confirmation_signed_duplicate_remove_and_render(self):
        database.save_scan([self.book])
        response = self.client.post("/wishlist/search", data={"title": self.book["title"]})
        self.assertIn(b"We found this book", response.data)
        self.assertEqual(database.get_wishlist(), [])
        token = web.book_confirmation_tokens.dumps(self.book)
        self.client.post("/wishlist/add", data={"token": token})
        self.client.post("/wishlist/add", data={"token": token})
        self.assertEqual(len(database.get_wishlist()), 1)
        self.assertEqual(self.client.get("/wishlist").status_code, 200)
        self.client.post("/wishlist/add", data={"token": "tampered"})
        self.assertEqual(len(database.get_wishlist()), 1)
        self.client.post("/wishlist/remove", data={"book_id": "wish"})
        self.assertEqual(database.get_wishlist(), [])

    def test_search_deduplicates_links_but_keeps_editions(self):
        card = """<div data-ratunit="item"><h2><a data-testid="title" href="%s">Example</a></h2>
          <div data-testid="authors"><a>Author</a></div><a href="%s">ebook</a></div>"""
        html = (
            card % (URL, URL)
            + card % (URL + "?tracking=1", URL)
            + card % (URL + "-2", URL + "-2")
        )
        results = scraper.extract_search_results(html)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["author"], "Author")
        self.assertEqual(scraper.extract_search_results("<p>No results</p>"), [])
        with self.assertRaises(ValueError):
            scraper.extract_search_results("<p>Access denied</p>")

    def test_sale_only_changes_are_recorded_once(self):
        database.add_to_wishlist(self.book)
        database.save_wishlist_check("wish", 4.99)
        database.save_wishlist_check("wish", 4.99, 7.99)
        database.save_wishlist_check("wish", 4.99, 7.99)
        with database.db() as connection:
            observations = connection.execute(
                "SELECT price, old_price FROM price_history WHERE book_id = ? ORDER BY id",
                ("wish",),
            ).fetchall()
        self.assertEqual(
            [tuple(row) for row in observations], [(4.99, None), (4.99, 7.99)]
        )

    def test_shared_layout_and_styles_are_served(self):
        for page_url in ("/", "/wishlist"):
            response = self.client.get(page_url)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"/static/styles.css", response.data)
        for asset in ("styles.css", "wishlist.css", "wishlist.js"):
            with self.client.get(f"/static/{asset}") as response:
                self.assertEqual(response.status_code, 200)

    def test_product_price_falls_back_to_open_graph_metadata(self):
        html = f"""<link rel="canonical" href="{URL}">
            <meta property="og:currency_code" content="GBP">
            <meta property="og:price" content="3.990">
            <aside>Recommended book £0.99</aside>"""
        self.assertEqual(scraper.extract_product_price(html, URL), (3.99, None))
