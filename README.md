# rankup.ai — CS2 Decision Coach

<img width="859" height="461" alt="rankup" src="https://github.com/user-attachments/assets/f6a241d9-a787-4a94-a54a-4c5e632db45d" />

rankup.ai is an AI post-game coach for Counter-Strike 2. Upload one of your own match demos and it finds the specific rounds where a decision cost you — not just another kills/deaths spreadsheet — and tells you what you should have done instead.

It answers one question, round by round:

> **"Given what the enemy did, what should I have done differently?"**

For each flagged round it shows the enemy action, your response, the outcome, and the better decision. Every analyzed demo is stored in Redis, so it can also flag when you're repeating a mistake across matches.

---

## How it works

```
  Your CS2 demo (.dem / .json)
            │
            ▼
   ┌──────────────────┐
   │      Parser      │   Reads rounds, deaths, positions,
   │  (demoparser2)   │   utility usage, bomb plants
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │     Detectors    │   Rule-based checks for passive utility
   │   (rule-based)   │   response, isolated deaths, early
   │                  │   overrotation, wasted utility
   └────────┬─────────┘
            ▼
   ┌──────────────────┐     ┌──────────────────────┐
   │   Redis memory   │◄───►│  Similar-mistake      │  Finds matching
   │  (your history)  │     │  retrieval (vector)   │  patterns from your past
   └────────┬─────────┘     └──────────┬────────────┘
            └───────────┬──────────────┘
                        ▼
              ┌──────────────────┐
              │      Coach       │   Writes the coaching summary
              │ (Claude or local │   and practice drills
              │    fallback)     │
              └────────┬─────────┘
                       ▼
              ┌──────────────────┐
              │     React UI     │   The report you read
              └──────────────────┘
```

The demo is parsed into structured facts, four rule-based detectors flag specific decision mistakes, Redis is checked for similar past mistakes by the same player, and a coach step (Claude if an API key is set, otherwise a deterministic local generator) writes the final summary and drills. The whole pipeline runs even without Redis or an API key — it just skips the memory/pattern features and falls back to the local coach.

---

## Tech stack

| Layer | Technology |
|---|---|
| **Frontend** | React 18 + TypeScript, built with Vite 6 |
| **Backend** | Python + FastAPI (Uvicorn) |
| **Data models** | Pydantic v2 |
| **Demo parsing** | `demoparser2` (real CS2 `.dem` files) |
| **Map zones** | Deterministic coordinate/callout resolver, optionally backed by `awpy` nav meshes. Currently only Mirage has canonical zone data. |
| **Memory & retrieval** | Redis (RediSearch vector index when available, brute-force cosine fallback otherwise) over a simple local hashed-text embedding — no external embedding API |
| **AI coaching** | Anthropic Claude (optional — a deterministic local coach is used if no `ANTHROPIC_API_KEY` is set) |
| **Observability** | Console-logged pipeline traces + a heuristic groundedness check on the coach's output |
| **Deployment** | Docker Compose (backend + nginx-served frontend), talking to a managed cloud Redis |

---

## Run it locally

**1. Redis** (optional — the coach's memory)
```bash
docker compose up -d redis
```
> The app still runs if Redis is down; it just skips memory and recurring-pattern features.

**2. Backend**
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**3. Frontend**
```bash
cd frontend
npm install
npm run dev
```

Then open **http://localhost:5173** and upload a `.dem` or already-parsed `.json` demo.

### Configuration

Set these as environment variables (or in a `.env` file the backend/Docker Compose can read). Everything has a sensible default and the API key is **optional**:

| Variable | Default | Purpose |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection string. |
| `ANTHROPIC_API_KEY` | _(unset)_ | If set, Claude writes the coaching summary. If unset, a local fallback coach is used. |
| `ANTHROPIC_MODEL` | `claude-opus-4-8` | Claude model used when a key is present. |
| `VITE_BACKEND_URL` | `http://localhost:8000` | Frontend → backend URL. |

---

## API reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Backend + Redis status. |
| `POST` | `/api/analyze/sample` | Run the pipeline on the bundled Mirage sample demo. |
| `POST` | `/api/analyze/upload` | Upload a `.dem`/`.json` demo and run the pipeline. |
| `GET` | `/api/player/{player_id}/memory` | Retrieve a player's stored mistake history. |

---

## Possible improvements

- Map-specific coaching knowledge for maps beyond Mirage.
- More accurate map positioning knowledge.
- More detailed rule based detectors.
- Positional heatmaps from parsed tick data.
- Direct FACEIT / share-code demo import — paste a code instead of a file.
- Richer UI: round timelines and filters.
