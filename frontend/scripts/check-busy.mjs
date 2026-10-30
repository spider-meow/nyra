// Checks WHEN a refresh of data already on screen is shown (src/lib/busy.ts, AXE-632): never for an answer that comes within 200 ms (no flash), from 200 ms
// when it lasts, and for at least 300 ms once shown (no blink). The timeline is run on a fake clock, the way the hook drives `busyAt`.
// No test framework: the file is compiled in memory. Run: `npm run check:busy`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { transformWithOxc } from "vite";

const source = readFileSync(new URL("../src/lib/busy.ts", import.meta.url), "utf8").replace(/^import .*$/m, "");
const { code } = await transformWithOxc(source, "source.ts", { target: "es2022" });
// The hooks are not run here: only the constants and the pure function (the import line of React is removed above).
const { busyAt, SHOW_AFTER_MS, SHOW_AT_LEAST_MS } = await import(`data:text/javascript;base64,${Buffer.from(code.replace(/export function use\w+[\s\S]*$/, "")).toString("base64")}`);

assert.equal(SHOW_AFTER_MS, 200);
assert.equal(SHOW_AT_LEAST_MS, 300);

// --- the way the hook drives it: `look` at each moment it is asked to, `recheckIn` says when the next one is -----------------------------------
function run(loadFrom, loadUntil, until = 2000) {
  const state = { activeSince: null, shownSince: null };
  const shown = []; // [time, show]
  let now = 0;
  let next = 0; // the next time the hook looks: when the load starts, when it ends, and after `recheckIn`
  const events = new Set([loadFrom, loadUntil, 0]);
  const times = () => [...events].sort((a, b) => a - b);
  for (let i = 0; i < 50 && times().some((t) => t >= now); i += 1) {
    now = times().find((t) => t >= now);
    if (now > until) break;
    if (now >= loadFrom && now < loadUntil && state.activeSince === null) state.activeSince = loadFrom;
    if (now >= loadUntil) state.activeSince = null;
    const result = busyAt(state, now);
    state.shownSince = result.show ? (state.shownSince ?? now) : null;
    shown.push([now, result.show]);
    events.delete(now);
    if (result.recheckIn !== null) events.add(now + result.recheckIn);
    next = now + 1;
  }
  void next;
  return shown;
}
const showAt = (shown, t) => shown.filter(([time]) => time <= t).at(-1)?.[1] ?? false;

// An answer within 200 ms: nothing is ever shown.
let shown = run(0, 150);
assert.ok(shown.every(([, show]) => !show), "a quick answer shows nothing");
shown = run(0, 199);
assert.ok(shown.every(([, show]) => !show), "199 ms: still nothing");

// A load of 1000 ms: shown from 200 ms, until it ends (and the 300 ms minimum is long past).
shown = run(0, 1000);
assert.equal(showAt(shown, 199), false);
assert.equal(showAt(shown, 200), true);
assert.equal(showAt(shown, 999), true);
assert.equal(showAt(shown, 1000), false, "ended after more than 300 ms of showing: gone at once");

// A load of 250 ms: shown from 200, and kept until 500 (300 ms at least), not hidden at 250.
shown = run(0, 250);
assert.equal(showAt(shown, 200), true);
assert.equal(showAt(shown, 250), true, "the answer came 50 ms after it was shown: it stays");
assert.equal(showAt(shown, 499), true);
assert.equal(showAt(shown, 500), false, "300 ms after it was shown");

// The pure function: nothing active and nothing shown is nothing to look at again.
assert.deepEqual(busyAt({ activeSince: null, shownSince: null }, 10_000), { show: false, recheckIn: null });
assert.deepEqual(busyAt({ activeSince: 0, shownSince: null }, 50), { show: false, recheckIn: 150 }, "asks to be looked at again when 200 ms have passed");
assert.deepEqual(busyAt({ activeSince: 0, shownSince: 200 }, 300), { show: true, recheckIn: null }, "loading and shown: until the load ends");
assert.deepEqual(busyAt({ activeSince: null, shownSince: 200 }, 250), { show: true, recheckIn: 250 }, "ended 50 ms after it was shown: 250 ms to go");
assert.deepEqual(busyAt({ activeSince: null, shownSince: 200 }, 500), { show: false, recheckIn: null });
console.log("check:busy ok");
