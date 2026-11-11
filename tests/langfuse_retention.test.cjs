const { test } = require('node:test');
const assert = require('node:assert/strict');

const modulePromise = import('../scripts/langfuse-retention.mjs');
const policy = { projectId: 'project-a', from: '2026-09-01T00:00:00Z',
  now: new Date('2026-11-11T12:00:00Z') };
const old = id => ({ projectId: 'project-a', traceId: id, startTime: '2026-09-10T12:00:00Z',
  endTime: '2026-09-10T12:01:00Z', updatedAt: '2026-09-10T12:02:00Z' });

function mockApi({ rows = [old('trace-a')], recent = [] } = {}) {
  const requests = [];
  const request = async (method, path, params, body) => {
    requests.push({ method, path, params, body });
    if (method === 'DELETE') return { message: 'Queued' };
    assert.equal(params.fields, 'time');
    if (params.traceId) return { data: recent, meta: {} };
    return { data: rows.filter(row => new Date(row.startTime) >= new Date(params.fromStartTime)
      && new Date(row.startTime) <= new Date(params.toStartTime)), meta: {} };
  };
  return { request, requests };
}

test('default dry run discovers only old traces and never deletes or prints IDs', async () => {
  const { cleanup } = await modulePromise;
  const api = mockApi();
  const result = await cleanup({ ...policy, request: api.request });
  assert.equal(result.mode, 'dry-run');
  assert.equal(result.eligible, 1);
  assert.equal(result.deletion_requested, 0);
  assert.ok(api.requests.every(r => r.method === 'GET'));
  assert.ok(!JSON.stringify(result).includes('trace-a'));
});

test('apply requires explicit matching project before making requests', async () => {
  const { cleanup } = await modulePromise;
  const api = mockApi();
  await assert.rejects(cleanup({ ...policy, request: api.request, apply: true }), /confirmation/);
  await assert.rejects(cleanup({ ...policy, request: api.request, apply: true, confirmProject: 'other' }), /confirmation/);
  assert.equal(api.requests.length, 0);
});

test('confirmed apply submits one bounded supported trace deletion', async () => {
  const { cleanup } = await modulePromise;
  const api = mockApi();
  const result = await cleanup({ ...policy, request: api.request, apply: true, confirmProject: 'project-a' });
  assert.equal(result.deletion_requested, 1);
  assert.deepEqual(api.requests.at(-1), { method: 'DELETE', path: '/api/public/traces',
    params: {}, body: { traceIds: ['trace-a'] } });
});

test('recent child observations protect an old-root trace', async () => {
  const { cleanup } = await modulePromise;
  const api = mockApi({ recent: [{ ...old('trace-a'), startTime: '2026-11-10T00:00:00Z' }] });
  const result = await cleanup({ ...policy, request: api.request, apply: true, confirmProject: 'project-a' });
  assert.equal(result.eligible, 0);
  assert.equal(result.recent_traces_skipped, 1);
  assert.ok(api.requests.every(r => r.method === 'GET'));
});

test('unfinished and recently updated roots are not candidates', async () => {
  const { cleanup } = await modulePromise;
  const api = mockApi({ rows: [{ ...old('active'), endTime: null },
    { ...old('updated'), updatedAt: '2026-11-10T00:00:00Z' }] });
  assert.equal((await cleanup({ ...policy, request: api.request })).eligible, 0);
});

test('cap overflow and unexpected projects fail before any deletion', async () => {
  const { cleanup } = await modulePromise;
  for (const rows of [[old('a'), old('b')], [{ ...old('a'), projectId: 'other' }]]) {
    const api = mockApi({ rows });
    await assert.rejects(cleanup({ ...policy, request: api.request, maxTraces: 1,
      apply: true, confirmProject: 'project-a' }), /cap|scope/);
    assert.ok(api.requests.every(r => r.method === 'GET'));
  }
});

test('repeated cursors cannot loop or partially delete', async () => {
  const { cleanup } = await modulePromise;
  const requests = [];
  const request = async method => { requests.push(method); return { data: [old('a')], meta: { cursor: 'same' } }; };
  await assert.rejects(cleanup({ ...policy, request, apply: true, confirmProject: 'project-a' }), /cursor/);
  assert.deepEqual(requests, ['GET', 'GET']);
});

test('out-of-window records fail closed', async () => {
  const { cleanup } = await modulePromise;
  const request = async () => ({ data: [{ ...old('a'), startTime: '2026-11-10T00:00:00Z' }], meta: {} });
  await assert.rejects(cleanup({ ...policy, request }), /time window/);
});

test('API wrapper requires safe origins and never follows redirects with credentials', async () => {
  const { apiRequest } = await modulePromise;
  for (const origin of ['http://outside.example', 'https://user:pass@host.example', 'https://host.example/?key=PRIVATE']) {
    assert.throws(() => apiRequest(origin, 'pk-lf-test', 'sk-lf-PRIVATE'));
  }
  const request = apiRequest('http://127.0.0.1:3000', 'pk-lf-test', 'sk-lf-PRIVATE', async (url, options) => {
    assert.equal(options.redirect, 'error');
    return { ok: false, status: 403, json: async () => ({ message: 'PRIVATE' }) };
  });
  await assert.rejects(request('GET', '/api/public/v2/observations', {}), { message: 'Retention API HTTP 403' });
});

test('connection errors cannot expose credentials or response bodies', async () => {
  const { apiRequest } = await modulePromise;
  const request = apiRequest('https://host.example', 'pk-lf-test', 'sk-lf-PRIVATE', async () => {
    throw new Error('PRIVATE_AUTH');
  });
  await assert.rejects(request('GET', '/api/public/v2/observations', {}), { message: 'Retention API connection failed' });
});
