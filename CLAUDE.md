# OffBook — notes for Claude

Read `STATUS.md` first: it says which stage is done and what is waiting on the user. The approved plan is at
`C:\Users\joebl\.claude\plans\i-want-to-design-linear-haven.md`.

The skeleton was ported from Trellis (`C:\Users\joebl\source\Trellis`); when something here looks unfinished,
the Trellis equivalent is the pattern to follow.

## Layout

- `src/offbook/` — Python package. `api/` routers, `auth.py` (family password in the `meta` table, scrypt),
  `jobs.py` (SQLite queue + one worker thread, priority: grade > ocr > tts), `llm.py` (NanoGPT, validate/retry,
  usage log, daily cap), `parse.py` (photo of a page -> validated items), `migrations/NNN_*.sql` (applied by
  `PRAGMA user_version`).
- `web/` — SvelteKit **3** SPA. Config is in `vite.config.ts` (there is no `svelte.config.js` in Kit 3).
  Imports use `#lib/...` (package.json `imports`), not `$lib`. State uses Svelte 5 runes; `$app/state`, not stores.
  Never name a variable `state` in a component (it shadows the `$state` rune).
- `tests/` — pytest. `conftest.py` provides `family` (logged in), `kid` (profile picked) and `fake_llm`.

## Auth model

One family password, then a profile picker. `require_family` guards everything; `current_profile` adds "a profile
is selected" and answers 409 `{code: "no_profile"}` when not, which the frontend turns into a redirect to `/who`.
Anything a child owns (casting, rehearsal history) must be scoped to the session's profile.

## Commands

- Tests: `uv run pytest`; frontend: `cd web; npm run check; npm run build`
- Local: `uv run offbook serve --port 8092` (8000 is taken by SillyTavern on the dev PC). Dev password is in
  `data/dev-login.txt` (gitignored).
- `uv run offbook check` — database, password, AI key, Kokoro reachability.
- Deploy: `ssh oci 'cd ~/DockerSource/offbook && git pull && docker compose up -d --build'`

## Rules

- Never render script or AI text with `{@html}`.
- One uvicorn worker only (the job thread must not be duplicated).
- Each page read is a paid AI call. `LLM_DAILY_CAP` bounds it. Ask the user before running `parse-spike` or
  anything else that calls the AI in bulk.
- A child memorises what the parser returns: never "improve" script text anywhere in the pipeline.
- Conventional Commits.
