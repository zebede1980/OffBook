// Copies the voice-detector's runtime files out of node_modules into static/vad/, so they are served from our
// own site rather than a CDN (nothing about the kids' rehearsals should depend on, or talk to, a third party).
// Runs before `dev` and `build`. static/vad/ is gitignored: these are build output, not source.
import { copyFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const out = join(root, 'static', 'vad');
const files = [
  ['@ricky0123/vad-web/dist/silero_vad_v5.onnx'],
  ['@ricky0123/vad-web/dist/vad.worklet.bundle.min.js'],
  ['onnxruntime-web/dist/ort-wasm-simd-threaded.wasm'],
  ['onnxruntime-web/dist/ort-wasm-simd-threaded.mjs']
];

mkdirSync(out, { recursive: true });
for (const [from] of files) {
  copyFileSync(join(root, 'node_modules', from), join(out, from.split('/').pop()));
}
console.log(`copied ${files.length} voice-detector files to static/vad/`);
