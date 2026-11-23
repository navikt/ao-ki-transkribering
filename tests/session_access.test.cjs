const {test} = require("node:test");
const assert = require("node:assert/strict");
const {SessionAccess} = require("../frontend/static/session-access.js");

test("401 gates subsequent requests; only a successful authenticated probe resumes them", async () => {
  const calls = [], changes = [];
  let audioState = "recording", resumed = 0;
  let authorized = false;
  const session = new SessionAccess({request: async url => {
    calls.push(url);
    return {ok: authorized, status: authorized ? 200 : 401};
  }, onChange: required => {
    changes.push(required);
    if (!required) {
      resumed++;
      assert.equal(audioState, "recording");
    }
  }});
  assert.equal((await session.fetch("/recordings/test/chunks/89")).status, 401);
  assert.equal(session.loginUrl, "/oauth2/login");
  assert.equal((await session.fetch("/system/status")).status, 401);
  assert.equal(calls.length, 1);
  assert.equal(await session.recover(), false);
  assert.equal(session.required, true);
  const waiting = session.waitForAuthentication();
  authorized = true;
  assert.equal(await session.recover(), true);
  await waiting;
  assert.deepEqual(changes, [true, false]);
  assert.equal(resumed, 1);
  assert.equal(audioState, "recording");
  assert.equal(session.loginUrl, "/oauth2/login");
  assert.equal((await session.fetch("/recordings/test/chunks/89")).status, 200);
});

test("password expiry sends the login link to /login", async () => {
  const session = new SessionAccess({request: async () => ({
    ok: false, status: 401, headers: {get: name => name === "X-Access-Password-Required" ? "true" : null},
  })});
  assert.equal((await session.fetch("/system/status")).status, 401);
  assert.equal(session.required, true);
  assert.equal(session.loginUrl, "/login");
});

test("recovery updates the login link when an existing gate becomes password-required", async () => {
  const changes = [];
  let requests = 0;
  const session = new SessionAccess({request: async () => {
    requests++;
    return requests === 1
      ? {ok: false, status: 401, headers: {get: () => null}}
      : {ok: false, status: 401, headers: {get: name => name === "X-Access-Password-Required" ? "true" : null}};
  }, onChange: (required, loginUrl) => changes.push([required, loginUrl])});
  await session.fetch("/system/status");
  assert.equal(session.loginUrl, "/oauth2/login");
  assert.equal(await session.recover(), false);
  assert.equal(session.required, true);
  assert.equal(session.loginUrl, "/login");
  assert.deepEqual(changes, [[true, "/oauth2/login"], [true, "/login"]]);
});

test("standard Entra denial keeps /oauth2/login as the recovery link", async () => {
  const session = new SessionAccess({request: async () => ({
    ok: false, status: 401, headers: {get: () => null},
  })});
  session.requireLogin("/login");
  assert.equal(await session.recover(), false);
  assert.equal(session.loginUrl, "/oauth2/login");
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
