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
