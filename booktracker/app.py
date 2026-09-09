import logging
from time import monotonic
import secrets
from datetime import datetime

from curl_cffi import requests
from flask import Flask, redirect, render_template, request, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import APP_TITLE, HOST
from .database import (
    add_to_wishlist,
    get_book,
    get_favourite_keywords,
    get_wishlist,
    init_db,
    query_books,
    remove_from_wishlist,
    save_favourite_keywords,
    save_scan,
    stats,
)
from .scraper import check_wishlist, check_wishlist_book, scrape_all, search_kobo
from .utils import money
from .logging_setup import configure_logging

logger = logging.getLogger("booktracker.app")

BOOK_CONFIRMATION_LIFETIME_SECONDS = 3600

log_handler = configure_logging()
app = Flask(__name__)
app.logger.addHandler(log_handler)
app.config["SECRET_KEY"] = secrets.token_hex(32)
book_confirmation_tokens = URLSafeTimedSerializer(
    app.config["SECRET_KEY"], salt="wishlist-book"
)
app.jinja_env.filters["money"] = money


@app.get("/")
def home():
    view = request.args.get("view", "new")
    search = request.args.get("q", "")
    message = request.args.get("message", "")
    books = query_books(view, search)

    return render_template(
        "home.html",
        app_title=APP_TITLE,
        books=books,
        summary=stats(),
        view=view,
        search=search,
        message=message,
        today=datetime.now().astimezone().date().isoformat(),
        wishlist_ids={book["id"] for book in get_wishlist()},
        keywords_text="\n".join(get_favourite_keywords()),
        tabs=[
            ("new", "New"),
            ("matches", "★ Matches"),
            ("99p", "99p"),
            ("drops", "Price Drops"),
            ("all", "All"),
            ("ended", "Ended"),
        ],
    )


@app.post("/settings")
def settings():
    keyword_lines = request.form.get("keywords", "")
    keywords = []

    for line in keyword_lines.splitlines():
        keywords.extend(part.strip() for part in line.split(","))

    save_favourite_keywords(keywords)

    return redirect(
        url_for(
            "home",
            view="matches",
            message=f"Saved {len(get_favourite_keywords())} match keywords.",
        )
    )


@app.get("/wishlist")
def wishlist():
    return render_template(
        "wishlist.html",
        app_title=APP_TITLE,
        books=get_wishlist(request.args.get("q", "")),
        search=request.args.get("q", ""),
        message=request.args.get("message", ""),
    )


@app.post("/wishlist/search")
def wishlist_search():
    title = request.form.get("title", "")
    author = request.form.get("author", "")
    try:
        results = search_kobo(title, author)
        saved_book_ids = {book["id"] for book in get_wishlist()}
        for book in results:
            book["token"] = book_confirmation_tokens.dumps(book)
            book["saved"] = book["id"] in saved_book_ids
        return render_template(
            "wishlist.html",
            app_title=APP_TITLE,
            books=get_wishlist(),
            results=results,
            title=title,
            author=author,
        )
    except Exception as error:
        logger.exception("Wish-list search failed")
        return (
            render_template(
                "wishlist.html",
                app_title=APP_TITLE,
                books=get_wishlist(),
                title=title,
                author=author,
                message=f"Search failed: {error}",
            ),
            502,
        )


@app.post("/wishlist/add")
def wishlist_add():
    token = request.form.get("token")
    if token:
        try:
            book = book_confirmation_tokens.loads(
                token, max_age=BOOK_CONFIRMATION_LIFETIME_SECONDS
            )
        except BadSignature:
            return redirect(
                url_for(
                    "wishlist",
                    message="Search confirmation expired. Please search again.",
                )
            )
    else:
        book = get_book(request.form.get("book_id", ""))
    if not book:
        return redirect(
            url_for("wishlist", message="Book not found. Please search again.")
        )
    if not add_to_wishlist(book):
        return redirect(url_for("wishlist", message="Already on your wish list."))
    with requests.Session(impersonate="chrome") as session:
        result = check_wishlist_book(session, book)
    message = "Added to your wish list."
    if result["failed"]:
        message += " The initial price check failed; Check Kobo now will retry."
    return redirect(url_for("wishlist", message=message))


@app.post("/wishlist/remove")
def wishlist_remove():
    remove_from_wishlist(request.form.get("book_id", ""))
    return redirect(url_for("wishlist", message="Removed from your wish list."))


@app.post("/scan")
def scan():
    messages = [scan_deals_and_report(), check_wishlist_and_report()]
    destination = "wishlist" if request.form.get("return_to") == "wishlist" else "home"
    return redirect(url_for(destination, message=" ".join(messages)))


def scan_deals_and_report():
    started = monotonic()
    logger.info("Deal scan started")
    try:
        scraped_books, errors = scrape_all()
        if scraped_books:
            book_count = save_scan(scraped_books, complete=not errors)
            message = f"Scan complete: {book_count} unique Kobo deal books found."
        else:
            message = "Deal scan failed: no books returned."
            logger.warning(message)
        if errors:
            for error in errors:
                logger.warning("Incomplete deal scan: %s", error)
            message += " Some deal pages failed: " + "; ".join(errors[:2])
        return message
    except Exception as error:
        logger.exception("Deal scan failed")
        return f"Deal scan failed: {error}"
    finally:
        logger.info("Deal scan finished in %.2f seconds", monotonic() - started)


def check_wishlist_and_report():
    try:
        result = check_wishlist()
        return (
            f"Wish list: {result['checked']} checked, {result['drops']} price drops, "
            f"{result['sales']} newly on sale, {result['failed']} checks failed."
        )
    except Exception as error:
        logger.exception("Wish-list check failed")
        return f"Wish-list check failed: {error}"


def run():
    logger.info("BookTracker starting")
    init_db()
    print()
    print("Kobo Deal Tracker")
    print("Open http://127.0.0.1:5000")
    print()
    app.run(host=HOST, port=5000, debug=False)
