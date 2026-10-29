const {test} = require("node:test");
const assert = require("node:assert/strict");
const {RecordingLifecycle} = require("../frontend/static/recording-lifecycle.js");

class Target extends EventTarget {}
const flush = () => new Promise(resolve => setImmediate(resolve));
function setup({wakeLock} = {}) {
  let now = 0;
  const doc = Object.assign(new Target(), {visibilityState: "visible"});
  const context = Object.assign(new Target(), {state: "running", currentTime: 0,
    async resume() { this.state = "running"; }});
  const track = Object.assign(new Target(), {muted: false, readyState: "live"});
  const states = [], gaps = [], reports = [];
  const lifecycle = new RecordingLifecycle({doc, nav: {wakeLock}, now: () => now,
    onstate: state => states.push(state), ongaps: value => gaps.push(value),
    report: event => reports.push(event)});
  lifecycle.start(context, [track]);
  return {lifecycle, doc, context, track, states, gaps, reports,
    advance(wall, audio = wall) { now += wall; context.currentTime += audio / 1000; }};
}

test("suspension is recovered and remains visible as a possible gap", async () => {
  const w = setup();
  w.lifecycle.samples(16000);
  w.advance(1000);
  w.lifecycle.check();
  w.context.state = "interrupted";
  w.context.dispatchEvent(new Event("statechange"));
  w.advance(60000, 0);
  await w.lifecycle.resume();
  w.lifecycle.samples(128);
  assert.equal(w.lifecycle.gaps.length, 1);
  assert.equal(w.lifecycle.gaps[0].sample_offset, 16000);
  assert.equal(w.lifecycle.gaps[0].duration_s, 60);
  assert.equal(w.states.at(-1).paused, false);
  assert.ok(w.reports.some(report => report.kind === "capture_recovered"));
  w.lifecycle.stop();
});

test("hidden page with continuous audio does not manufacture a gap", () => {
  const w = setup();
  w.doc.visibilityState = "hidden";
  w.doc.dispatchEvent(new Event("visibilitychange"));
  w.advance(60000);
  w.lifecycle.check();
  assert.equal(w.lifecycle.gaps.length, 0);
  w.lifecycle.stop();
});

test("missed lifecycle events still detect stalled audio clock after sleep", () => {
  const w = setup();
  w.advance(60000, 0);
  w.lifecycle.check();
  assert.equal(w.states.at(-1).paused, true);
  w.lifecycle.samples(128);
  assert.equal(w.lifecycle.gaps[0].duration_s, 60);
  w.lifecycle.stop();
});

test("wake lock resolves after Stop: immediately release instead of retaining it", async () => {
  let finish, releases = 0;
  const lock = Object.assign(new Target(), {released: false, async release() { releases++; }});
  const w = setup({wakeLock: {request: () => new Promise(resolve => { finish = resolve; })}});
  w.lifecycle.stop();
  finish(lock);
  await flush();
  assert.equal(releases, 1);
});

test("released wake lock is reacquired on return; Stop removes listeners", async () => {
  const locks = [];
  const w = setup({wakeLock: {async request() {
    const lock = Object.assign(new Target(), {released: false, async release() { this.released = true; }});
    locks.push(lock); return lock;
  }}});
  await flush();
  locks[0].released = true;
  locks[0].dispatchEvent(new Event("release"));
  w.doc.dispatchEvent(new Event("visibilitychange"));
  await flush();
  assert.equal(locks.length, 2);
  w.lifecycle.stop();
  w.doc.dispatchEvent(new Event("visibilitychange"));
  await flush();
  assert.equal(locks.length, 2);
  assert.equal(locks[1].released, true);
});

test("resume denial keeps an explicit action, muted PCM cannot falsely recover", async () => {
  const w = setup();
  w.context.state = "suspended";
  w.context.resume = async () => { throw new Error("gesture required"); };
  w.lifecycle.check();
  await w.lifecycle.resume();
  assert.equal(w.states.at(-1).paused, true);
  w.track.muted = true;
  w.context.state = "running";
  w.lifecycle.samples(128);
  assert.equal(w.states.at(-1).paused, true);
  w.lifecycle.stop();
  const count = w.reports.length;
  await w.lifecycle.resume();
  assert.equal(w.reports.length, count);
});

test("wake-lock denial does not stop capture", async () => {
  const w = setup({wakeLock: {async request() { throw new Error("not allowed"); }}});
  await flush();
  assert.equal(w.states.at(-1).wake, "unavailable");
  assert.equal(w.states.at(-1).paused, false);
  assert.equal(w.lifecycle.active, true);
  w.lifecycle.stop();
});

test("Stop finalizes an unrecovered gap and ignores subsequent track events", () => {
  const w = setup();
  w.track.muted = true;
  w.track.dispatchEvent(new Event("mute"));
  w.advance(45000, 0);
  w.lifecycle.stop();
  assert.equal(w.lifecycle.gaps[0].duration_s, 45);
  const reports = w.reports.length;
  w.track.dispatchEvent(new Event("ended"));
  assert.equal(w.reports.length, reports);
});

test("explicit resume can retry while an automatic resume promise is pending", async () => {
  const w = setup();
  let finish;
  w.context.state = "suspended";
  w.context.resume = () => new Promise(resolve => { finish = resolve; });
  const automatic = w.lifecycle.resume();
  w.context.resume = async () => { w.context.state = "running"; };
  await w.lifecycle.resume(true);
  assert.equal(w.context.state, "running");
  finish();
  await automatic;
  w.lifecycle.stop();
});

test("a terminated microphone is reported but never silently reopened", async () => {
  const w = setup();
  w.track.readyState = "ended";
  w.track.dispatchEvent(new Event("ended"));
  await w.lifecycle.resume(true);
  assert.equal(w.states.at(-1).ended, true);
  assert.equal(w.states.at(-1).paused, true);
  assert.equal(w.reports.at(-1).track_state, "ended");
  w.lifecycle.stop();
});
