"""Authenticated local bridge for normal-browser scans."""

import secrets
import hashlib
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from flask import Blueprint, current_app, jsonify, request, render_template

from .config import DEAL_PAGES, MAX_PAGES_PER_LIST
from .database import get_wishlist, save_scan, save_wishlist_check
from .scraper import extract_books, extract_product_price, MIN_BOOKS_ON_FULL_DEAL_PAGE

bridge = Blueprint("extension", __name__)


@bridge.before_request
def protect_bridge():
    if request.remote_addr not in {"127.0.0.1", "::1"} or urlsplit(request.host_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        return jsonify(error="Extension bridge is available on localhost only."), 403
    if request.endpoint != "extension.setup":
        supplied = request.headers.get("Authorization", "")
        if not secrets.compare_digest(supplied, "Bearer " + current_app.config["EXTENSION_TOKEN"]):
            return jsonify(error="Pair the extension again using BookTracker's connection code."), 401


@bridge.get("/extension")
def setup():
    response = current_app.make_response(render_template("extension.html", token=current_app.config["EXTENSION_TOKEN"]))
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def scan_jobs():
    jobs = [deal_job(source, url, 1) for source, url in DEAL_PAGES.items()]
    for row in get_wishlist():
        jobs.append(dict(kind="wishlist", url=row["url"], label=row["title"], id=row["id"]))
    return jobs


def deal_job(source, base_url, page):
    return dict(kind="deals", url=base_url if page == 1 else f"{base_url}?pageNumber={page}", label=source, page=page)


def allowed_job(url):
    for source, base_url in DEAL_PAGES.items():
        for page in range(1, MAX_PAGES_PER_LIST + 1):
            job = deal_job(source, base_url, page)
            if job["url"] == url:
                return job
    return next((job for job in scan_jobs() if job["url"] == url), None)


@bridge.get("/api/extension/plan")
def plan():
    return jsonify(jobs=scan_jobs())


@bridge.post("/api/extension/page")
def ingest():
    if request.content_length is None or request.content_length > 8 * 1024 * 1024:
        return jsonify(error="Page is missing or too large."), 413
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Expected a JSON page."), 400
    html = payload.get("html")
    seen_signatures = payload.get("seen_signatures", [])
    if not isinstance(seen_signatures, list) or len(seen_signatures) > MAX_PAGES_PER_LIST or any(not isinstance(value, str) for value in seen_signatures):
        return jsonify(error="Invalid pagination history."), 400
    job = allowed_job(payload.get("url"))
    if job is None or not isinstance(html, str) or not html.strip():
        return jsonify(error="Expected a page from the browser scan plan."), 400
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text().casefold() if soup.title else ""
    page_text = soup.get_text(" ", strip=True).casefold()
    if any(word in title for word in ("challenged", "just a moment", "access denied")) or "enable javascript and cookies to continue" in page_text:
        return jsonify(error="Complete Kobo's verification in the scan tab first."), 422
    try:
        if job["kind"] == "deals":
            books = extract_books(html, job["label"])
            if not books:
                # An empty follow-on page is a normal pagination terminator.
                # Product links without readable prices indicate a parser/load
                # failure instead, so retain the retry path for those pages.
                error_page = any(text in page_text for text in (
                    "temporarily unavailable", "something went wrong", "access denied",
                    "verify you are human", "page not found", "internal server error",
                ))
                if (job["page"] > 1 and seen_signatures and "kobo" in title
                        and soup.body is not None and not error_page
                        and not soup.select('a[href*="/ebook/"]')):
                    return jsonify(
                        message=f"{job['label']}, page {job['page']}: no books; list finished.",
                        next_job=None,
                    )
                raise ValueError("No readable deals found. Leave the Kobo page open until it finishes loading.")
            signature = hashlib.sha256("|".join(sorted(book["id"] for book in books)).encode()).hexdigest()
            if signature in seen_signatures:
                return jsonify(message=f"{job['label']}: repeated page; list finished.", signature=signature, next_job=None)
            save_scan(books, complete=False)
            full = len(books) >= MIN_BOOKS_ON_FULL_DEAL_PAGE
            next_job = deal_job(job["label"], DEAL_PAGES[job["label"]], job["page"] + 1) if full and job["page"] < MAX_PAGES_PER_LIST else None
            warning = " Page limit reached; this list may be incomplete." if full and next_job is None else ""
            return jsonify(message=f"Saved {len(books)} deals: {job['label']}, page {job['page']}.{warning}", signature=signature, next_job=next_job)
        price, old_price = extract_product_price(html, job["url"])
        drops, sales = save_wishlist_check(job["id"], price, old_price)
        return jsonify(message=f"Checked {job['label']}: GBP {price:.2f}.", drops=bool(drops), sales=bool(sales))
    except ValueError as error:
        return jsonify(error=str(error)), 422
