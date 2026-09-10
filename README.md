# BookTracker

BookTracker keeps a local record of Kobo UK ebook deals, price changes, and your
wishlist. It runs on your computer and opens in your web browser.

## How it works

BookTracker has two parts:

- **The Python app** displays your library, wishlist, and saved prices, and stores
  them in a local SQLite database.
- **The Edge/Chrome extension** opens Kobo pages in your normal browser, reads
  their contents, and sends them to the running app using a connection code.

You start scans from the extension. It visits each configured deal list, follows
its pages, then checks every book on your wishlist. Prices update when a page is
successfully imported. There are no scheduled background scans, direct HTTP
scans, or Playwright browser pop-ups.

## Set up on Windows

You need Python, Microsoft Edge or Google Chrome, and a copy of this project.
Python 3.14 has been used for local development. Keep the project folders together.

### 1. Start BookTracker

Open PowerShell in the project root?the folder containing this README and
`requirements.txt`. Use your own project path if it differs from this example:

```powershell
cd C:\Users\turus\repo\BookTracker
python -m pip install -r requirements.txt
python -m booktracker
```

Open [BookTracker](http://127.0.0.1:5000). Keep PowerShell open while using the app.
Press **Ctrl+C** there to stop it.

For later sessions, run `python -m booktracker` from the same folder. Install
requirements again after an update changes `requirements.txt`.

### 2. Install the browser extension

Use your usual browser, where you can open Kobo successfully:

1. Open `edge://extensions` in Edge or `chrome://extensions` in Chrome.
2. Enable **Developer mode**.
3. Click **Load unpacked** and select this project's `extension` folder, for
   example `C:\Users\turus\repo\BookTracker\extension`.
4. Find **BookTracker browser scan** in the browser's extensions menu. Pin it to
   the toolbar if you want easier access.

### 3. Connect and run your first scan

1. In BookTracker, click **Browser extension**, or open the
   [connection page](http://127.0.0.1:5000/extension).
2. Copy the connection code.
3. Click the BookTracker extension in your browser to open its scan tab.
4. Paste the code, choose **Deals and wishlist** or **Wishlist only**, and click
   **Start scan**.
5. Keep the extension's scan tab and its Kobo tab open. If Kobo asks you to verify
   that you are human, complete the check in the Kobo tab.
6. Watch progress in the extension tab. When the scan finishes, refresh
   BookTracker to see the results.

The connection code changes every time the app restarts. Copy the new code into
the extension before the next scan. The extension remembers the code only for
the current browser session.

## Using BookTracker

### Browse deals

Use the tabs to browse new books, keyword matches, 99p deals, price drops, or all
imported books. Search by title or author. Use **Settings** to change the keywords
used for matches. Book links open the corresponding Kobo page.

Saved prices are observations from successful scans, not a live price feed.
Check the Kobo page before buying. A sale indicates a higher previous or list
price; coupon and membership offers are not tracked.

### Track a wishlist

Add a book from the deal list, or open **Wish List** and use **Find imported
books** to search books already saved in BookTracker. Confirm the book you want
to track. This search does not search Kobo's entire catalogue.

Adding a book does not immediately check its price. The next extension scan
checks wishlist books after the deal lists. Remove a book from the wishlist when
you no longer want to track it; its saved book record is retained.

### Control a scan

Choose **Wishlist only** to update saved wishlist prices without visiting deal
lists. **Deals and wishlist** is the default. An empty wishlist opens no Kobo
pages in wishlist-only mode. The scan selection is locked until the scan ends.

| Control | What it does |
| --- | --- |
| **Pause / Resume** | Pauses between reads and saves, then continues. |
| **Resume** after an error | Retries the failed page. |
| **Skip failed page** | Skips the rest of that deal list, or the failed wishlist book, and continues. |
| **Stop** | Stops the scan and keeps results already saved. |

An in-progress save may finish after you press Pause or Stop. Pause/resume works
only while the scan tab stays open; closing it or restarting the browser loses
the current scan's progress. The Kobo tab stays open after the scan ends.

The queued-page count can grow as more pages are discovered. A short page, a
repeated page, or a recognised empty page after earlier results ends a deal list.
An apparently empty page is read again after a short wait. Errors and unreadable
book cards still pause for retry. Each list has a default limit of 20 pages; the
scan reports when it reaches that limit.

Failed checks keep previous prices. Browser scans do not mark books as ended
just because they were absent from the scan, so the **Ended** tab is not an
automatic record of offers disappearing from Kobo.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| Python cannot find `main.py`, or `app.py` reports a relative-import error | Run `python -m booktracker` from the project root. If you are inside the `booktracker` subfolder, run `cd ..` first. |
| The extension cannot connect | Keep the Python app running and check that http://127.0.0.1:5000 opens on the same computer. |
| The extension asks you to pair again | Copy a fresh code from the app's connection page after restarting BookTracker. |
| Kobo asks for verification | Complete it in the Kobo tab opened by the extension. The extension waits for the page to load. |
| Verification keeps repeating | Stop or skip the failed page. Check Kobo access in your usual browser. The extension cannot guarantee that Kobo will accept verification. |
| A page fails to load or parse | Use Resume to retry, or Skip failed page to continue. Saved prices remain intact. |
| Recent changes are missing from the extension | Reload the extension on its browser extensions page, close the old scan tab, and open a new one. |
| The app cannot start because port 5000 is busy | Stop any other BookTracker process or Docker deployment using that port. |

## Updating the app

1. Stop BookTracker with Ctrl+C and finish or stop any extension scan.
2. Update the project files, keeping your `data` folder.
3. Install requirements if they changed, then run `python -m booktracker`.
4. If extension files changed, click **Reload** on the browser's extensions page
   and reopen the extension scan tab.
5. Copy the new connection code and start a scan when ready.

## Data and configuration

- **Database:** `data/kobo_deals.db` stores imported books, prices, and wishlist
  data. To back it up, stop the app and copy the database file to a safe location.
- **Logs:** `Logs/` contains runtime logs useful for troubleshooting.
- **Configuration:** `booktracker/config.py` defines deal-list URLs, the page
  limit, and default match keywords. Restart the app after changing configuration.
- **Local connection:** the extension connects to `127.0.0.1:5000`. Its bridge
  requires a connection code and accepts local requests only. Do not share the code.

The extension has access to Kobo pages and the local app. It does not read your
browser cookies or use browser debugging controls. Database and log files are
excluded from Git.

## Docker

Docker deployment files remain available for running the app and viewing stored
data. **Use the local Python setup above for extension scanning:** the extension
bridge's loopback-only checks do not support the Docker connection path.

See [Docker instructions](docs/DOCKER.md) for deployment and storage details. Docker
uses its own persistent database volume; later changes in that volume do not
update the local `data/kobo_deals.db`. Stop Docker before starting the local app
on port 5000.

## Project layout

| Path | Contents |
| --- | --- |
| `booktracker/` | Python app, HTML parsers, extension bridge, templates, and styles |
| `extension/` | Unpacked Edge/Chrome extension |
| `data/` | Local SQLite database |
| `Logs/` | Runtime logs |
| `tests/` | Python tests and extension workflow tests |
| `scripts/` | Windows deployment and database import helpers |
| `docker/` | Dockerfile |
| `docs/` | Deployment documentation |
| `compose.yaml` | Docker Compose configuration |

## Development checks

From the project root:

```powershell
python -m unittest discover -s tests -t .
```

With Node.js installed, run the extension workflow tests:

```powershell
node tests/extension_runner.cjs
```

These tests use simulated browser pages. They do not verify current access to
Kobo or complete human-verification checks.
