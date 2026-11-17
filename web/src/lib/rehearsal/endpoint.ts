// Deciding when a child has finished saying their line, from a stream of "is this speech?" probabilities.
//
// "Silence means done" is not good enough: actors pause for effect, and children stop to think. So the wait
// depends on how much has been said compared with how long the line should take:
//
//   - nothing said yet            -> never move on
//   - clearly not enough said     -> never move on; after a long silence, suggest a hint
//   - enough said, then a pause   -> move on after a short silence
//
// It is a pure function of its inputs, so it can be tested with made-up timelines. The Continue button always
// works whatever this decides.

export interface EndpointConfig {
  /** Probability at or above which a frame counts as speech. */
  speechOn: number;
  /** Probability below which a frame counts as silence. Between the two, the previous state carries on. */
  speechOff: number;
  /** A burst of sound shorter than this is a cough or a click, not the start of the line. */
  minSpeechMs: number;
  /** Silence needed to move on once enough has been said. */
  shortSilenceMs: number;
  /** Silence after which, if not enough has been said, a hint is suggested. */
  longSilenceMs: number;
  /** How much of the line's expected duration must be spoken before a short pause counts as the end. */
  enoughFraction: number;
  /** Expected speaking pace. Deliberately brisk: children rattle lines off, and guessing too slow would make
   *  the app sit waiting after they have finished. */
  wordsPerSecond: number;
}

export const DEFAULTS: EndpointConfig = {
  speechOn: 0.5,
  speechOff: 0.35,
  minSpeechMs: 200,
  shortSilenceMs: 900,
  longSilenceMs: 3000,
  enoughFraction: 0.5,
  wordsPerSecond: 3
};

export interface EndpointState {
  /** Confirmed speech so far in this turn. */
  speechMs: number;
  /** Length of the current silence. */
  silenceMs: number;
  /** Length of the current unbroken run of sound, until it is long enough to count. */
  runMs: number;
  /** A run of sound is in progress and has been counted as speech. */
  inSpeech: boolean;
}

export type Verdict =
  | { kind: 'waiting' } // nothing said yet
  | { kind: 'speaking' }
  | { kind: 'pausing'; progress: number } // enough said; 0..1 of the way to moving on
  | { kind: 'thinking' } // not enough said, and quiet, but not for long yet
  | { kind: 'nudge' } // not enough said and a long silence: offer a hint
  | { kind: 'advance' };

export const newState = (): EndpointState => ({ speechMs: 0, silenceMs: 0, runMs: 0, inSpeech: false });

/** How much speech (ms) this line needs before a short pause is taken as the end. */
export function enoughMs(words: number, cfg: EndpointConfig = DEFAULTS): number {
  // One- and two-word lines ("No." "Who?") are over almost before they are detected: any real speech will do.
  if (words <= 2) return cfg.minSpeechMs;
  return Math.max(cfg.minSpeechMs, (words / cfg.wordsPerSecond) * 1000 * cfg.enoughFraction);
}

/**
 * Feed one audio frame. `patience` scales the silences (1 = normal; 2 waits twice as long).
 * Mutates and returns `state`, with the verdict as of this frame.
 */
export function step(
  state: EndpointState,
  prob: number,
  frameMs: number,
  words: number,
  patience = 1,
  cfg: EndpointConfig = DEFAULTS
): Verdict {
  const sound = prob >= cfg.speechOn || (prob >= cfg.speechOff && (state.inSpeech || state.runMs > 0));
  if (sound) {
    state.runMs += frameMs;
    state.silenceMs = 0;
    if (state.inSpeech) {
      state.speechMs += frameMs;
    } else if (state.runMs >= cfg.minSpeechMs) {
      state.inSpeech = true;
      state.speechMs += state.runMs; // the run has proved itself: count all of it
    }
  } else {
    state.runMs = 0;
    state.inSpeech = false;
    state.silenceMs += frameMs;
  }

  if (state.speechMs === 0) return { kind: 'waiting' };
  if (state.inSpeech || state.silenceMs === 0) return { kind: 'speaking' };
  if (state.speechMs >= enoughMs(words, cfg)) {
    const need = cfg.shortSilenceMs * patience;
    return state.silenceMs >= need ? { kind: 'advance' } : { kind: 'pausing', progress: state.silenceMs / need };
  }
  return state.silenceMs >= cfg.longSilenceMs * patience ? { kind: 'nudge' } : { kind: 'thinking' };
}

export function countWords(text: string): number {
  return text.split(/\s+/).filter(Boolean).length;
}
