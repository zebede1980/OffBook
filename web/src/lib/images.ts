// Phone photos are 4-12 MB. The server (and the AI behind it) wants them small, and a 2000 px page is still
// easily readable, so shrink in the browser before uploading.
const MAX_EDGE = 2000;
const MAX_BYTES = 3_300_000;

export async function shrinkImage(file: File): Promise<Blob> {
  let bitmap: ImageBitmap;
  try {
    // 'from-image' applies the camera's rotation flag, so a portrait photo stays portrait
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  } catch {
    // Desktop Chrome and Firefox can't decode iPhone HEIC files; Safari on the phone converts them itself
    throw new Error(
      /\.hei[cf]$/i.test(file.name)
        ? `${file.name} is an iPhone HEIC photo, which this browser can't open. Add it from the phone instead.`
        : `Couldn't open ${file.name}. Is it a photo?`
    );
  }
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
