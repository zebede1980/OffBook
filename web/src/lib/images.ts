// Phone photos are 4-12 MB. The server (and the AI behind it) wants them small, and a 2000 px page is still
// easily readable, so shrink in the browser before uploading.
const MAX_EDGE = 2000;
const MAX_BYTES = 3_300_000;

export async function shrinkImage(file: File): Promise<Blob> {
  // 'from-image' applies the camera's rotation flag, so a portrait photo stays portrait
  const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext('2d')!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  for (const quality of [0.85, 0.7, 0.55]) {
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
    if (blob && blob.size <= MAX_BYTES) return blob;
  }
  throw new Error(`${file.name} is too big, even after shrinking.`);
}
