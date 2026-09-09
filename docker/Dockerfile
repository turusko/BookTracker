FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BOOKTRACKER_HOST=0.0.0.0 \
    BOOKTRACKER_DB_FILE=/app/data/kobo_deals.db

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home appuser \
    && mkdir -p /app/data /app/Logs \
    && chown -R appuser:appuser /app
COPY --chown=appuser:appuser booktracker/ ./booktracker/

USER appuser
EXPOSE 5000
CMD ["python", "-m", "booktracker"]
