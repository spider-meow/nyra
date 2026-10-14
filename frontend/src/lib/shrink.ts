/** A file lighter than this is sent as it is (the server keeps such a file untouched too). */
export const SHRINK_ABOVE_BYTES = 1_500_000;
/** What a request can carry on a serverless host (~4.5 MB), minus the form around the file: the copy is pushed under it. */
const SEND_BYTES = 3_000_000;
// Long side and JPEG quality, tried in turn until the copy is light enough. The first is what the server keeps of a reference.
const STEPS = [{ side: 2048, quality: 0.88 }, { side: 2048, quality: 0.8 }, { side: 1600, quality: 0.75 }, { side: 1200, quality: 0.7 }];
// What a browser decodes with a canvas; TIFF and animated GIF go as they are.
const SHRINKABLE = new Set(["image/jpeg", "image/png", "image/webp", "image/avif", "image/bmp"]);

/** The size an image of this size gets: unchanged when it already fits, else its long side becomes `side`. */
export function fitWithin(width: number, height: number, side: number): { width: number; height: number } {
  const scale = Math.min(1, side / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

function hasTransparency(context: CanvasRenderingContext2D, width: number, height: number): boolean {
  const pixels = context.getImageData(0, 0, width, height).data;
  for (let index = 3; index < pixels.length; index += 4) if (pixels[index] < 255) return true;
  return false;
}

function toBlob(canvas: HTMLCanvasElement, type: string, quality?: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, type, quality));
}

/** The picture drawn at a given size, and encoded: JPEG, or PNG when `png` (transparency to keep). */
async function encode(bitmap: ImageBitmap, side: number, quality: number, png: boolean | null): Promise<{ blob: Blob | null; png: boolean }> {
  const { width, height } = fitWithin(bitmap.width, bitmap.height, side);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) return { blob: null, png: false };
  context.drawImage(bitmap, 0, 0, width, height);
  const keepAlpha = png ?? hasTransparency(context, width, height);
  return { blob: await toBlob(canvas, keepAlpha ? "image/png" : "image/jpeg", keepAlpha ? undefined : quality), png: keepAlpha };
}

/**
 * A lighter copy of a heavy image, under the same name: JPEG (PNG when there is transparency to keep), first
 * at most 2048 px, then smaller and more compressed until it is light enough to send. The file itself comes back
 * when it is light already, when the browser cannot read it, or when no copy is lighter: the server then does
 * what it always did with it.
 */
export async function shrinkImage(file: File): Promise<File> {
  if (file.size <= SHRINK_ABOVE_BYTES || !SHRINKABLE.has(file.type)) return file;
  let bitmap: ImageBitmap | undefined;
  try {
    bitmap = await createImageBitmap(file); // applies the photo's orientation
    // Not a JPEG: whether it has transparency is read once, on the first copy, then kept for the next ones.
    let png: boolean | null = file.type === "image/jpeg" ? false : null;
    let best: Blob | null = null;
    for (const step of STEPS) {
      const made = await encode(bitmap, step.side, step.quality, png);
      png = made.png;
      if (made.blob && (!best || made.blob.size < best.size)) best = made.blob;
      if (best && best.size <= SEND_BYTES) break;
    }
    return best && best.size < file.size ? new File([best], file.name, { type: best.type, lastModified: file.lastModified }) : file;
  } catch {
    return file; // an image this browser cannot decode: the server decides, and says why if it refuses it
  } finally {
    bitmap?.close();
  }
}

/** `shrinkImage` over a list, one file at a time (a decoded photo weighs ~50 MB in memory); `onDone` after each. */
export async function shrinkAll(files: File[], onDone: (done: number) => void): Promise<File[]> {
  const out: File[] = [];
  for (const file of files) {
    out.push(await shrinkImage(file));
    onDone(out.length);
  }
  return out;
}
