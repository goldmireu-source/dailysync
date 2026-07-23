# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Personal AI-news curation service. Pulls Korean + English AI news via RSS and arXiv/HF papers, embeds + clusters them, cross-checks the cluster with an LLM, and renders a Korean digest in a Flask dashboard (email delivery is on the MVP roadmap but not wired up yet). Single-user / small beta; Korean-only UI and prompts.

## Commands

**Required env vars** (`.env`): `ANTHROPIC_API_KEY` is the only one the running pipeline actually needs — `services/claude.py` raises `RuntimeError` without it. The README's quick-start lists `GEMINI_API_KEY` / `GMAIL_*` but those are stale: Gemini is an unused alternate, and Gmail SMTP isn't wired into any job yet. Optional knobs: `ADMIN_TOKEN` (else dev mode = everyone admin), `CLUSTER_SIMILARITY_THRESHOLD` (default 0.80), `COLLECT_DAYS_BACK` (default 0 = today only).

Repo has both `venv\` and `.venv\` directories — `venv\` is the README convention; `.venv\` is an IDE artifact. Either works, just don't create a third.

```powershell
# Setup
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# DB schema + seed RSS sources from data/sources.yaml
# (also the workflow for adding/editing a feed: edit sources.yaml, re-run — UPSERTs by rss_url)
python init_db.py
python init_db.py --reset           # drop-all + recreate (interactive 'yes' confirm)

# Run app (Flask + APScheduler in one process; port 5001, NOT 5000 — README is stale)
python app.py
# → http://localhost:5001/healthz  (dashboard at /, admin at /admin)

# Ad-hoc DB migrations (run once each — idempotent, check for column existence first)
python migrate_first_shown.py
python migrate_glossary.py
python migrate_hidden.py
python migrate_paper_title_ko.py
python migrate_saved.py

# Inspection / debug utilities (read-only, pretty-print to stdout)
python inspect_clusters.py [--min N] [--full]
python inspect_summaries.py [--n N] [--multi] [--papers|--clusters]
```

No test suite, no linter, no formatter configured. Don't invent one.

**Running one pipeline step ad-hoc** — there is no CLI for individual jobs. Two options:
- With the app running, POST `/admin/run/<job_id>` (e.g. `summarize_news`, `embed_and_cluster`); see "Job ID mapping" below.
- Without the app running, push an app context manually:
  ```powershell
  python -c "from app import create_app; from jobs.pipeline import job_summarize_news; app=create_app(with_scheduler=False); ctx=app.app_context(); ctx.push(); job_summarize_news(triggered_by='manual')"
  ```
  Pass `with_scheduler=False` to `create_app()` so the cron jobs don't also fire.

## Architecture

### One process, two roles
`app.py:create_app()` builds the Flask app **and** starts an APScheduler `BackgroundScheduler` (KST) in-process. The scheduler runs jobs in the same Python process — there is no Celery, no worker queue. `app.run(debug=False)` is intentional: Werkzeug's reloader would otherwise spawn the scheduler twice (`scheduler.py:init_scheduler` has a `WERKZEUG_RUN_MAIN` guard but the simpler rule is just leave debug off and restart manually).

### The daily pipeline (KST)
`scheduler.py` registers four cron jobs. The 6-hour `refresh_6h` is the main one — the morning batch was collapsed into it so the 06:00 run produces the 08:00 digest:

- `08–22:00` hourly — `job_collect_news` (RSS via feedparser + requests) — keeps articles fresh between refresh runs
- `08–22:00` every 2h at :05 — `job_fetch_bodies` (trafilatura, best-effort, stores in `Article.body` for **analysis only** — never rendered)
- `00, 06, 12, 18:00` — `job_refresh_now` (composed: collect_news → collect_papers → [skip if no_changes ∧ no dirty queue] → fetch_bodies → embed_and_cluster → summarize_news → summarize_papers).
- `04:00` daily — `job_cleanup_old_data` (`jobs/cleanup.py`): deletes Article/Cluster/Paper/TechPost rows older than 4 days by `published_at`. **Saved items are preserved**: any `Cluster.saved_at IS NOT NULL` keeps the cluster + ALL its articles regardless of age; any `Paper.saved_at IS NOT NULL` is kept too; any `TechPost.saved_at IS NOT NULL` or `hidden_at IS NOT NULL` is kept (hidden is also an exception, unlike the other tracks). Default retention is 4 days; pass `retention_days=N` to `job_cleanup_old_data()` for ad-hoc runs.

`jobs/pipeline.py` is the single dispatch layer. Every job is wrapped in the `_track()` context manager which writes a `JobRun` row (queued → running → success/failed) with `stats` JSON. The admin dashboard and the one-shot "refresh now" button (`job_refresh_now`) both pre-create a `JobRun` via `create_job_run()` and pass its `run_id` down so the frontend can poll a known row. `_update_phase()` writes the current phase string into `stats.phase` mid-run for UI polling.

### Data model spine (`models.py`)
- `Source` → `Article` → `Cluster` (an Article belongs to at most one Cluster; clustering is incremental — centroid is a running mean).
- `Paper` is a **completely separate track** from news clustering. Don't try to unify them.
- `Cluster.summary_dirty` / `Paper.summary_dirty` is the "needs re-summarization" flag the summarizer jobs filter on.
- `hidden_at` / `saved_at` are nullable timestamps (NULL = default, value = state set). The dashboard hides/saves via the `/api/cluster/<id>/{hide,show,save,unsave}` and matching paper routes.
- `Cluster.first_shown_date` prevents the same cluster from appearing on multiple days — set the first time it's displayed.
- `GlossaryTerm` powers the `/glossary` page; seed from `data/glossary_seed.json` via `migrate_glossary.py`.
- `Digest` exists for future email delivery; not yet populated by any job.

### LLM / embedding services (`services/`)
- `services/local_embed.py` — **BGE-M3 via sentence-transformers**, the default and only embedder used in the pipeline. Heavy first load (downloads to `hf_cache/`).
- `services/claude.py` — Anthropic SDK, used for all summarization. Has a 1.2s throttle (`CLAUDE_MIN_INTERVAL`) for Tier-1 50/min limit. Model default `claude-haiku-4-5` (see `Config.CLAUDE_SUMMARY_MODEL`). `generate_json()` is the main entrypoint and includes `_extract_json()` regex fallback because Anthropic has no JSON-mode.
- `services/gemini.py`, `services/voyage.py` — fallback/alternate providers, currently unused but kept with matching interfaces (`generate_json`). Don't delete without checking imports.

### News collection policy (`jobs/news_collector.py`)
Sources have a `tier` and `needs_ai_filter` flag. Tier-2 (OpenAI/Anthropic/DeepMind first-party blogs) skip the keyword filter; Tier-1 generalist outlets must match the AI keyword list. Window defaults to today only (`COLLECT_DAYS_BACK=0`). Dedup is via SHA256 of URL in `Article.url_hash`.

### Web (`web/routes.py`, ~566 lines, single blueprint)
All routes live in `web/routes.py` registered as the `web` blueprint. Three groups: digest views (`/`, `/cluster/<id>`, `/paper/<id>`, `/saved`, `/glossary`), hide/save/restore JSON APIs under `/api/...`, and admin (`/admin`, `/admin/run/<job_id>`, `/api/job/...`).

**Admin auth model**: `Config.ADMIN_TOKEN` empty → dev mode, everyone is admin. Set → set cookie via `/admin-login?token=xxx`, checked by the `admin_required` decorator. The decorator returns 403 JSON for `/api/*` and `/admin/run/*`, otherwise redirects to `/`.

`web/cardnews.py` builds the card-format payload for templates (`templates/_slides.html`, `cardnews.html`).

### Job ID mapping
`scheduler.trigger_job_now(job_id, app, run_id)` is the single entry point for manual triggers. It accepts these `job_id` keys: `collect_news`, `fetch_bodies`, `collect_papers`, `embed_and_cluster`, `summarize_news`, `summarize_papers`, `morning_pipeline`, `refresh_now`. The admin UI's `JOB_LABELS` dict (in `web/routes.py`) is the source of truth for what's user-triggerable.

## Project rules (from README, non-negotiable)

1. **No article body in emails or UI** — `Article.body` is collected via trafilatura for clustering/summarization input only. Email and dashboard render *only* headline + LLM summary + source link. This is a legal constraint, not a style choice.
2. RSS and official APIs only. No scraping for body beyond what trafilatura extracts from the source's own page.
3. All recipients opt-in; every email needs an unsubscribe token (`User.unsubscribe_token`).

## Conventions worth knowing

- All comments and prompts are in **Korean**. Keep them that way when editing.
- Time-of-day logic is **KST** (`timezone(timedelta(hours=9))` defined in both `scheduler.py` and `web/routes.py`). Timestamps in the DB are naive UTC (`datetime.utcnow()`).
- `db.create_all()` runs on app startup, so adding a column to `models.py` doesn't migrate existing rows — write a `migrate_*.py` SQLite script (see existing ones for the pattern: check `PRAGMA table_info`, `ALTER TABLE ADD COLUMN` if missing).
- `from app import create_app` inside jobs is intentional — many CLI utilities push their own app context this way.
