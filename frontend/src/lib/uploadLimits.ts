/** The weight a file may have to be sent (AXE-630), the same as the server's (`MAX_UPLOAD_BYTES` and `MAX_TIFF_UPLOAD_BYTES`, routes/common.py).
 * A TIFF is the one format a browser cannot lighten before sending (lib/shrink.ts), and a studio scan weighs 72 MB at 24 Mpx. Imports nothing, so that
 * `npm run check:drop` can run it. */
export const MAX_BYTES = 30_000_000;
export const MAX_TIFF_BYTES = 100_000_000;

const isTiff = (name: string) => /\.tiff?$/i.test(name);

/** `45,3 Mo`, as the server writes it (decimal megabytes, a comma). */
export const megabytes = (size: number) => `${(size / 1e6).toFixed(1).replace(".", ",")} Mo`;

/** Why a file is too heavy to be sent, in the server's own words (nothing leaves the browser), or null when it may go. */
export function heavyReason(file: { name: string; size: number }): string | null {
  const tiff = isTiff(file.name);
  const limit = tiff ? MAX_TIFF_BYTES : MAX_BYTES;
  if (file.size <= limit) return null;
  return `Fichier trop lourd : ${megabytes(file.size)}, ${megabytes(limit)} au plus${tiff ? " pour un TIFF" : ""}.${tiff ? " Exportez-le en JPEG ou réduisez-le." : ""}`;
}
