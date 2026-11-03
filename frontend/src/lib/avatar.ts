// The avatar of a person (AXE-675): a motif drawn from their id. Nothing is stored and nothing is fetched: the same id gives the same
// SVG on every screen. 15 families, each with settings (colours, counts, angle, offset) drawn from the id, so two people of one family differ.
// Pure functions: `npm run check:avatar` checks them.

type Rng = () => number;
type Family = (r: Rng) => string;

/** A seeded generator (xmur3 to seed, mulberry32 to draw): numbers in [0, 1) that depend on the text only. */
export function rng(seed: string): Rng {
  let h = 1779033703 ^ seed.length;
  for (let i = 0; i < seed.length; i++) {
    h = Math.imul(h ^ seed.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  h = Math.imul(h ^ (h >>> 16), 2246822507);
  h = Math.imul(h ^ (h >>> 13), 3266489909);
  let a = (h ^ (h >>> 16)) >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const INK = "#1a3300";
const TERRA = "#cb5521";
const WHITE = "#ffffff";
const PAST = ["#dce9cf", "#f6e27f", "#f4d9c6", "#d5e5e3"];
const ST = `stroke="${INK}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"`;

const pick = <T>(r: Rng, items: readonly T[]): T => items[Math.floor(r() * items.length)];
const int = (r: Rng, low: number, high: number) => low + Math.floor(r() * (high - low + 1));
const num = (n: number) => +n.toFixed(1);
const others = (bg: string) => [...PAST.filter((c) => c !== bg), TERRA, WHITE];
const strong = (r: Rng) => pick(r, [INK, TERRA]);
const base = (bg: string) => `<rect width="64" height="64" fill="${bg}"/>`;

function blocs(r: Rng) {
  const bg = pick(r, PAST);
  const fg = pick(r, [INK, INK, TERRA]);
  const n = pick(r, [3, 4, 5]);
  const cell = 48 / n;
  const half = Math.ceil(n / 2);
  const on = Array.from({ length: n }, () => Array.from({ length: half }, () => r() < 0.58));
  let out = "";
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      if (on[y][x < half ? x : n - 1 - x]) out += `<rect x="${num(8 + x * cell)}" y="${num(8 + y * cell)}" width="${num(cell + 0.4)}" height="${num(cell + 0.4)}" fill="${fg}"/>`;
    }
  }
  return base(bg) + out;
}

function rosace(r: Rng) {
  const bg = pick(r, PAST);
  const n = pick(r, [4, 5, 6, 7, 8]);
  const o = others(bg);
  const c1 = pick(r, o);
  const c2 = pick(r, o);
  const len = pick(r, [20, 24]);
  let petals = "";
  for (let i = 0; i < n; i++) {
    petals += `<ellipse cx="32" cy="${num(32 - len / 1.6)}" rx="${n > 6 ? 6 : 8}" ry="${num(len / 1.6)}" transform="rotate(${num((i * 360) / n)} 32 32)" fill="${i % 2 ? c2 : c1}" ${ST}/>`;
  }
  return `${base(bg)}${petals}<circle cx="32" cy="32" r="5" fill="${INK}"/>`;
}

function pois(r: Rng) {
  const bg = pick(r, PAST);
  const fg = pick(r, [INK, INK, TERRA]);
  const n = pick(r, [4, 5, 6]);
  const gap = 64 / (n + 1);
  const half = Math.ceil(n / 2);
  const size = Array.from({ length: n }, () => Array.from({ length: half }, () => r()));
  let dots = "";
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) dots += `<circle cx="${num(gap * (x + 1))}" cy="${num(gap * (y + 1))}" r="${num(0.8 + size[y][x < half ? x : n - 1 - x] * gap * 0.45)}" fill="${fg}"/>`;
  }
  return base(bg) + dots;
}

/** Four tiles, each a quarter circle, a circle, a diagonal or an arch, turned by a quarter-turn step. */
function tuiles(r: Rng) {
  const bg = pick(r, PAST);
  const o = others(bg);
  let tiles = "";
  for (let i = 0; i < 4; i++) {
    const x = (i % 2) * 32;
    const y = Math.floor(i / 2) * 32;
    const fill = `fill="${pick(r, o)}" ${ST}`;
    const kind = int(r, 0, 4);
    const turn = int(r, 0, 3) * 90;
    const shape = [
      `<path d="M${x} ${y}h32a32 32 0 0 1 -32 32z" ${fill}/>`,
      `<path d="M${x + 32} ${y}v32h-32a32 32 0 0 1 32 -32z" ${fill}/>`,
      `<circle cx="${x + 16}" cy="${y + 16}" r="11" ${fill}/>`,
      `<path d="M${x} ${y + 32}L${x + 32} ${y}V${y + 32}z" ${fill}/>`,
      `<path d="M${x} ${y + 16}a16 16 0 0 1 32 0z" ${fill}/>`,
    ][kind];
    tiles += `<g transform="rotate(${turn} ${x + 16} ${y + 16})">${shape}</g>`;
  }
  return base(bg) + tiles;
}

function chevrons(r: Rng) {
  const bg = pick(r, PAST);
  const o = [...others(bg), INK];
  const n = int(r, 4, 6);
  const h = 70 / n;
  const depth = pick(r, [7, 10, 14]);
  const segments = pick(r, [2, 3, 4]);
  const width = pick(r, [h * 0.5, h * 0.65]);
  const c1 = pick(r, o);
  const c2 = pick(r, o);
  let bands = "";
  for (let i = -1; i < n; i++) {
    let points = "";
    for (let k = 0; k <= segments; k++) points += `${num((k * 64) / segments)},${num(i * h + h / 2 + (k % 2 ? depth : 0))} `;
    bands += `<polyline points="${points}" fill="none" stroke="${i % 2 ? c2 : c1}" stroke-width="${num(width)}" stroke-linejoin="miter"/>`;
  }
  return base(bg) + bands;
}

function rayons(r: Rng) {
  const bg = pick(r, PAST);
  const color = pick(r, [INK, TERRA, INK, TERRA, ...others(bg)]);
  const n = pick(r, [8, 10, 12, 16]);
  const [cx, cy] = pick(r, [[32, 32], [32, 52], [12, 12], [52, 32], [32, 6]]);
  let wedges = "";
  for (let i = 0; i < n; i += 2) {
    const a1 = (i * 2 * Math.PI) / n;
    const a2 = ((i + 1) * 2 * Math.PI) / n;
    wedges += `<path d="M${cx} ${cy}L${num(cx + 120 * Math.cos(a1))} ${num(cy + 120 * Math.sin(a1))}L${num(cx + 120 * Math.cos(a2))} ${num(cy + 120 * Math.sin(a2))}z" fill="${color}"/>`;
  }
  const hub = r() < 0.5 ? `<circle cx="${cx}" cy="${cy}" r="${pick(r, [6, 9, 12])}" fill="${INK}"/>` : "";
  return base(bg) + wedges + hub;
}

function cibles(r: Rng) {
  const bg = pick(r, PAST);
  const o = [...others(bg), INK];
  const n = int(r, 3, 5);
  const cx = int(r, 22, 42);
  const cy = int(r, 22, 42);
  const radius = pick(r, [36, 44]);
  const colors = Array.from({ length: n }, () => pick(r, o));
  let rings = "";
  for (let i = 0; i < n; i++) rings += `<circle cx="${cx}" cy="${cy}" r="${num(radius * (1 - i / n))}" fill="${colors[i]}" ${ST}/>`;
  return base(bg) + rings;
}

function damier(r: Rng) {
  const bg = pick(r, PAST);
  const o = [...others(bg), INK];
  const c = pick(r, o);
  const c2 = r() < 0.5 ? pick(r, o.filter((k) => k !== c)) : null;
  const n = pick(r, [3, 4, 5, 6]);
  const s = 96 / n;
  const round = r() < 0.35;
  const rot = pick(r, [45, 0, 45, 20]);
  const ox = int(r, 0, Math.floor(s));
  const oy = int(r, 0, Math.floor(s));
  let cells = "";
  for (let y = 0; y < n + 1; y++) {
    for (let x = 0; x < n + 1; x++) {
      const fill = (x + y) % 2 ? c2 : c;
      if (!fill) continue;
      const px = -16 + x * s - ox;
      const py = -16 + y * s - oy;
      cells += round ? `<circle cx="${num(px + s / 2)}" cy="${num(py + s / 2)}" r="${num(s / 2.2)}" fill="${fill}"/>` : `<rect x="${num(px)}" y="${num(py)}" width="${num(s)}" height="${num(s)}" fill="${fill}"/>`;
    }
  }
  return `${base(bg)}<g transform="rotate(${rot} 32 32)">${cells}</g>`;
}

function rayures(r: Rng) {
  const bg = pick(r, PAST);
  const o = [...others(bg), INK];
  const angle = pick(r, [0, 90, 45, -45, 30, -30, 60]);
  let x = -30;
  let stripes = "";
  while (x < 100) { // ends: each stripe is at least 4 wide
    const w = int(r, 4, 13);
    const c = r() < 0.2 ? bg : r() < 0.4 ? strong(r) : pick(r, o);
    stripes += `<rect x="${x}" y="-40" width="${w}" height="150" fill="${c}"/>`;
    x += w;
  }
  return `${base(bg)}<g transform="rotate(${angle} 32 32)">${stripes}</g>`;
}

function ecailles(r: Rng) {
  const bg = pick(r, PAST);
  const o = others(bg);
  const radius = pick(r, [8, 10, 13]);
  const colors = [pick(r, o), pick(r, o), pick(r, o)];
  let scales = "";
  for (let j = 0; j < 8; j++) {
    for (let i = -1; i < 7; i++) scales += `<circle cx="${num(i * radius * 2 + (j % 2 ? radius : 0))}" cy="${num(j * radius * 0.62 - 2)}" r="${radius}" fill="${colors[j % 3]}" ${ST}/>`;
  }
  return base(bg) + scales;
}

function triangles(r: Rng) {
  const bg = pick(r, PAST);
  const o = [...others(bg), INK];
  const palette = [bg, strong(r), pick(r, o), pick(r, o)];
  const n = pick(r, [3, 4]);
  const s = 64 / n;
  let out = "";
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const X = x * s;
      const Y = y * s;
      const down = r() < 0.5;
      const a = pick(r, palette);
      const b = pick(r, palette);
      out += down
        ? `<path d="M${num(X)} ${num(Y)}h${num(s)}v${num(s)}z" fill="${a}"/><path d="M${num(X)} ${num(Y)}v${num(s)}h${num(s)}z" fill="${b}"/>`
        : `<path d="M${num(X)} ${num(Y + s)}V${num(Y)}h${num(s)}z" fill="${a}"/><path d="M${num(X + s)} ${num(Y)}v${num(s)}h-${num(s)}z" fill="${b}"/>`;
    }
  }
  return base(bg) + out;
}

function hexagones(r: Rng) {
  const bg = pick(r, PAST);
  const o = [...others(bg), INK];
  const R = pick(r, [10, 12, 15]);
  const w = Math.sqrt(3) * R;
  const palette = [pick(r, o), pick(r, o), bg, "none"];
  let cells = "";
  for (let j = -1; j < 64 / (R * 1.5) + 1; j++) {
    for (let i = -1; i < 64 / w + 1; i++) {
      const cx = i * w + (j % 2 ? w / 2 : 0);
      const cy = j * R * 1.5;
      let points = "";
      for (let k = 0; k < 6; k++) {
        const angle = (Math.PI / 180) * (60 * k - 30);
        points += `${num(cx + R * Math.cos(angle))},${num(cy + R * Math.sin(angle))} `;
      }
      cells += `<polygon points="${points}" fill="${pick(r, palette)}" ${ST}/>`;
    }
  }
  return base(bg) + cells;
}

/** One symbol (disc, plus, cross, square, triangle, ring) centred on (cx, cy). */
function symbol(kind: number, cx: number, cy: number, u: number, c: string) {
  const line = `stroke="${c}" stroke-width="3" stroke-linecap="round" fill="none"`;
  return [
    `<circle cx="${num(cx)}" cy="${num(cy)}" r="${num(u)}" fill="${c}"/>`,
    `<path d="M${num(cx - u)} ${num(cy)}h${num(2 * u)}M${num(cx)} ${num(cy - u)}v${num(2 * u)}" ${line}/>`,
    `<path d="M${num(cx - u)} ${num(cy - u)}l${num(2 * u)} ${num(2 * u)}M${num(cx + u)} ${num(cy - u)}l-${num(2 * u)} ${num(2 * u)}" ${line}/>`,
    `<rect x="${num(cx - u)}" y="${num(cy - u)}" width="${num(2 * u)}" height="${num(2 * u)}" rx="1.5" fill="${c}"/>`,
    `<path d="M${num(cx)} ${num(cy - u)}L${num(cx + u)} ${num(cy + u)}H${num(cx - u)}z" fill="${c}"/>`,
    `<circle cx="${num(cx)}" cy="${num(cy)}" r="${num(u)}" ${line}/>`,
  ][kind];
}

function symboles(r: Rng) {
  const bg = pick(r, PAST);
  const n = pick(r, [3, 3, 4]);
  const s = 64 / n;
  let grid = "";
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const kind = int(r, 0, 5);
      grid += symbol(kind, s * (x + 0.5), s * (y + 0.5), s * 0.28, pick(r, [INK, INK, TERRA]));
    }
  }
  return base(bg) + grid;
}

function arches(r: Rng) {
  const bg = pick(r, PAST);
  const o = [...others(bg), INK];
  const n = int(r, 3, 5);
  const rot = pick(r, [0, 90, 180, 270]);
  const colors = Array.from({ length: n }, () => pick(r, o));
  let rings = "";
  for (let i = 0; i < n; i++) {
    const R = 44 * (1 - i / n);
    rings += `<path d="M${num(32 - R)} 50a${num(R)} ${num(R)} 0 0 1 ${num(2 * R)} 0z" fill="${colors[i]}" ${ST}/>`;
  }
  return `${base(bg)}<g transform="rotate(${rot} 32 32)">${rings}</g>`;
}

function spirale(r: Rng) {
  const bg = pick(r, PAST);
  const color = pick(r, [INK, INK, TERRA]);
  const turns = pick(r, [2.5, 3, 3.5, 4]);
  const width = pick(r, [3.5, 4.5, 5.5]);
  const dir = pick(r, [1, -1]);
  const cx = 32 + int(r, -6, 6);
  const cy = 32 + int(r, -6, 6);
  let d = "";
  for (let t = 0; t <= turns * 2 * Math.PI; t += 0.2) {
    const radius = 1.5 + t * (30 / (turns * 2 * Math.PI));
    d += `${t === 0 ? "M" : "L"}${num(cx + dir * radius * Math.cos(t))} ${num(cy + radius * Math.sin(t))} `;
  }
  return `${base(bg)}<path d="${d}" fill="none" stroke="${color}" stroke-width="${width}" stroke-linecap="round"/>`;
}

/** The families, in the order that decides which one a person gets. The names are part of the seed: do not rename them, or everyone's avatar changes. */
export const FAMILIES: [string, Family][] = [
  ["blocs", blocs], ["rosace", rosace], ["pois", pois], ["tuiles", tuiles], ["chevrons", chevrons],
  ["rayons", rayons], ["cibles", cibles], ["damier", damier], ["rayures", rayures], ["ecailles", ecailles],
  ["triangles", triangles], ["hexagones", hexagones], ["symboles", symboles], ["arches", arches], ["spirale", spirale],
];

/** The drawing (the inside of the SVG) of a person: the family comes from the id, then its settings from the id and the family. */
export function motif(seed: string): string {
  const [name, draw] = FAMILIES[Math.floor(rng(seed)() * FAMILIES.length)];
  return draw(rng(`${seed}|${name}`));
}

/** The avatar as an image address (a data URI: nothing to fetch, no markup injected into the page). The CSP of the app allows `data:` images. */
export function avatarSrc(seed: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><clipPath id="a"><circle cx="32" cy="32" r="32"/></clipPath></defs><g clip-path="url(#a)">${motif(seed)}</g></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}
