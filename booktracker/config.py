import os
from pathlib import Path

APP_TITLE = "Kobo Deal Tracker"
DB_FILE = os.environ.get(
    "BOOKTRACKER_DB_FILE",
    str(Path(__file__).resolve().parent.parent / "data" / "kobo_deals.db"),
)
HOST = os.environ.get("BOOKTRACKER_HOST", "127.0.0.1")


DEAL_PAGES = {
    "Special Offers": "https://www.kobo.com/gb/en/list/special-offers-from-99p/7LG8iCWSwsqRNwjV_iFn1Q",
    "99 for 99p": "https://www.kobo.com/gb/en/list/99-for-99p/205jtiQgoM1WdAjbXi8_8g",
    "SciFi & Fantasy": "https://www.kobo.com/gb/en/list/sci-fi-fantasy-deals/7LG8iCWSwsqRNwjV_iFn1Q",
}

MAX_PAGES_PER_LIST = 20


DEFAULT_FAVOURITE_KEYWORDS = [
    "science fiction",
    "sci-fi",
    "science-fiction",
    "space",
    "starship",
    "galaxy",
    "alien",
    "first contact",
    "fantasy",
    "magic",
    "dragon",
    "litrpg",
    "rpg",
]
