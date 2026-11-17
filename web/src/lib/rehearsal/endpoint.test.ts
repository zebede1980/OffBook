import { describe, expect, it } from 'vitest';
import { countWords, enoughMs, newState, step, type Verdict } from './endpoint';

const FRAME = 32; // ms, the voice detector's frame length
const SPEECH = 0.9;
const QUIET = 0.02;

/** Play a timeline of [probability, milliseconds] spans and return the verdict after each frame. */
function play(words: number, spans: [number, number][], patience = 1): Verdict[] {
  const state = newState();
  const out: Verdict[] = [];
  for (const [prob, ms] of spans) {
    for (let t = 0; t < ms; t += FRAME) out.push(step(state, prob, FRAME, words, patience));
  }
  return out;
}

const kinds = (v: Verdict[]) => [...new Set(v.map((x) => x.kind))];
const last = (v: Verdict[]) => v[v.length - 1];

describe('auto-continue', () => {
  it('never moves on if nothing has been said', () => {
    expect(kinds(play(10, [[QUIET, 20000]]))).toEqual(['waiting']);
  });

  it('moves on about a second after a whole line', () => {
    // 12 words should take ~4 s; they take 3.5 s
    const v = play(12, [[QUIET, 500], [SPEECH, 3500], [QUIET, 1000]]);
    expect(last(v).kind).toBe('advance');
    // ...but not after only half a second of quiet
    const early = play(12, [[QUIET, 500], [SPEECH, 3500], [QUIET, 480]]);
    const e = last(early);
    expect(e.kind).toBe('pausing');
    expect(e.kind === 'pausing' && e.progress).toBeGreaterThan(0.45);
    expect(e.kind === 'pausing' && e.progress).toBeLessThan(0.65);
  });

  it('copes with a child who rattles the line off', () => {
    // 12 words in 2.2 s (over 5 words a second)
    expect(last(play(12, [[SPEECH, 2200], [QUIET, 1000]])).kind).toBe('advance');
  });

  it('waits through a dramatic pause early in a long line', () => {
    // 30 words (~10 s). One second in, a two-second pause. That must not count as the end.
    const v = play(30, [[SPEECH, 1000], [QUIET, 2000]]);
    expect(kinds(v)).not.toContain('advance');
    expect(last(v).kind).toBe('thinking');
    // carrying on and finishing does move on
    const whole = play(30, [[SPEECH, 1000], [QUIET, 2000], [SPEECH, 6000], [QUIET, 1000]]);
    expect(last(whole).kind).toBe('advance');
  });

  it('suggests a hint, and still does not move on, when they dry up', () => {
    const v = play(30, [[SPEECH, 1000], [QUIET, 12000]]);
    expect(kinds(v)).not.toContain('advance');
    expect(last(v).kind).toBe('nudge');
    // the nudge comes at about three seconds, not straight away
    const at2s = play(30, [[SPEECH, 1000], [QUIET, 2000]]);
    expect(last(at2s).kind).toBe('thinking');
  });

  it('ignores a cough or a click', () => {
    expect(kinds(play(10, [[SPEECH, 100], [QUIET, 5000]]))).toEqual(['waiting']);
  });

  it('accepts a one-word line', () => {
    expect(last(play(1, [[QUIET, 300], [SPEECH, 260], [QUIET, 1000]])).kind).toBe('advance');
  });

  it('starts the countdown again if they speak during it', () => {
    const v = play(6, [[SPEECH, 1500], [QUIET, 700], [SPEECH, 400], [QUIET, 700]]);
    expect(kinds(v)).not.toContain('advance');
    expect(last(play(6, [[SPEECH, 1500], [QUIET, 700], [SPEECH, 400], [QUIET, 1000]])).kind).toBe('advance');
  });

  it('waits longer for a patient profile', () => {
    const spans: [number, number][] = [[SPEECH, 2000], [QUIET, 1200]];
    expect(last(play(6, spans, 1)).kind).toBe('advance');
    expect(last(play(6, spans, 2)).kind).toBe('pausing');
    expect(last(play(6, [[SPEECH, 2000], [QUIET, 1900]], 2)).kind).toBe('advance');
  });

  it('treats a wavering probability as part of the speech it follows', () => {
    // dips to 0.4 mid-word (between the two thresholds) should not break the run or start a silence
    const v = play(6, [[SPEECH, 600], [0.4, 300], [SPEECH, 600]]);
    expect(kinds(v)).toEqual(['waiting', 'speaking']);
  });

  it('sizes the wait to the line', () => {
    expect(enoughMs(1)).toBe(200);
    expect(enoughMs(12)).toBe(2000);
    expect(countWords("  Maybe if we all stay very still, he'll think we're out. ")).toBe(11);
  });
});
