import logging
import sqlite3
from pathlib import Path
from contextlib import contextmanager

from .config import DB_FILE, DEFAULT_FAVOURITE_KEYWORDS
from .utils import clean_text, current_timestamp

logger = logging.getLogger("booktracker.database")


@contextmanager
def db():
    connection = sqlite3.connect(DB_FILE)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db():
    Path(DB_FILE).parent.mkdir(parents=True, exist_ok=True)
    with db() as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS books (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT,
            url TEXT NOT NULL,
            source TEXT,
            old_price REAL,
            current_price REAL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            first_seen_price REAL,
            previous_price REAL,
            synopsis TEXT,
            genres TEXT,
            is_active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            price REAL,
            old_price REAL,
            source TEXT,
            FOREIGN KEY(book_id) REFERENCES books(id)
        );

        CREATE INDEX IF NOT EXISTS idx_books_price
            ON books(current_price);

        CREATE INDEX IF NOT EXISTS idx_history_book
            ON price_history(book_id, checked_at);

        CREATE TABLE IF NOT EXISTS wishlist (
            book_id TEXT PRIMARY KEY REFERENCES books(id),
            added_at TEXT NOT NULL,
            baseline_price REAL,
            current_price REAL,
            previous_price REAL,
            old_price REAL,
            last_checked TEXT,
            last_attempt TEXT,
            check_error TEXT,
            on_sale INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS favourite_keywords (
            keyword TEXT PRIMARY KEY COLLATE NOCASE,
            created_at TEXT NOT NULL
        );
        """)

        add_missing_metadata_columns(connection)
        seed_default_keywords_if_empty(connection)


def add_missing_metadata_columns(connection):

    book_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(books)").fetchall()
    }
    if "synopsis" not in book_columns:
        connection.execute("ALTER TABLE books ADD COLUMN synopsis TEXT")
    if "genres" not in book_columns:
        connection.execute("ALTER TABLE books ADD COLUMN genres TEXT")


def seed_default_keywords_if_empty(connection):
    count = connection.execute("SELECT COUNT(*) FROM favourite_keywords").fetchone()[0]

    if count == 0:
        now = current_timestamp()
        connection.executemany(
            "INSERT OR IGNORE INTO favourite_keywords (keyword, created_at) VALUES (?, ?)",
            [(keyword, now) for keyword in DEFAULT_FAVOURITE_KEYWORDS],
        )


def save_scan(scraped, *, complete=True):
    # Materialize once so generators and duplicate cards cannot distort history.
    unique_books = {}
    for book in scraped:
        existing = unique_books.get(book["id"])
        if existing is None or book["current_price"] < existing["current_price"]:
            unique_books[book["id"]] = book
    scraped = list(unique_books.values())
    if not scraped:
        logger.warning("Empty scan ignored; existing availability preserved")
        return 0
    now = current_timestamp()
    seen_ids = {book["id"] for book in scraped}

    with db() as connection:
        existing_ids = {row[0] for row in connection.execute("SELECT id FROM books")}
        active_ids = {row[0] for row in connection.execute("SELECT id FROM books WHERE is_active = 1")}

        if complete:
            connection.execute("UPDATE books SET is_active = 0")

        for book in scraped:
            save_deal_book(connection, book, now)
            record_price_change(
                connection,
                book["id"],
                now,
                book["current_price"],
                book["old_price"],
                book["source"],
                include_sale_changes=True,
            )

    logger.info(
        "Scan saved: found=%d added=%d updated=%d ended=%d complete=%s",
        len(seen_ids), len(seen_ids - existing_ids), len(seen_ids & existing_ids),
        len(active_ids - seen_ids) if complete else 0, complete,
    )
    return len(seen_ids)


def save_deal_book(connection, book, now):
    existing = connection.execute(
        "SELECT * FROM books WHERE id = ?",
        (book["id"],),
    ).fetchone()

    if existing:
        previous_price = (
            existing["current_price"]
            if existing["current_price"] != book["current_price"]
            else existing["previous_price"]
        )

        connection.execute(
            """
            UPDATE books
            SET source = ?,
                old_price = ?,
                previous_price = ?,
                current_price = ?,
                first_seen_price = COALESCE(first_seen_price, ?),
                synopsis = ?,
                genres = ?,
                last_seen = ?,
                is_active = 1
            WHERE id = ?
            """,
            (
                book["source"],
                book["old_price"],
                previous_price,
                book["current_price"],
                book["current_price"],
                existing["synopsis"] or book.get("synopsis", ""),
                existing["genres"] or book.get("genres", ""),
                now,
                book["id"],
            ),
        )
    else:
        connection.execute(
            """
            INSERT INTO books (
                id, title, author, url, source,
                old_price, current_price,
                first_seen, last_seen,
                first_seen_price, previous_price,
                synopsis, genres, is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                book["id"],
                book["title"],
                book["author"],
                book["url"],
                book["source"],
                book["old_price"],
                book["current_price"],
                now,
                now,
                book["current_price"],
                None,
                book.get("synopsis", ""),
                book.get("genres", ""),
            ),
        )


def record_price_change(
    connection,
    book_id,
    checked_at,
    price,
    old_price,
    source,
    include_sale_changes=False,
):
    last_observation = connection.execute(
        """
        SELECT price, old_price FROM price_history
        WHERE book_id = ? ORDER BY id DESC LIMIT 1
        """,
        (book_id,),
    ).fetchone()
    price_changed = last_observation is None or last_observation["price"] != price
    sale_changed = (
        include_sale_changes
        and last_observation is not None
        and last_observation["old_price"] != old_price
    )
    if price_changed or sale_changed:
        logger.debug(
            "Recording price observation: book=%s source=%s previous=%s price=%s old_price=%s",
            book_id, source, last_observation["price"] if last_observation else None, price, old_price,
        )
        connection.execute(
            """
            INSERT INTO price_history (book_id, checked_at, price, old_price, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (book_id, checked_at, price, old_price, source),
        )


def get_favourite_keywords():
    with db() as connection:
        rows = connection.execute(
            "SELECT keyword FROM favourite_keywords ORDER BY keyword COLLATE NOCASE"
        ).fetchall()
    return [row["keyword"] for row in rows]


def save_favourite_keywords(keywords):
    cleaned = []
    seen = set()

    for keyword in keywords:
        keyword = clean_text(keyword).lower()
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        cleaned.append(keyword)

    now = current_timestamp()

    with db() as connection:
        connection.execute("DELETE FROM favourite_keywords")
        connection.executemany(
            "INSERT INTO favourite_keywords (keyword, created_at) VALUES (?, ?)",
            [(keyword, now) for keyword in cleaned],
        )


def matched_keywords(row, keywords):
    haystack = " ".join(
        [
            row.get("title") or "",
            row.get("author") or "",
            row.get("genres") or "",
            row.get("synopsis") or "",
        ]
    ).lower()
    return [word for word in keywords if word.lower() in haystack]


def interest_score(row, keywords):
    return len(matched_keywords(row, keywords))


def query_books(view="new", search=""):
    search = clean_text(search)
    where = ["1=1"]
    args = []
    keywords = get_favourite_keywords()

    if view != "ended":
        where.append("is_active = 1")

    if view == "99p":
        where.append("current_price <= 0.99")
    elif view == "drops":
        where.append("previous_price IS NOT NULL AND current_price < previous_price")
    elif view == "new":
        where.append("date(first_seen) = date('now', 'localtime')")
    elif view == "ended":
        where.append("is_active = 0")
        where.append("source != 'Wish list'")

    if search:
        where.append(
            "(title LIKE ? OR author LIKE ? OR genres LIKE ? OR synopsis LIKE ?)"
        )
        term = f"%{search}%"
        args.extend([term, term, term, term])

    sql = f"""
        SELECT *
        FROM books
        WHERE {' AND '.join(where)}
        ORDER BY
            CASE WHEN current_price <= 0.99 THEN 0 ELSE 1 END,
            current_price ASC,
            first_seen DESC,
            title COLLATE NOCASE
    """

    with db() as connection:
        rows = [dict(row) for row in connection.execute(sql, args).fetchall()]

    for row in rows:
        row["matches"] = matched_keywords(row, keywords)
        row["interest_score"] = len(row["matches"])

    if view == "matches":
        rows = [row for row in rows if row["interest_score"] > 0]

    rows.sort(key=book_display_order)
    return rows


def book_display_order(book):
    price = book["current_price"]
    is_99p_deal = price is not None and price <= 0.99
    return (
        -book["interest_score"],
        0 if is_99p_deal else 1,
        price if price is not None else 999,
        book["title"].lower(),
    )


def stats():
    with db() as connection:
        row = connection.execute("""
            SELECT
                COUNT(*) FILTER (WHERE is_active = 1) AS active,
                COUNT(*) FILTER (
                    WHERE is_active = 1 AND current_price <= 0.99
                ) AS penny99,
                COUNT(*) FILTER (
                    WHERE is_active = 1
                    AND previous_price IS NOT NULL
                    AND current_price < previous_price
                ) AS drops,
                COUNT(*) FILTER (
                    WHERE is_active = 1
                    AND date(first_seen) = date('now', 'localtime')
                ) AS new_today,
                MAX(last_seen) FILTER (WHERE source != 'Wish list') AS last_checked
            FROM books
        """).fetchone()
        return dict(row)


def get_book(book_id):
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM books WHERE id = ?", (book_id,)
        ).fetchone()
        return dict(row) if row else None


def get_wishlist(search=""):
    with db() as connection:
        rows = connection.execute(
            """
            SELECT b.id, b.title, b.author, b.url, w.*
            FROM wishlist w JOIN books b ON b.id = w.book_id
            WHERE b.title LIKE ? OR b.author LIKE ?
            ORDER BY w.added_at DESC, b.title COLLATE NOCASE
        """,
            (f"%{clean_text(search)}%",) * 2,
        ).fetchall()
        return [dict(row) for row in rows]


def add_to_wishlist(book):
    now = current_timestamp()
    with db() as connection:
        connection.execute(
            """INSERT OR IGNORE INTO books
            (id, title, author, url, source, first_seen, last_seen, is_active)
            VALUES (?, ?, ?, ?, 'Wish list', ?, ?, 0)""",
            (book["id"], book["title"], book.get("author", ""), book["url"], now, now),
        )
        cursor = connection.execute(
            "INSERT OR IGNORE INTO wishlist (book_id, added_at) VALUES (?, ?)",
            (book["id"], now),
        )
        return cursor.rowcount == 1


def remove_from_wishlist(book_id):
    with db() as connection:
        connection.execute("DELETE FROM wishlist WHERE book_id = ?", (book_id,))


def save_wishlist_check(book_id, price=None, old_price=None, error=None):
    now = current_timestamp()
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM wishlist WHERE book_id = ?", (book_id,)
        ).fetchone()
        if not row:
            return False, False
        if error:
            connection.execute(
                "UPDATE wishlist SET last_attempt = ?, check_error = ? WHERE book_id = ?",
                (now, error, book_id),
            )
            return False, False
        if price is None:
            raise ValueError("A successful check requires a price")
        on_sale = old_price is not None and old_price > price
        dropped = row["current_price"] is not None and price < row["current_price"]
        newly_on_sale = on_sale and not row["on_sale"]
        connection.execute(
            """UPDATE wishlist SET baseline_price = COALESCE(baseline_price, ?),
            previous_price = current_price, current_price = ?, old_price = ?, on_sale = ?,
            last_checked = ?, last_attempt = ?, check_error = NULL WHERE book_id = ?""",
            (price, price, old_price, on_sale, now, now, book_id),
        )
        record_price_change(
            connection,
            book_id,
            now,
            price,
            old_price,
            "Wish list",
            include_sale_changes=True,
        )
        return dropped, newly_on_sale
