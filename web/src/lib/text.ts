export interface Segment {
  t: string;
  mark: boolean;
}

/** Split a line into plain and marked runs, marking each word the page reader was unsure of. */
export function segments(text: string, unsure: string[]): Segment[] {
  const words = unsure.filter(Boolean).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  if (!words.length) return [{ t: text, mark: false }];
  // split() with a capture group alternates: plain, match, plain, match...
  return text
    .split(new RegExp(`(${words.join('|')})`, 'gi'))
    .map((t, i) => ({ t, mark: i % 2 === 1 }))
    .filter((s) => s.t !== '');
}

/** A stable hue for the nth character in a script. The CSS picks a lightness to suit light or dark mode. */
export function castHue(index: number): number {
  return (index * 47 + 350) % 360;
}
