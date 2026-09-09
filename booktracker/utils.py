import re
from datetime import datetime


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def money(value):
    if value is None:
        return ""
    return f"£{value:.2f}"


def price_from_text(text: str):
    match = re.search(r"£\s*(\d+(?:\.\d{1,2})?)", text or "")
    return float(match.group(1)) if match else None


def prices_from_text(text: str):
    text = clean_text(text)

    match = re.search(
        r"Was\s*£\s*(\d+(?:\.\d{1,2})?).*?" r"Now\s*£\s*(\d+(?:\.\d{1,2})?)",
        text,
        re.I,
    )
    if match:
        return float(match.group(1)), float(match.group(2))

    values = re.findall(r"£\s*(\d+(?:\.\d{1,2})?)", text)
    if len(values) >= 2:
        return float(values[-2]), float(values[-1])
    if values:
        return None, float(values[-1])
    return None, None


def current_timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")
