# Status

Live: https://offbook.terminuscloud.giize.com (OCI, containers `offbook` and `offbook-zonos`, host port 8091, NPM
proxy host #31, cert `npm-46`, expires 2027-02-14). Repo: github.com/zebede1980/OffBook.
Plan: `C:\Users\joebl\.claude\plans\i-want-to-design-linear-haven.md`. Last updated 2026-11-17.

## Stages

| Stage | State |
|---|---|
| 0 Skeleton + deploy + vision spike | **Done. Gate passed** (see below). |
| 1 Scripts in (upload, parse, review/edit) | **Deployed and used on an iPhone.** Page tools added after that test (below). |
| 2 Casting, voices, audio pre-generation | **Deployed.** Now with Zonos as well as Kokoro, and volume levelling. |
| 3 Rehearsal player (tap to continue) | **Deployed and used on an iPhone**: "went well for most functions". |
| 4 Listening / auto-continue | **Deployed.** Gate: tested with the kids on iPhone, Android and laptop. |
| 5 Marking | **Recording deployed** (opt-in, playback on the end screen, 7-day retention). Marking itself waits on the bake-off recordings. |
| 6 Zonos3-Mini trial | **You preferred Zonos (2026-11-17). Now the default engine**, running as its own container. Kokoro stays available per script. |
| 7 Extras | Not started |

## Voice engines (2026-11-17)

- Each script has an engine (Cast tab, "Voices made by"). New scripts use `DEFAULT_TTS_ENGINE` (zonos in prod).
- Switching keeps every character's voice choice on the old engine (`character.saved_voices`), so switching back
  restores them, and the old clips are still on disk.
- Every clip, from either engine, is levelled to -18 LUFS (two-pass ffmpeg loudnorm, linear gain) and stored as
  MP3. This changed every clip's hash, so all audio was regenerated once after this release.
- Zonos voices offered: British & Irish (BritishFemale, Imogen, Rupert, Cillian), American (4), and "character
  voices" (a witch, a cartoon voice, an announcer, a lively one, a whispery one) which are only chosen by hand.
  Automatic assignment uses the 4 women and 4 men, so with many characters some share a voice.
- Zonos speed on the 4-core ARM box: ~1x real-time (warm). It's capped at 3 cores so the rest of the box stays
  responsive; ~1.4 GB RAM. Built from source (`zonos/Dockerfile`, pinned commit); a full rebuild takes ~10 minutes.
- Spike results and the side-by-side samples: `C:\Users\joebl\source\offbook\spike\compare\`.

## Page reading (iPhone test, 2026-11-17)

What went wrong: page 2 of "Alice in Wonderland" came back with no text after using the whole 14,000-token reply
allowance (the model got stuck, not a genuinely huge page). Trying to fix it, all three pages were deleted, which
left an empty script with 12 characters still listed.

Now:
- A reply that runs out of room is tried once more; if that fails too, the photo is cut in two along its emptiest
  line (between the columns, or between rows of text) and each half is read, with a speech across the cut joined.
- A page that still can't be read gets a banner on the script: Try again, Replace photo, See it.
- Pages show their number. Tapping one offers Earlier / Later (its lines move with it), Replace photo (re-read in
  the same place), and Delete (the warning says how many lines go with it).
- Deleting a page closes the gap in the numbering and removes characters left with no lines. The Cast tab has
  "Remove N characters with no lines" for older leftovers (the Alice script has 12).
- Lines tab: search by words or character. During rehearsal, "Fix line" opens that line in the editor, with a
  "Back to rehearsing" button.

## Voice progress

While voices are being made, the script page, the rehearsal setup and the rehearsal itself show how many lines are
ready and roughly how long the rest will take. In rehearsal, a wait of more than ~0.7 s says whose voice is being
made. Audio missing for any reason is queued when progress is checked and when the server starts.

## Stage 0 gate: page reading (2026-11-17)

Four real pages of "The Twelve Days of Christmas" (landscape, two columns, ~95 speeches a sheet, italic
directions without brackets, print showing through, a handwritten name) read with `anthropic/claude-haiku-5.5`:
4 of 4 read first time, 372 lines, 15 characters; pages 1 and 3 checked line by line with no wrong words.
About 2-3p a sheet.

## NanoGPT spend (you authorised 25 calls for testing)

| What | Calls |
|---|---|
| `parse-spike` on page 1 | 1 |
| Four pages through the full pipeline, locally | 4 |
| Same four pages imported on the live server | 4 |
| **Total for testing** | **9 of 25** |

(Your own use since: 3 calls reading Alice, one of which failed.)

## Waiting on you

1. **Try Zonos on the phone**: the Christmas script has been switched to it. Voices take about 15 minutes to make.
2. **Alice**: the script is empty. Add the pages again (or Replace photo on any that fail), and use "Remove 12
   characters with no lines" on the Cast tab first if you like.
3. **Stage 5**: have the kids rehearse with "Move on by itself" and "Record my lines" ticked. About 20 recorded
   lines (any scenes) is enough to compare speech recognisers on their actual voices.

## Not verified

- Zonos voices have only been listened to as the four spike samples; a whole script in Zonos hasn't been heard.
  Short lines come out brisk ("Where do you want these?" in ~1.2 s). The length is the same across seeds, so it
  looks like pace rather than dropped words, but listen for clipped endings. If they're too quick, Zonos has a
  `speaking_rate` condition that could be tried. Its alignment-guarded mode (`laci`) is refused by this model.
- Clips in production measure -18 LUFS ±0.3 after levelling; Zonos makes one every ~3.2 s (about 16 minutes for
  the Christmas script).
- The split-in-halves page reading is tested with synthetic images and a fake AI, not yet on a real stuck page.
- Microphone listening and recording haven't been tried on a phone.

## Known gaps

- iPhone `.heic` files can't be uploaded from a desktop browser (clear message shown). From the phone is fine.
- Group lines ("ALL", "DOVES") are just another character: they aren't treated as everyone's line.
- Clips for deleted lines or replaced voices are never cleaned up (small files; not urgent).
- The photo overlay and rehearsal screen don't trap keyboard focus.
- Zonos has no speed control on its speech endpoint, so per-character speed only applies to Kokoro.

## Things noticed in passing

- Kokoro on the server is published on `0.0.0.0:8880`. If the OCI security list lets that port in, anyone can use it.

## Local dev

`data/dev-login.txt` (gitignored) holds the local family password. Serve on port 8092. `offbook dev-seed` adds a
public-domain demo scene. Add `?quiet=1` to a rehearse URL to play silently. Locally there is no Zonos (Kokoro only)
unless `ZONOS_URL` points at one.
