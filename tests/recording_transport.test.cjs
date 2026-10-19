const {test} = require("node:test");
const assert = require("node:assert/strict");
const {RecordingTransport} = require("../frontend/static/recording.js");

class Archive {
  rows = new Map();
  async open() {}
  async put(row) { this.rows.set(row.key, structuredClone(row)); }
  async get(key) { return this.rows.get(key); }
  async cleanup() { this.rows.clear(); }
}
const tick = () => new Promise(resolve => setImmediate(resolve));
async function until(condition) {
  for (let i = 0; i < 100 && !condition(); i++) await new Promise(resolve => setTimeout(resolve, 2));
  assert.ok(condition());
}
function reply(url, options) {
  const start = Number(new URL(url, "https://example.test").searchParams.get("start_sample"));
  const seq = Number(url.split("/chunks/")[1].split("?")[0]);
  return {ok: true, json: async () => ({type: "segment", state: "processed", sequence: seq,
    start_sample: start, end_sample: start + options.body.byteLength / 4, tekst: "", segmenter: []})};
}

test("failed upload retries same audio, stop drains tail, WAV has no overlap duplication", async () => {
  const archive = new Archive(), calls = [], results = [];
  let failed = false;
  const transport = new RecordingTransport({archive, retryMs: 1, request: async (url, options) => {
    calls.push(url);
    if (!failed) { failed = true; throw new Error("connection reset"); }
    return reply(url, options);
  }});
  transport.onmessage = e => results.push(JSON.parse(e.data));
  try {
    await transport.initialized;
    transport.send(new Float32Array(241000).buffer);
    transport.stop();
    await until(() => transport.readyState === 3);
    assert.equal(calls[0], calls[1]);
    assert.equal(transport.rows.length, 2);
    assert.equal(transport.rows[1].start, 224000);
    assert.equal(transport.rows[1].end, 241000);
    assert.equal(results.filter(r => r.type === "segment").length, 2);
    const wav = await transport.wav();
    assert.equal(wav.size, 44 + 241000 * 2);
    assert.equal(new DataView(await wav.arrayBuffer()).getUint32(40, true), 241000 * 2);
  } finally { await transport.dispose(); }
});

test("recording continues while server unavailable; discard cancels retry and erases archive", async () => {
  const archive = new Archive();
  const transport = new RecordingTransport({archive, request: async () => { throw new Error("offline"); }});
  try {
    await transport.initialized;
    transport.send(new Float32Array(240000).buffer);
    await until(() => transport.retryTimer);
    transport.send(new Float32Array(240000).buffer);
    await transport.writing;
    assert.equal(transport.stopped, false);
    assert.equal(archive.rows.size, 2);
  } finally { await transport.dispose(); }
  assert.equal(archive.rows.size, 0);
});

test("reload replays processed result and only uploads unfinished chunks", async () => {
  const archive = new Archive(), calls = [], results = [];
  const session = crypto.randomUUID();
  for (let sequence = 0; sequence < 2; sequence++) {
    await archive.put({key: `${session}:${sequence}`, session, sequence,
      start: sequence * 224000, end: sequence * 224000 + 240000,
      expires: Date.now() + 10000, pcm: new Float32Array(240000),
      result: sequence === 0 ? {type: "segment", sequence, tekst: "saved"} : undefined});
  }
  const transport = new RecordingTransport({archive, restored: [...archive.rows.values()],
    request: async (url, options) => { calls.push(url); return reply(url, options); }});
  transport.onmessage = e => results.push(JSON.parse(e.data));
  try {
    await transport.initialized;
    await until(() => transport.readyState === 3);
    assert.equal(calls.length, 1);
    assert.match(calls[0], /chunks\/1\?/);
    assert.equal(results.filter(r => r.type === "segment").length, 2);
  } finally { await transport.dispose(); }
});

test("late response after discard cannot append transcript", async () => {
  let resolve;
  const transport = new RecordingTransport({archive: new Archive(), request: (url, options) =>
    new Promise(done => { resolve = () => done(reply(url, options)); })});
  const results = [];
  transport.onmessage = e => results.push(JSON.parse(e.data));
  await transport.initialized;
  transport.send(new Float32Array(240000).buffer);
  await until(() => resolve);
  await transport.dispose();
  resolve();
  await tick();
  assert.equal(results.filter(r => r.type === "segment").length, 0);
});
