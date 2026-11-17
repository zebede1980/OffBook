# OffBook — notes for Claude

Read `STATUS.md` first: it says which stage is done and what is waiting on the user. The approved plan is at
`C:\Users\joebl\.claude\plans\i-want-to-design-linear-haven.md`.

The skeleton was ported from Trellis (`C:\Users\joebl\source\Trellis`); when something here looks unfinished,
the Trellis equivalent is the pattern to follow.

## Layout

- `src/offbook/` — Python package.
  - `api/` routers: `auth.py` (login, profiles), `scripts.py` (scripts, pages, review edits), `audio.py` (voices,
    casting, rehearsal payload, clips).
  - `auth.py` — family password (scrypt hash in the `meta` table).
  - `jobs.py` — SQLite queue + one worker thread. Priority: grade > ocr > tts; an awaited clip is bumped to 100.
  - `llm.py` — NanoGPT client, validate/retry, usage log, daily cap.
  - `parse.py` — photo of a page -> validated items. The prompt was tuned on real pages; see STATUS before changing it.
  - `scripts.py` — pages -> ordered lines (joins across pages, name matching), review edits, the `ocr` job.
  - `tts.py` — speech engines behind one small interface (Kokoro today). `audio.py` — voice assignment, clip
    hashing, the `tts` job.
  - `migrations/NNN_*.sql` — applied by `PRAGMA user_version`.
- `web/` — SvelteKit **3** SPA. Config is in `vite.config.ts` (there is no `svelte.config.js` in Kit 3).
  Imports use `#lib/...` (package.json `imports`), not `$lib`. State uses Svelte 5 runes; `$app/state`, not stores.
  Never name a variable `state` in a component (it shadows the `$state` rune).
  - `lib/rehearsal/clips.ts` (Web Audio playback), `endpoint.ts` (pure auto-continue rule, unit-tested),
    `listener.ts` (microphone -> speech probability per frame).
  - `scripts/copy-vad.mjs` copies the voice detector's wasm/model into `static/vad/` before dev and build.
- `tests/` — pytest. `conftest.py` provides `family` (logged in), `kid` (profile picked), `fake_llm`, and an
  autouse `fake_tts`, so no test touches the network.

## Auth model

One family password, then a profile picker. `require_family` guards everything; `current_profile` adds "a profile
is selected" and answers 409 `{code: "no_profile"}` when not, which the frontend turns into a redirect to `/who`.
Scripts, voices and audio are shared by the family. Casting (and later, rehearsal history) is per profile.

## Commands

- Tests: `uv run pytest`; frontend: `cd web; npm run check; npm test; npm run build`
- Local: `uv run offbook serve --port 8092` (8000 is taken by SillyTavern on the dev PC). Dev password is in
  `data/dev-login.txt` (gitignored). `offbook dev-seed` adds a public-domain demo scene.
- `uv run offbook check` — database, password, AI key, Kokoro reachability.
- Deploy: `git push`, then `ssh oci 'cd ~/DockerSource/offbook && git pull && docker compose up -d --build'`.
  Queued jobs survive the restart.
- Rehearse silently while testing: add `?quiet=1` to the rehearse URL. `/selftest` checks the voice detector.

## Rules

- Never render script or AI text with `{@html}`.
- One uvicorn worker only (the job thread must not be duplicated).
- Each page read is a paid AI call. `LLM_DAILY_CAP` bounds it. Ask the user before running `parse-spike`,
  `import-pages` or anything else that calls the AI in bulk; record spend in STATUS.md.
- A child memorises what the parser returns: never "improve" script text anywhere in the pipeline.
- Do not log in to the live site: the family password is the user's. Verify production through `/api/health`,
  `docker exec offbook offbook check`, and read-only looks at the database.
- Bash heredocs containing apostrophes break in this harness: write patch scripts to a file and run that.
- Conventional Commits.
