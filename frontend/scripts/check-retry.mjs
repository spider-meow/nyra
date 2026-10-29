// Checks that the HTTP client (src/lib/api.ts) waits and replays a request the API refused with 429 (rate limit),
// at most 3 times, only when `Retry-After` is present and valid (a 429 without it is final, like the daily invitation limit), capped at 30 s,
// never for a file upload (FormData), and then raises the usual ApiError.
// No test framework: the file is compiled in memory and `fetch` is replaced by a fake. Run: `npm run check:retry`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { transformWithOxc } from "vite";

const source = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const { code: outputText } = await transformWithOxc(source, "source.ts", { target: "es2022" });
const { api, ApiError, isUnanswered, UNAVAILABLE, UNREACHABLE } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

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

// --- the delay: Retry-After in seconds, capped at 30 ------------------------------------------------------------------
for (const [header, expected] of [["5", 5000], ["120", 30000], ["0.5", 500]]) {
  waits.length = 0;
  fakeApi(() => limited(header), () => reply(200, {}));
  await api.get("/x");
  assert.deepEqual(waits, [expected], header);
}

// --- a 429 without a valid Retry-After is final (the daily invitation limit): no wait, no second call ----------------
for (const header of [null, "", "soon", "-3", "0"]) {
  waits.length = 0;
  fakeApi(() => limited(header), () => reply(200, {}));
  await assert.rejects(api.post("/x", {}), (error) => error instanceof ApiError && error.status === 429, String(header));
  assert.equal(calls.length, 1, String(header));
  assert.deepEqual(waits, []);
}

// --- a file upload is never replayed: the server has already read the files when it refuses --------------------------
fakeApi(() => limited(), () => reply(200, {}));
await assert.rejects(api.post("/x", new FormData()), (error) => error.status === 429);
assert.equal(calls.length, 1);

// --- a caller that opts out (`replay` false: the test mail of the back office, "come back in 50 minutes") gets the 429 at once ---
fakeApi(() => limited("3000"), () => reply(200, {}));
waits.length = 0;
await assert.rejects(api.post("/x", undefined, false), (error) => error.status === 429);
assert.equal(calls.length, 1);
assert.deepEqual(waits, []);

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

// --- what the person reads when the server did not answer (AXE: error states) --------------------------------------------
const rawReply = (status, text) => new Response(text, { status });
for (const [label, make] of [["empty 502 from the proxy", () => rawReply(502, "")], ["502 with a page", () => rawReply(502, "<html>Bad Gateway</html>")], ["504 plain text", () => rawReply(504, "upstream timeout")], ["503, no detail", () => reply(503, {})]]) {
  fakeApi(make);
  await assert.rejects(api.get("/x"), (error) => error.message === UNAVAILABLE && isUnanswered(error), label);
  assert.equal(calls.length, 1, `${label}: not replayed`);
}
fakeApi(() => rawReply(500, "Internal Server Error"));
await assert.rejects(api.get("/x"), (error) => /côté serveur/.test(error.message) && !/Internal/.test(error.message) && !isUnanswered(error), "a 500 page is not shown as is");
fakeApi(() => reply(502, { detail: "L'image n'est pas disponible pour le moment." }));
await assert.rejects(api.get("/x"), (error) => error.message === "L'image n'est pas disponible pour le moment.", "the API's own reason is kept, whatever the status");
fakeApi(() => reply(403, { detail: "Réservé aux administrateurs de l'organisation." }));
await assert.rejects(api.get("/x"), (error) => error.message === "Réservé aux administrateurs de l'organisation." && error.status === 403);
fakeApi(() => rawReply(404, "not found"));
await assert.rejects(api.get("/x"), (error) => error.message === "not found", "a 4xx text body is kept as before");
globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
await assert.rejects(api.get("/x"), (error) => error instanceof ApiError && error.status === 0 && error.message === UNREACHABLE && isUnanswered(error), "no connection");
assert.equal(isUnanswered(new ApiError("x", 429)), false);
assert.equal(isUnanswered(new Error("x")), false);

console.log("retry: 429 with Retry-After replayed at most 3 times, capped, never for uploads or a caller that opts out, other errors untouched, and a server that does not answer says so in words");
