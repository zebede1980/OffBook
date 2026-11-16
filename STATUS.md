# Status

Not deployed yet. Plan: `C:\Users\joebl\.claude\plans\i-want-to-design-linear-haven.md`. Last updated 2026-11-16.

## Stages

| Stage | State |
|---|---|
| 0 Skeleton + deploy + vision spike | **Skeleton built and tested locally.** Deploy and the spike are waiting on you (below). |
| 1 Scripts in (upload, parse, review/edit) | Not started. The page parser itself (`parse.py`) exists and is unit-tested against a fake AI. |
| 2 Casting, voices, audio pre-generation | Not started |
| 3 Rehearsal player (tap to continue), PWA | Not started |
| 4 Listening / auto-continue | Not started |
| 5 Marking | Not started |
| 6 Zonos3-Mini trial | Not started |
| 7 Extras | Not started |

## What works today

- Family password login (throttled), profile picker, per-device profile selection.
- Job queue with priorities, AI client with validation/retry, usage log and daily cap.
- `offbook parse-spike <photos>`: reads page photos and prints who-says-what. Not yet run against the real AI.
- `offbook check`: reports database, password, AI key and Kokoro reachability. Kokoro is reachable locally.

## Waiting on you

1. **Three or four real script photos** for the Stage 0 gate, and a go-ahead for the paid calls (one per page,
   two if a reply fails validation). Drop them in `spike/` (gitignored). Phone photos need shrinking below
   3.5 MB first; I can do that.
2. **NanoGPT key** for this project's `.env`: reuse Trellis's, or a separate one?
3. **Deploy approvals**: create the GitHub repo and push; create the NPM proxy host and certificate
   (suggested `offbook.terminuscloud.giize.com`).

## Not verified

- The web UI has only been type-checked and built; the login/profile flow was exercised over HTTP, not looked
  at in a browser.
- The Docker image has not been built on ARM.
- Whether NanoGPT accepts the OpenAI-style `image_url` data-URL for Claude models. The spike will show.

## Known gaps

- No UI yet for renaming or deleting a profile, or changing its patience (the API exists).
- PWA manifest and icons arrive with Stage 3.

## Local dev

`data/dev-login.txt` (gitignored) holds the local family password. Serve on port 8092.
