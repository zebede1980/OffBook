import { describe, expect, it } from 'vitest';
import { encodeWav } from './wav';

describe('encodeWav', () => {
  it('writes a 16-bit mono PCM header and clamps samples', async () => {
    const blob = encodeWav([new Float32Array([0, 0.5, -0.5]), new Float32Array([2, -2])]);
    const v = new DataView(await blob.arrayBuffer());
    const tag = (at: number) => String.fromCharCode(...new Uint8Array(v.buffer, at, 4));
    expect([tag(0), tag(8), tag(12), tag(36)]).toEqual(['RIFF', 'WAVE', 'fmt ', 'data']);
    expect(v.getUint16(20, true)).toBe(1); // PCM
    expect(v.getUint16(22, true)).toBe(1); // mono
    expect(v.getUint32(24, true)).toBe(16000);
    expect(v.getUint16(34, true)).toBe(16);
    expect(v.getUint32(40, true)).toBe(10); // 5 samples x 2 bytes
    expect(blob.size).toBe(54);
    const s = (i: number) => v.getInt16(44 + i * 2, true);
    expect([s(0), s(1), s(2), s(3), s(4)]).toEqual([0, 16383, -16384, 32767, -32768]);
  });
});
