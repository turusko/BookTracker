# Run with Docker

On Windows, start Docker Desktop with Linux containers, stop any locally running
BookTracker app, then double-click **scripts\deploy.bat** (or run it from a terminal).
The script builds the image, imports your current `data/kobo_deals.db` into the Docker
volume on first deployment, and starts BookTracker. No local Python installation
is needed. Keep the project folders together when copying the app.

The import uses SQLite's backup API and checks the copied database before use.
Your original database is unchanged. If a Docker database already exists, the
script keeps it instead of overwriting it. After deployment, changes made in Docker
are stored in the volume, not in the original local database file.

For a fresh empty database instead, run from the project directory:

```sh
docker compose up -d --build
```

Open http://localhost:5000. This runs the existing Flask server for local use; Compose publishes it only on the host's loopback interface.

Logs are written to `Logs/booktracker.log` on your computer. The SQLite database is stored in the `booktracker-data` named volume and survives container rebuilds and `docker compose down`. Running `docker compose down -v` deletes that database volume.

To stop:

```sh
docker compose down
```

On Linux, the host `Logs` directory must be writable by the container user (UID 1000).

Local execution with `python -m booktracker` still defaults to `127.0.0.1:5000` and the project's `data/kobo_deals.db`. The `BOOKTRACKER_HOST` and `BOOKTRACKER_DB_FILE` environment variables override these defaults in Docker.
