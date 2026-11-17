# Status

Live: https://offbook.terminuscloud.giize.com (OCI, container `offbook`, host port 8091, NPM proxy host #31,
cert `npm-46`, expires 2027-02-14). Repo: github.com/zebede1980/OffBook.
Plan: `C:\Users\joebl\.claude\plans\i-want-to-design-linear-haven.md`. Last updated 2026-11-17.

## Stages

| Stage | State |
|---|---|
| 0 Skeleton + deploy + vision spike | **Done. Gate passed** (see below). |
| 1 Scripts in (upload, parse, review/edit) | **Built and deployed.** Gate: you import and correct a real scene on a phone. |
| 2 Casting, voices, audio pre-generation | **Built and deployed.** Gate: you listen and decide the voices are acceptable. |
| 3 Rehearsal player (tap to continue) | **Built and deployed**, PWA install still to do. Gate: the kids use it for real. |
| 4 Listening / auto-continue | **Built and deployed.** Gate: tested with the kids on iPhone, Android and laptop. |
| 5 Marking | **Recording built and deployed** (opt-in, playback on the end screen, 7-day retention). Marking itself waits on the bake-off recordings. |
| 6 Zonos3-Mini trial | **Speed passed** (below). Quality: waiting for you to listen. |
| 7 Extras | Not started |

## Stage 0 gate: page reading (2026-11-17)

Four real pages of "The Twelve Days of Christmas" (landscape, two columns, ~95 speeches a sheet, italic
directions without brackets, print showing through, a handwritten name) read with `anthropic/claude-haiku-5.5`:

- 4 of 4 pages read first time: 372 lines, 15 characters, page numbers 1-4, clean joins between pages.
- Pages 1 and 3 checked against the photos line by line: no wrong words found. Directions that trail a speech
  ("Silence.") come out as their own stage direction.
- About 4.7k tokens in and 5.5k out per sheet, ~22 s each. That is nearer 2-3p a sheet than the "penny or less"
  first estimated; NanoGPT's actual charge is not visible from here.
- First attempt would have been cut off at the original 6,000-token reply limit. Fixed: compact output, limit 14,000.

Not tested: a badly lit or skewed photo, highlighter marks, a speech split across a page break (none in these pages).

## Stage 6: Zonos3-Mini on the server (2026-11-17)

Built from source (ggml, CPU) in a throwaway container `zonos-spike` on OCI: no build problems on ARM, 8 minutes.
Default quantised model (backbone-ptq, 0.58 GB), ~1.4 GB RAM while running.

| | Kokoro (live) | Zonos3-Mini (warm server) |
|---|---|---|
| Speed on the 4-core ARM box | ~real-time | 0.94-1.17x real-time |
| British voices | 8 (4 F, 4 M) | 3 stock (BritishFemale, Imogen, Rupert); more by cloning or steering age/accent |
| Child voices | none | none stock; can be steered |

Speed is fine either way, because all audio is generated ahead of time. Model loading takes ~17 s, so Zonos
would have to run as a permanent server container (~1.4 GB RAM), not the CLI.

**Listen and decide:** `C:\Users\joebl\source\offbook\spike\compare\` has the same four lines from each engine
(`kokoro_*.mp3`, `zonos_*.wav`). If Zonos is clearly better, the next step is a `tts/zonos.py` engine and a
per-script engine choice. The spike container is stopped, not deleted: `docker start zonos-spike` brings it back.

## NanoGPT spend (you authorised 25 calls)

| What | Calls |
|---|---|
| `parse-spike` on page 1 | 1 |
| Four pages through the full pipeline, locally | 4 |
| Same four pages imported on the live server | 4 |
| **Total** | **9 of 25** |

## Waiting on you

1. **Family password**: generated, in `~/DockerSource/offbook/initial-password.txt` on the server. Change it with
   `docker exec -it offbook offbook set-password` (that signs every device out), then delete the file.
2. **Stage gates 1-3**: try it on a phone. Add the kids as profiles, pick parts, listen to the voices, rehearse.
3. **Stage 5**: have the kids rehearse with "Move on by itself" and "Record my lines" ticked. About 20 recorded
   lines (any scenes) is enough to compare speech recognisers on their actual voices.
4. **Stage 6**: listen to the Kokoro vs Zonos samples (above) and say which you prefer.

## Not verified

- Nothing has been tried on a real phone. In particular: the camera/gallery upload, sound starting after the
  Start tap on iOS, and the screen staying awake.
- The rehearsal player was tested in desktop Chrome with the sound off (`?quiet=1`), so the voices themselves
  have not been listened to by anyone.
- Uploading through the browser (photo shrinking) has only been type-checked; pages so far went in via the API.

## Known gaps

- Pages can't be reordered after upload; delete and re-add.
- iPhone `.heic` files can't be uploaded from a desktop browser (clear message shown). From the phone is fine.
- Group lines ("ALL", "DOVES") are just another character: they aren't treated as everyone's line.
- Clips for deleted lines or replaced voices are never cleaned up (small files; not urgent).
- The photo overlay and rehearsal screen don't trap keyboard focus.
- Speed per character can be set through the API but has no control on screen.

## Things noticed in passing

- Kokoro on the server is published on `0.0.0.0:8880`. If the OCI security list lets that port in, anyone can use it.

## Local dev

`data/dev-login.txt` (gitignored) holds the local family password. Serve on port 8092. `offbook dev-seed` adds a
public-domain demo scene. Add `?quiet=1` to a rehearse URL to play silently.
