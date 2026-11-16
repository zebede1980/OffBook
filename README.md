# OffBook

Someone to run your lines with. Photograph the pages of a script, pick your part, and OffBook voices everyone
else, waiting while you say your lines.

Self-hosted, for one family: one shared password, then each child picks their profile.

**Status:** early. See [STATUS.md](STATUS.md) for what works today and what is next.

## How it will work

1. Photograph script pages. A vision model reads them into characters, lines and stage directions; you correct
   anything it got wrong.
2. Pick your character and give the others voices (Kokoro text-to-speech, generated ahead of time so playback
   is instant).
3. Rehearse: script visible, cues only, or fully off book with a hint button. Tap to continue, or let it hear
   when you have finished.
4. Afterwards, see which lines you got right. Speech recognition runs on your own server.

## Stack

- **Backend**: Python 3.12, FastAPI, SQLite, managed with `uv`. One process: web server plus one job thread.
- **Frontend**: SvelteKit static SPA, served by the backend.
- **Needs**: a [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) server, and a NanoGPT API key for
  reading page photos.

## Local development

```powershell
copy .env.example .env          # set OFFBOOK_SECRET_KEY
uv sync
uv run offbook set-password     # the shared family password
uv run offbook check            # database, password, AI key, Kokoro
cd web; npm ci; npm run build; cd ..
uv run offbook serve --port 8092
```

For frontend work, run `npm run dev` in `web/` alongside the server; it proxies `/api` to port 8092.

Tests: `uv run pytest`, and in `web/`: `npm run check`.

## Deploying (Docker)

`docker-compose.yml` is written for a host that already runs nginx-proxy-manager on the `nginx-proxy-nw`
network and a `kokoro-tts` container on `character-generator-network`. Adjust the network names to suit.

```bash
cp .env.example .env            # fill in OFFBOOK_SECRET_KEY and NANOGPT_API_KEY
docker compose up -d --build
docker exec -it offbook offbook set-password
docker exec offbook offbook check
```

Then point a proxy host at `offbook:8000`. It must be served over HTTPS: browsers only allow the camera and
microphone on secure pages.
