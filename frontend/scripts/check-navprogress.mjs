// Checks the delay logic of the « page is loading » bar (src/lib/navProgress.ts, AXE-631) with a fake clock: nothing on a fast navigation,
// the bar after 150 ms on a slow one, at least 300 ms on screen once shown, and it stays when another navigation starts as it was going.
// No test framework: the file is compiled in memory. Run: `npm run check:navprogress`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { transformWithOxc } from "vite";

const source = readFileSync(new URL("../src/lib/navProgress.ts", import.meta.url), "utf8");
const { code } = await transformWithOxc(source, "source.ts", { target: "es2022" });
const { createNavProgress, SHOW_AFTER_MS, MIN_SHOWN_MS } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
assert.deepEqual([SHOW_AFTER_MS, MIN_SHOWN_MS], [150, 300]);

/** A clock that only moves when told to, running the timers that fall due. `at(ms, busy)` is the router changing state at `ms`. */
function run(events, until) {
  let time = 0;
  let next = 0;
  const timers = new Map();
  const log = [];
  const progress = createNavProgress((visible) => log.push([time, visible]), {
    now: () => time,
    set: (fn, ms) => (timers.set(++next, { due: time + ms, fn }), next),
    clear: (handle) => timers.delete(handle),
  });
  const advance = (to) => {
    for (;;) {
      const due = [...timers.entries()].filter(([, t]) => t.due <= to).sort((a, b) => a[1].due - b[1].due)[0];
      if (!due) break;
      timers.delete(due[0]);
      time = due[1].due;
      due[1].fn();
    }
    time = to;
  };
  for (const [at, busy] of events) {
    advance(at);
    progress.update(busy);
  }
  advance(until);
  progress.dispose();
  return log;
}

assert.deepEqual(run([[0, true], [100, false]], 2000), [], "a navigation over within 150 ms: no bar at all");
assert.deepEqual(run([[0, true], [149, false]], 2000), [], "just under the delay: none");
assert.deepEqual(run([[0, true], [1000, false]], 3000), [[150, true], [1000, false]], "a slow one: the bar after 150 ms, gone when the page is there");
assert.deepEqual(run([[0, true], [200, false]], 3000), [[150, true], [450, false]], "a bar shown at 150 stays until 450 (300 ms at least)");
assert.deepEqual(run([[0, true], [400, false]], 3000), [[150, true], [450, false]], "over at 400: the bar still waits for its 300 ms");
assert.deepEqual(run([[0, true], [200, false], [300, true], [900, false]], 3000), [[150, true], [900, false]], "another navigation while it was about to go: it stays, no flicker");
assert.deepEqual(run([[0, true], [1000, false], [2000, true], [2100, false]], 4000), [[150, true], [1000, false]], "the second, fast one shows nothing");
assert.deepEqual(run([[0, true], [0, true], [500, false], [500, false]], 3000), [[150, true], [500, false]], "repeated states change nothing");

console.log("navprogress: nothing under 150 ms, shown for 300 ms at least, stays when a navigation follows at once");
