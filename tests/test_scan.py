import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from booktracker import database
from booktracker import scraper


def book(book_id="saved", price=2.99, **fields):
    return {
        "id": book_id,
        "title": "Original title",
        "author": "Author",
        "url": f"https://www.kobo.com/gb/en/ebook/{book_id}",
        "source": "Deals",
        "old_price": 5.99,
        "current_price": price,
        "synopsis": "",
        "genres": "",
        **fields,
    }


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_patch = patch.object(
            database, "DB_FILE", str(Path(self.directory.name) / "test.db")
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()

    def test_partial_scan_updates_found_books_without_ending_missing_books(self):
        database.save_scan([book(), book("missing")])
        database.save_scan([book(price=0.99), book("new")], complete=False)
        self.assertEqual(database.get_book("saved")["current_price"], 0.99)
        self.assertEqual(database.get_book("missing")["is_active"], 1)
        self.assertEqual(database.get_book("new")["is_active"], 1)
        database.save_scan([])
        self.assertEqual(database.get_book("missing")["is_active"], 1)

    def test_duplicate_generator_records_one_observation_and_preserves_baseline(self):
        database.save_scan(iter([book(), book(price=0.99), book()]))
        original = database.get_book("saved")
        database.save_scan([book(price=1.99)])
        updated = database.get_book("saved")
        self.assertEqual(updated["first_seen"], original["first_seen"])
        self.assertEqual(updated["first_seen_price"], 0.99)
        self.assertEqual(updated["previous_price"], 0.99)
        with database.db() as connection:
            history = connection.execute("SELECT price FROM price_history ORDER BY id").fetchall()
        self.assertEqual([row[0] for row in history], [0.99, 1.99])

    def test_wishlist_book_gets_first_deal_price(self):
        database.add_to_wishlist(book())
        database.save_scan([book(price=0.99)])
        self.assertEqual(database.get_book("saved")["first_seen_price"], 0.99)
        self.assertEqual(len(database.get_wishlist()), 1)

    def test_sale_price_changes_recorded_without_duplicate_history(self):
        database.save_scan([book()])
        database.save_scan([book(old_price=7.99)])
        database.save_scan([book(old_price=7.99)])
        with database.db() as connection:
            history = connection.execute("SELECT old_price FROM price_history ORDER BY id").fetchall()
        self.assertEqual([row[0] for row in history], [5.99, 7.99])

    def test_url_variants_share_identity(self):
        url = book()["url"]
        expected = scraper.canonical_book_id(url, "Title", "Author")
        for suffix in ("/", "?tracking=1", "#preview", "/?tracking=1#preview"):
            self.assertEqual(scraper.canonical_book_id(url + suffix, "Title", "Author"), expected)

    def test_unreadable_page_and_page_limit_report_incomplete_scan(self):
        with patch.object(scraper, "fetch_page", return_value="Blocked"):
            self.assertTrue(scraper.scrape_deal_list(Mock(), "Deals", "url")[1])
        with (
            patch.object(scraper, "MAX_PAGES_PER_LIST", 1),
            patch.object(scraper, "fetch_page", return_value="page"),
            patch.object(scraper, "extract_books", return_value=[book(str(i)) for i in range(20)]),
        ):
            books, errors = scraper.scrape_deal_list(Mock(), "Deals", "url")
        self.assertEqual(len(books), 20)
        self.assertTrue(errors)

    def test_app_preserves_missing_books_after_partial_failure(self):
        from booktracker import app as web
        database.save_scan([book("missing")])
        with patch.object(web, "scrape_all", return_value=([book("new")], ["Page failed"])):
            web.scan_deals_and_report()
        self.assertEqual(database.get_book("missing")["is_active"], 1)
        self.assertIsNotNone(database.get_book("new"))

    def test_saved_books_without_metadata_do_not_fetch_details(self):
        database.save_scan([book()])
        session = Mock()
        session.get.return_value.text = "<html></html>"
        books = [book(), book("new")]
        with patch.object(scraper, "MAX_DETAIL_FETCHES_PER_SCAN", 1):
            enriched, errors = scraper.enrich_missing_metadata(session, books)
        self.assertEqual((enriched, errors), (1, []))
        self.assertEqual(session.get.call_args.args, (books[1]["url"],))
        self.assertEqual(session.get.call_count, 1)

    def test_repeat_scan_preserves_details_and_price_drop(self):
        database.save_scan([book(synopsis="Saved synopsis", genres="Fantasy")])
        database.save_scan([book(price=0.99, title="Changed card title")])
        database.save_scan([book(price=0.99)])
        with database.db() as conn:
            rows = conn.execute("SELECT * FROM books").fetchall()
            history = conn.execute(
                "SELECT price FROM price_history ORDER BY id"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Original title")
        self.assertEqual(rows[0]["synopsis"], "Saved synopsis")
        self.assertEqual(rows[0]["genres"], "Fantasy")
        self.assertEqual(rows[0]["previous_price"], 2.99)
        self.assertEqual(rows[0]["current_price"], 0.99)
        self.assertEqual([row["price"] for row in history], [2.99, 0.99])

    def test_availability_still_updates(self):
        database.save_scan([book(), book("other")])
        database.save_scan([book("other")])
        with database.db() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT is_active FROM books WHERE id = 'saved'"
                ).fetchone()[0],
                0,
            )
        database.save_scan([book()])
        with database.db() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT is_active FROM books WHERE id = 'saved'"
                ).fetchone()[0],
                1,
            )

    def test_enrichment_uses_open_session(self):
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)

        def enrich(active_session, books):
            self.assertIs(active_session, session)
            session.__exit__.assert_not_called()
            return 0, []

        with (
            patch.object(scraper.requests, "Session", return_value=session),
            patch.object(scraper, "DEAL_PAGES", {"Deals": "https://example.test"}),
            patch.object(scraper, "fetch_page", return_value=""),
            patch.object(scraper, "extract_books", return_value=[book()]),
            patch.object(scraper, "enrich_missing_metadata", side_effect=enrich),
        ):
            books, errors = scraper.scrape_all()
        self.assertEqual(len(books), 1)
        self.assertEqual(errors, [])
        session.__exit__.assert_called_once()

    def test_paginated_deals_keep_lowest_price_and_stop_on_repeated_page(self):
        first_page = [book(str(index), price=4.99) for index in range(20)]
        second_page = [book(str(index), price=2.99) for index in range(10, 30)]
        with (
            patch.object(scraper, "fetch_page", return_value="page") as fetch,
            patch.object(
                scraper,
                "extract_books",
                side_effect=[first_page, second_page, second_page],
            ),
        ):
            books, errors = scraper.scrape_deal_list(
                Mock(), "Deals", "https://example.test"
            )
        self.assertEqual(errors, [])
        self.assertEqual(len(books), 30)
        self.assertEqual(fetch.call_count, 3)
        prices = {saved_book["id"]: saved_book["current_price"] for saved_book in books}
        self.assertEqual(prices["0"], 4.99)
        self.assertEqual(prices["10"], 2.99)

    def test_metadata_preserves_structured_synopsis_and_combines_genres(self):
        html = """
            <script type="application/ld+json">
                {"@graph": [{"description": "A long structured synopsis about a voyage across the stars.",
                             "genre": ["Science Fiction", "Fantasy"]}]}
            </script>
            <meta name="description" content="Fallback synopsis">
            <a href="/genre/fantasy">fantasy</a>
            <a href="/category/adventure">Adventure</a>
        """
        synopsis, genres = scraper.extract_product_metadata(html)
        self.assertEqual(
            synopsis, "A long structured synopsis about a voyage across the stars."
        )
        self.assertEqual(genres, "Science Fiction, Fantasy, Adventure")

    def test_metadata_uses_page_synopsis_when_structured_data_is_missing(self):
        synopsis = "An explorer discovers an abandoned city beyond the mountains."
        html = f'<div id="book-synopsis">{synopsis}</div>'
        self.assertEqual(scraper.extract_product_metadata(html), (synopsis, ""))


if __name__ == "__main__":
    unittest.main()
