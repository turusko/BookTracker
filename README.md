# BookTracker

Track Kobo deals, price changes, and your wishlist.

## Run with Docker on Windows

Start Docker Desktop, stop any local BookTracker process, then double-click
[`scripts/deploy.bat`](scripts/deploy.bat). Open http://localhost:5000.

The first deployment imports `data/kobo_deals.db`. Later deployments keep the
existing Docker database. See [Docker instructions](docs/DOCKER.md).

## Run locally

From this project directory:

```sh
pip install -r requirements.txt
python -m booktracker
```

Local execution uses `data/kobo_deals.db`; Docker uses its persistent named volume.
Configuration lives in `booktracker/config.py`. Logs are written to `Logs/`.

## Project layout

| Folder | Contents |
| --- | --- |
| `booktracker/` | Python application, templates, and static assets |
| `data/` | Local SQLite database (excluded from Docker images and Git) |
| `scripts/` | Windows deployment script and database import helper |
| `docker/` | Dockerfile |
| `tests/` | Automated tests |
| `docs/` | Deployment documentation |
| `Logs/` | Runtime logs |

`compose.yaml` remains at the project root to preserve the existing Compose
project and database volume when deploying updates from this folder.

## Tests

```sh
python -m unittest discover -s tests -t .
```
