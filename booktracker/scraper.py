import logging
from time import monotonic
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from curl_cffi import requests

from .config import (
    DEAL_PAGES,
    HEADERS,
    MAX_DETAIL_FETCHES_PER_SCAN,
    MAX_PAGES_PER_LIST,
    TIMEOUT,
)
from .database import db, get_wishlist, save_wishlist_check
from .utils import clean_text, price_from_text, prices_from_text

logger = logging.getLogger("booktracker.scraper")

MIN_BOOKS_ON_FULL_DEAL_PAGE = 20
MAX_PRODUCT_CONTAINER_DEPTH = 7
MAX_PRODUCT_CONTAINER_TEXT_LENGTH = 3500
MAX_PRODUCT_LINKS_PER_CONTAINER = 5

GENERIC_PRODUCT_LINK_TEXT = {
    "read more",
    "see synopsis",
    "add to cart",
    "add to wishlist",
    "preview now",
    "buy now",
}


def canonical_book_id(url: str, title: str, author: str):
    parts = urlsplit(url)
    key = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", "")) or f"{title}|{author}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def likely_product_container(anchor):
    node = anchor
    closest_container = anchor.parent

    for _ in range(MAX_PRODUCT_CONTAINER_DEPTH):
        node = getattr(node, "parent", None)
        if not node:
            break

        text = clean_text(node.get_text(" ", strip=True))
        product_links = node.select('a[href*="/ebook/"]')

        if "£" in text and product_links:
            closest_container = node

            if (
                len(text) < MAX_PRODUCT_CONTAINER_TEXT_LENGTH
                and len(product_links) <= MAX_PRODUCT_LINKS_PER_CONTAINER
            ):
                return node

    return closest_container


def extract_card_synopsis(container, title="", author=""):
    candidates = []

    for selector in (
        ".synopsis",
        ".description",
        ".product-synopsis",
        "[class*='synopsis']",
        "[class*='description']",
    ):
        for node in container.select(selector):
            value = clean_text(node.get_text(" ", strip=True))
            if value:
                candidates.append(value)

    for link in container.select("a"):
        if clean_text(link.get_text(" ", strip=True)).casefold() == "read more":
            parent = link.parent
            if parent:
                value = clean_text(parent.get_text(" ", strip=True))
                if value:
                    candidates.append(value)

    for value in candidates:
        lowered = value.casefold()
        if lowered in GENERIC_PRODUCT_LINK_TEXT:
            continue
        if title and lowered == title.casefold():
            continue
        if author and lowered == author.casefold():
            continue

        value = re.sub(r"\s*(?:Read more|See Synopsis)\s*$", "", value, flags=re.I)
        if len(value) >= 40:
            return value[:3000]

    return ""


def iter_json_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_objects(child)


def extract_product_metadata(html):
    soup = BeautifulSoup(html, "html.parser")
    synopsis, genres = extract_structured_metadata(soup)
    if not synopsis:
        synopsis = extract_page_synopsis(soup)
    genres.extend(extract_category_labels(soup))
    return synopsis[:5000], ", ".join(unique_genre_labels(genres)[:20])


def extract_structured_metadata(soup):
    synopsis = ""
    genres = []
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        for metadata in iter_json_objects(data):
            description = metadata.get("description")
            if not synopsis and isinstance(description, str):
                description = clean_text(
                    BeautifulSoup(description, "html.parser").get_text(" ")
                )
                if len(description) >= 30:
                    synopsis = description

            genre = metadata.get("genre")
            if isinstance(genre, str):
                genres.append(clean_text(genre))
            elif isinstance(genre, list):
                genres.extend(clean_text(str(x)) for x in genre if x)

    return synopsis, genres


def extract_page_synopsis(soup):
    synopsis = ""
    if not synopsis:
        meta = soup.select_one('meta[name="description"]')
        if meta and meta.get("content"):
            synopsis = clean_text(meta.get("content"))

    if not synopsis:
        for selector in (
            "[class*='synopsis']",
            "[class*='description']",
            "#book-synopsis",
            ".book-synopsis",
        ):
            node = soup.select_one(selector)
            if node:
                candidate = clean_text(node.get_text(" ", strip=True))
                if len(candidate) >= 30:
                    synopsis = candidate
                    break

    return synopsis


def extract_category_labels(soup):
    genres = []
    for link in soup.select("a"):
        href = (link.get("href") or "").lower()
        label = clean_text(link.get_text(" ", strip=True))
        if not label or len(label) > 80:
            continue
        if any(part in href for part in ("/ebooks/", "/genre/", "/category/")):
            if label.casefold() not in {"ebooks", "books"}:
                genres.append(label)

    return genres


def unique_genre_labels(genres):
    cleaned_genres = []
    seen = set()
    for genre in genres:
        genre = clean_text(genre)
        key = genre.casefold()
        if not genre or key in seen:
            continue
        seen.add(key)
        cleaned_genres.append(genre)

    return cleaned_genres


def enrich_missing_metadata(session, books):
    if not books:
        return 0, []

    with db() as connection:
        existing = {
            row["id"]: row
            for row in connection.execute(
                "SELECT id, synopsis, genres FROM books WHERE id IN (%s)"
                % ",".join("?" for _ in books),
                [book["id"] for book in books],
            ).fetchall()
        }

    candidates = []
    for book in books:
        row = existing.get(book["id"])
        has_synopsis = bool(row and clean_text(row["synopsis"]))
        has_genres = bool(row and clean_text(row["genres"]))

        book["synopsis"] = row["synopsis"] if has_synopsis else book.get("synopsis", "")
        book["genres"] = row["genres"] if has_genres else book.get("genres", "")

        if row is None and (
            not clean_text(book.get("synopsis")) or not clean_text(book.get("genres"))
        ):
            candidates.append(book)

    errors = []
    enriched = 0

    for book in candidates[:MAX_DETAIL_FETCHES_PER_SCAN]:
        try:
            response = session.get(
                book["url"],
                headers=HEADERS,
                timeout=TIMEOUT,
                impersonate="chrome",
            )
            response.raise_for_status()
            synopsis, genres = extract_product_metadata(response.text)

            if synopsis:
                book["synopsis"] = synopsis
            if genres:
                book["genres"] = genres
            enriched += 1
        except Exception as error:
            logger.exception("Metadata fetch failed: book=%s", book["id"])
            errors.append(f"{book['title']}: {error}")

    return enriched, errors


def extract_books(html: str, source_name: str):
    soup = BeautifulSoup(html, "html.parser")
    found = {}

    anchors = soup.select('a[href*="/ebook/"]')

    for anchor in anchors:
        title = clean_text(anchor.get_text(" ", strip=True))
        href = anchor.get("href")

        if (
            not title
            or not href
            or len(title) > 220
            or title.casefold() in GENERIC_PRODUCT_LINK_TEXT
        ):
            continue

        url = urljoin("https://www.kobo.com", href)
        container = likely_product_container(anchor)
        block_text = clean_text(container.get_text(" ", strip=True))

        old_price, current_price = prices_from_text(block_text)
        if current_price is None:
            continue

        author = extract_deal_card_author(container, title, block_text)

        book_id = canonical_book_id(url, title, author)
        synopsis = extract_card_synopsis(container, title, author)

        book = {
            "id": book_id,
            "title": title,
            "author": author,
            "url": url,
            "source": source_name,
            "old_price": old_price,
            "current_price": current_price,
            "synopsis": synopsis,
            "genres": "",
        }

        if book_id not in found:
            found[book_id] = book
        else:

            if not found[book_id].get("synopsis") and synopsis:
                found[book_id]["synopsis"] = synopsis

    return list(found.values())


def extract_deal_card_author(container, title, block_text):
    author = ""

    for link in container.select("a"):
        link_text = clean_text(link.get_text(" ", strip=True))
        author_url = link.get("href", "")
        if (
            link_text
            and link_text != title
            and link_text.casefold() not in GENERIC_PRODUCT_LINK_TEXT
            and (
                "/author/" in author_url
                or "/search?query=" in author_url
                or "search?Query=" in author_url
            )
        ):
            author = link_text
            break

    if not author:
        match = re.search(
            r"\bby\s+(.{2,100}?)(?=\s+(?:Series|Was|£|\(|Translated|Read more|See Synopsis)\b)",
            block_text,
            re.I,
        )
        if match:
            author = clean_text(match.group(1)).rstrip(" .…")

    return author


def fetch_page(session: requests.Session, base_url: str, page_number: int):
    params = {} if page_number == 1 else {"pageNumber": page_number}
    response = session.get(
        base_url,
        params=params,
        headers=HEADERS,
        timeout=TIMEOUT,
        impersonate="chrome",
    )
    response.raise_for_status()
    return response.text


def scrape_all():
    all_books = {}
    errors = []

    with requests.Session(impersonate="chrome") as session:
        for source_name, base_url in DEAL_PAGES.items():
            source_books, source_errors = scrape_deal_list(
                session, source_name, base_url
            )
            keep_lowest_deal_prices(all_books, source_books)
            errors.extend(source_errors)

        books = list(all_books.values())
        _, metadata_errors = enrich_missing_metadata(session, books)
    if metadata_errors:
        errors.extend(f"Metadata: {error}" for error in metadata_errors[:5])

    return books, errors


def scrape_deal_list(session, source_name, base_url):
    all_books = {}
    errors = []
    previous_page_ids = None

    for page_number in range(1, MAX_PAGES_PER_LIST + 1):
        try:
            html = fetch_page(session, base_url, page_number)
            books = extract_books(html, source_name)
        except Exception as error:
            logger.exception("Deal page failed: source=%s page=%d", source_name, page_number)
            message = str(error)
            if "403" in message:
                message += " (Kobo blocked the request; update curl-cffi with: pip install -U curl-cffi)"
            errors.append(f"{source_name}, page {page_number}: {message}")
            break

        logger.info("Deal page read: source=%s page=%d books=%d", source_name, page_number, len(books))
        page_ids = {book["id"] for book in books}

        if not books:
            errors.append(f"{source_name}, page {page_number}: no readable books returned")
            break
        if page_ids == previous_page_ids:
            break

        previous_page_ids = page_ids

        keep_lowest_deal_prices(all_books, books)

        if len(books) < MIN_BOOKS_ON_FULL_DEAL_PAGE:
            break
    else:
        errors.append(f"{source_name}: page limit reached; scan may be incomplete")

    return list(all_books.values()), errors


def keep_lowest_deal_prices(books_by_id, incoming_books):
    for book in incoming_books:
        existing = books_by_id.get(book["id"])
        if existing is None or book["current_price"] < existing["current_price"]:
            books_by_id[book["id"]] = book


def wishlist_url(url):
    ebook_path_prefix = "/gb/en/ebook/"
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.netloc != "www.kobo.com"
        or not parts.path.startswith(ebook_path_prefix)
        or not parts.path[len(ebook_path_prefix) :].strip("/")
        or parts.username
        or parts.password
    ):
        raise ValueError("Expected a Kobo UK ebook product URL")
    return urlunsplit(("https", "www.kobo.com", parts.path.rstrip("/"), "", ""))


def extract_search_results(html):
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    for anchor in soup.select('a[data-testid="title"][href*="/ebook/"]'):
        try:
            url = wishlist_url(urljoin("https://www.kobo.com", anchor["href"]))
        except ValueError:
            continue
        card = anchor.find_parent(attrs={"data-ratunit": "item"})
        if not card:
            continue
        book = extract_search_card(anchor, card, url)
        found.setdefault(book["id"], book)
    if not found:
        text = soup.get_text(" ", strip=True).casefold()
        if not any(
            message in text
            for message in (
                "no results",
                "no matches",
                "couldn’t find",
                "couldn't find",
                "0 results",
            )
        ):
            raise ValueError("Kobo search could not be read. Please try again later.")
    return list(found.values())


def extract_search_card(anchor, card, url):
    title = clean_text(anchor.get_text(" ", strip=True))
    authors = []
    for link in card.select('[data-testid="authors"] a'):
        label = clean_text(link.get_text(" ", strip=True))
        if label and label not in authors:
            authors.append(label)
    subtitle = anchor.parent.parent.find("p", recursive=False)
    language = card.select_one('[data-testid="book-card-language-iso"]')
    pricing = card.select_one('[data-testid$="-pricing"]')
    price = price_from_text(pricing.get_text(" ", strip=True)) if pricing else None
    book_id = canonical_book_id(url, title, "")
    return {
        "id": book_id,
        "title": title,
        "author": ", ".join(authors),
        "url": url,
        "edition": clean_text(subtitle.get_text()) if subtitle else "",
        "language": clean_text(language.get_text()).lstrip("· ") if language else "",
        "price": price,
    }


def search_kobo(title, author=""):
    query = clean_text(f"{title} {author}")
    if not clean_text(title) or len(query) > 300:
        raise ValueError("Enter a book title (up to 300 characters including author).")
    with requests.Session(impersonate="chrome") as session:
        response = session.get(
            "https://www.kobo.com/gb/en/search",
            params={"query": query},
            headers=HEADERS,
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        response.raise_for_status()
        return extract_search_results(response.text)


def extract_product_price(html, expected_url):
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.select_one('link[rel="canonical"]')
    if not canonical or wishlist_url(canonical.get("href", "")) != wishlist_url(
        expected_url
    ):
        raise ValueError(
            "Kobo did not return the confirmed edition. Please retry or select the book again."
        )
    price = extract_purchase_widget_price(soup)
    if price is None:
        price = extract_open_graph_price(soup)
    if price is None:
        raise ValueError(
            "No UK purchase price available; the last known price has been kept."
        )
    return price


def extract_purchase_widget_price(soup):
    for node in soup.select("[data-kobo-gizmo-config]"):
        try:
            config = json.loads(node["data-kobo-gizmo-config"])
        except (ValueError, TypeError):
            continue
        details = config.get("priceDetails")
        if not isinstance(details, dict) or config.get("productType") != "Book":
            continue
        if details.get("currency") != "GBP":
            raise ValueError("Kobo returned a price outside the UK store.")
        price = price_from_text(details.get("displayPrice"))
        old_price = price_from_text(details.get("wasPrice")) or price_from_text(
            details.get("listPrice")
        )
        if price is not None:
            return price, (
                old_price if old_price is not None and old_price > price else None
            )
    return None


def extract_open_graph_price(soup):
    price_meta = soup.select_one('meta[property="og:price"]')
    currency = soup.select_one('meta[property="og:currency_code"]')
    if price_meta and currency and currency.get("content") == "GBP":
        try:
            value = Decimal(price_meta["content"])
            if value.is_finite() and value >= 0:
                return float(value), None
        except (InvalidOperation, KeyError):
            pass
    return None


def check_wishlist_book(session, book):
    try:
        url = wishlist_url(book["url"])
        response = session.get(
            url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False
        )
        response.raise_for_status()
        price, old_price = extract_product_price(response.text, url)
        price_dropped, newly_on_sale = save_wishlist_check(book["id"], price, old_price)
        return dict(
            checked=1, drops=int(price_dropped), sales=int(newly_on_sale), failed=0
        )
    except Exception as error:
        logger.exception("Wish-list price check failed: book=%s", book["id"])
        save_wishlist_check(book["id"], error=str(error))
        return dict(checked=0, drops=0, sales=0, failed=1)


def check_wishlist():
    started = monotonic()
    logger.info("Wish-list check started")
    totals = dict(checked=0, drops=0, sales=0, failed=0)
    with requests.Session(impersonate="chrome") as session:
        for book in get_wishlist():
            result = check_wishlist_book(session, book)
            for key in totals:
                totals[key] += result[key]
    logger.info("Wish-list check finished in %.2f seconds: %s", monotonic() - started, totals)
    return totals
