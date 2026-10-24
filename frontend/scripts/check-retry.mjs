// Checks that the HTTP client (src/lib/api.ts) waits and replays a request the API refused with 429 (rate limit),
// at most 3 times, honours `Retry-After` (capped, 2 s when absent or invalid) and cancellation, and then raises the usual ApiError.
// No test framework: the file is compiled in memory and `fetch` is replaced by a fake. Run: `npm run check:retry`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } });
const { api, ApiError } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

const reply = (status, body, headers = {}) => new Response(JSON.stringify(body), { status, headers });
const limited = (retryAfter = "0.01") => reply(429, { detail: "Trop de requêtes." }, retryAfter === null ? {} : { "Retry-After": retryAfter });
let calls = [];
const fakeApi = (...responses) => {
  calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, method: init.method ?? "GET", signal: init.signal });
    return responses[Math.min(calls.length, responses.length) - 1]();
  };
};

// Every wait is recorded and skipped, so the delays can be read without waiting for them.
const waits = [];
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (callback, ms) => (waits.push(ms), realSetTimeout(callback, 0));

// --- the delay: Retry-After in seconds, capped at 30, 2 s when absent or not a positive number ---------------------
for (const [header, expected] of [["5", 5000], ["120", 30000], [null, 2000], ["", 2000], ["soon", 2000], ["-3", 2000], ["0", 2000]]) {
  waits.length = 0;
  fakeApi(() => limited(header), () => reply(200, {}));
  await api.get("/x");
  assert.deepEqual(waits, [expected], String(header));
}

// --- 429 then success: the caller never sees the 429, the request (a POST here) is sent again -----------------------
fakeApi(() => limited(), () => limited(), () => reply(200, { ok: true }));
assert.deepEqual(await api.post("/x", { a: 1 }), { ok: true });
assert.deepEqual(calls.map((c) => c.method), ["POST", "POST", "POST"]);

// --- still 429 after 3 retries: 4 calls in all, then the usual ApiError with the server's sentence -------------------
fakeApi(() => limited());
await assert.rejects(api.get("/x"), (error) => error instanceof ApiError && error.status === 429 && error.message === "Trop de requêtes.");
assert.equal(calls.length, 4);

// --- other errors are not replayed ---------------------------------------------------------------------------------
fakeApi(() => reply(500, { detail: "boom" }));
await assert.rejects(api.get("/x"), (error) => error.status === 500);
assert.equal(calls.length, 1);

// --- cancellation: a request cancelled during the wait raises the caller's reason and sends nothing more ---------------
fakeApi(() => limited("1"));
const controller = new AbortController();
const cancelled = api.get("/x", { signal: controller.signal });
controller.abort(new Error("cancelled"));
await assert.rejects(cancelled, /cancelled/);
assert.equal(calls.length, 1);

console.log("retry: 429 replayed at most 3 times after Retry-After, cancellable, other errors untouched");
