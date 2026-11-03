const {test} = require("node:test");
const assert = require("node:assert/strict");
const {SessionAccess} = require("../frontend/static/session-access.js");

test("401 gates subsequent requests; only a successful authenticated probe resumes them", async () => {
  const calls = [], changes = [];
  let authorized = false;
  const session = new SessionAccess({request: async url => {
    calls.push(url);
    return {ok: authorized, status: authorized ? 200 : 401};
  }, onChange: required => changes.push(required)});
  assert.equal((await session.fetch("/recordings/test/chunks/89")).status, 401);
  assert.equal((await session.fetch("/system/status")).status, 401);
  assert.equal(calls.length, 1);
  assert.equal(await session.recover(), false);
  assert.equal(session.required, true);
  const waiting = session.waitForAuthentication();
  authorized = true;
  assert.equal(await session.recover(), true);
  await waiting;
  assert.deepEqual(changes, [true, false]);
  assert.equal((await session.fetch("/recordings/test/chunks/89")).status, 200);
});

test("unavailable probe never clears authentication gate; waiting jobs can be cancelled", async () => {
  const session = new SessionAccess({request: async () => { throw new Error("offline"); }});
  session.requireLogin();
  assert.equal(await session.recover(), false);
  const controller = new AbortController();
  const waiting = session.waitForAuthentication(controller.signal);
  controller.abort();
  await assert.rejects(waiting, {name: "AbortError"});
  assert.equal(session.required, true);
  assert.equal(session.waiters.size, 0);
});

test("403 is not a login expiration and does not gate other requests", async () => {
  const session = new SessionAccess({request: async () => ({ok: false, status: 403})});
  assert.equal((await session.fetch("/transkriber")).status, 403);
  assert.equal(session.required, false);
});
