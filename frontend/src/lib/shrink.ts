/** A file lighter than this is sent as it is (the server keeps such a file untouched too). */
export const SHRINK_ABOVE_BYTES = 1_500_000;
/** Long side of what is sent: the server keeps at most this much of a reference. */
export const SHRINK_SIDE = 2048;
const JPEG_QUALITY = 0.88;
// What the browser can decode with a canvas; anything else (TIFF, BMP, animated GIF) goes as it is.
const SHRINKABLE = new Set(["image/jpeg", "image/png", "image/webp"]);

/** The size an image of this size gets: unchanged when it already fits, else its long side becomes SHRINK_SIDE. */
export function fitWithin(width: number, height: number): { width: number; height: number } {
  const scale = Math.min(1, SHRINK_SIDE / Math.max(width, height));
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

/**
 * A lighter copy of a heavy image, under the same name: at most SHRINK_SIDE px, JPEG, or PNG when there is
 * transparency to keep. The file itself comes back when it is light already, when the browser cannot read it,
 * or when the copy would not be lighter: the server then does what it always did with it.
 */
export async function shrinkImage(file: File): Promise<File> {
  if (file.size <= SHRINK_ABOVE_BYTES || !SHRINKABLE.has(file.type)) return file;
  let bitmap: ImageBitmap | undefined;
  try {
    bitmap = await createImageBitmap(file); // applies the photo's orientation
    const { width, height } = fitWithin(bitmap.width, bitmap.height);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) return file;
    context.drawImage(bitmap, 0, 0, width, height);
    const keepAlpha = file.type !== "image/jpeg" && hasTransparency(context, width, height);
    const type = keepAlpha ? "image/png" : "image/jpeg";
    const blob = await toBlob(canvas, type, keepAlpha ? undefined : JPEG_QUALITY);
    return blob && blob.size < file.size ? new File([blob], file.name, { type, lastModified: file.lastModified }) : file;
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
