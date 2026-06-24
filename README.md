# rankup.ai — CS2 Decision Coach

<img width="859" height="461" alt="rankup" src="https://github.com/user-attachments/assets/f6a241d9-a787-4a94-a54a-4c5e632db45d" />

**Most Counter-Strike stats tools tell you _what_ happened. rankup.ai tells you what you should have _done_.**

rankup.ai is an AI post-game coach for Counter-Strike 2. You upload one of your own match demos, and instead of another spreadsheet of kills and deaths, you get something a real coach would give you: the specific moments where a decision cost you the round, and the better play you should have made.

It answers one question, round by round:

> **"Given what the enemy did, what should I have done differently?"**

---

## What it looks like

For each costly round, rankup.ai breaks down the decision like a coach reviewing the tape with you:

| | |
|---|---|
| **Enemy action** | CT smoked A ramp early. |
| **Your response** | You waited outside A for 28 seconds. |
| **Outcome** | Your team lost map control and executed late. |
| **Better decision** | When early A utility stalls you, don't freeze in the choke. Take mid control, reset, or prepare a late split. |

And because it remembers every demo you've ever uploaded, it can tell you when you're making the **same mistake again** — _"this echoes a pattern from your last three matches; fix it first."_

---

## Why it's different

- **Decision-focused, not stat-focused.** It coaches judgment — rotations, utility timing, over-commitment — not just aim and K/D.
- **It's about _you_, not your opponents.** This isn't an enemy-scouting tool. It analyzes your own play and your own recurring habits.
- **It has a memory.** Every mistake is stored and matched against your history, so coaching gets sharper the more you use it.
- **It always works.** The full pipeline runs even with no AI key and no database connection — it just gracefully drops to a built-in coach and skips the long-term memory features.

---

## How it works

```
  Your CS2 demo (.dem / .json)
            │
            ▼
   ┌──────────────────┐
   │      Parser      │   Reads real rounds, deaths, positions,
   │  (demoparser2)   │   utility usage, bomb plants, map callouts
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │     Detectors    │   Flags decision mistakes:
   │   (rule-based)   │   passive utility response, isolated death,
   │                  │   early overrotation, utility inefficiency
   └────────┬─────────┘
            ▼
   ┌──────────────────┐     ┌──────────────────────┐
   │   Redis memory   │◄───►│  Similar-mistake      │  Finds matching
   │  (your history)  │     │  retrieval (vector)   │  patterns from your past
   └────────┬─────────┘     └──────────┬────────────┘
            └───────────┬──────────────┘
                        ▼
              ┌──────────────────┐
              │      Coach       │   Writes the coaching report
              │ (Claude or local │   and practice drills
              │    fallback)     │
              └────────┬─────────┘
                       ▼
              ┌──────────────────┐
              │     React UI     │   The report you read
              └──────────────────┘
```

In plain terms: we turn your raw demo into structured facts about the match, run those facts through detectors that spot bad decisions, check whether you've made similar mistakes before, and hand it all to an AI coach that writes the final feedback and drills.

---

## Tech stack

| Layer | Technology | What it does for us |
|---|---|---|
| **Frontend** | React 18 + TypeScript, built with Vite 6 | The single-page web app where you upload a demo and read your coaching report. |
| **Backend** | Python + FastAPI (Uvicorn server) | The analysis pipeline and REST API tying everything together. |
| **Data models** | Pydantic v2 | Type-safe, validated data flowing through every pipeline stage. |
| **Demo parsing** | `demoparser2` | Parses real CS2 `.dem` files into rounds, deaths, utility, and positions. |
| **Map intelligence** | `awpy` (nav meshes) | Resolves player positions into real map callouts; falls back gracefully if unavailable. |
| **Memory & retrieval** | Redis (RediSearch vector index) | Stores every decision moment and finds recurring mistakes by similarity — the coach's long-term memory. |
| **AI coaching** | Anthropic Claude (Opus 4.8) | Writes the natural-language coaching summary and drills. Optional — a deterministic local coach covers it if no key is set. |
| **Observability** | Arize-style tracing + groundedness eval | Traces each pipeline stage and sanity-checks the coach's output for trustworthiness. |
| **Deployment** | Docker Compose (backend + nginx-served frontend) | One-command containerized deploy; talks to managed cloud Redis. |

---

## Run it locally

You need three terminals.

**1. Redis** (the coach's memory)
```bash
docker compose up -d redis
```
> The app still runs if Redis is down — it just skips memory and recurring-pattern features.

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

Then open **http://localhost:5173**, upload a demo (or click **Analyze Sample Demo**), and read your report.

### Configuration

Copy `.env.example` to `.env`. Everything has a sensible default and the AI key is **optional**:

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

## Where we're headed

- Map-specific coaching knowledge (callouts and timings tuned per map).
- Positional heatmaps from parsed tick data.
- Direct FACEIT / share-code demo import — paste a code instead of a file.
- Richer UI: round timelines and filters.
