# Deploying CS2 Decision Coach

The app is containerized into two services wired to **cloud Redis** (no local
Redis needed):

- **backend** — FastAPI + uvicorn (`backend/Dockerfile`), talks to cloud Redis.
- **frontend** — Vite build served by nginx (`frontend/Dockerfile`), which also
  reverse-proxies `/api` and `/health` to the backend. Because of this proxy the
  browser only ever talks to one origin — no CORS config or hardcoded backend URL.

## 1. Configure `.env`

`.env` (repo root, git-ignored) drives both containers via `env_file`:

```
REDIS_URL=rediss://default:<password>@<host>:<port>   # rediss:// = TLS
REDIS_REQUIRED=1                                       # fail fast if Redis is down
ANTHROPIC_API_KEY=...                                  # optional
ANTHROPIC_MODEL=claude-sonnet-4-6                      # optional
USE_MOCK_DEM_PARSER=                                   # leave empty for real .dem parsing
```

> The provided cloud URL uses `rediss://` (TLS). redis-py verifies the server
> cert against the system CA store. If your provider serves a cert that fails
> verification, append `?ssl_cert_reqs=none` to `REDIS_URL`.

## 2. Build & run

```bash
docker compose up -d --build
```

- Frontend (UI): http://localhost:8080
- Backend (direct, optional): http://localhost:8000/health

Stop with `docker compose down`.

## Notes

- The frontend image is built with `VITE_BACKEND_URL=""` (set in
  `docker-compose.yml`), so the SPA uses same-origin relative paths and nginx
  proxies them to the backend. To point the UI at a backend on a different
  origin, set that build arg to an absolute URL instead.
- nginx allows uploads up to 300 MB (`client_max_body_size`) for large `.dem`
  files; raise it in `frontend/nginx.conf` if needed.
- Cloud Redis only needs the RediSearch module for vector-KNN similarity. If the
  managed instance lacks it, the app automatically falls back to brute-force
  cosine search — no config change required.
