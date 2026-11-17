const test = require('node:test');
const assert = require('node:assert/strict');
const {SessionFeedback} = require('../frontend/static/feedback.js');

function setup(request) {
  const nodes = new Map();
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {value: '', checked: false, hidden: false,
      disabled: false, textContent: '', focus() {}, addEventListener(type, callback) { this[type] = callback; }});
    return nodes.get(selector);
  };
  const root = {hidden: true, querySelector: node, querySelectorAll: () => [], addEventListener() {}};
  node('[name="rating"]').value = '4';
  node('[name="category"]').value = 'general';
  node('[name="target"]').value = 'batch';
  const feedback = new SessionFeedback({root, request, transcript: () => 'PRIVATE TRANSCRIPT'});
  feedback.config = {enabled: true, attachments: true};
  return {feedback, node};
}

test('feedback sends no meeting content unless explicitly attached', async () => {
  const requests = [];
  const {feedback, node} = setup(async (url, options) => {
    requests.push({url, body: options.body});
    return {ok: true, json: async () => ({case_id: 'case'})};
  });
  await feedback.submit();
  const body = JSON.parse(requests[0].body);
  assert.equal(body.attach, false);
  assert.equal(body.rating, 4);
  assert.ok(!requests[0].body.includes('PRIVATE'));
  assert.match(node('[role="status"]').textContent, /Takk/);
  assert.equal(node('[data-feedback-case]').textContent, 'case');
});

test('ambiguous failure retries the exact original snapshot and submission ID', async () => {
  const requests = [];
  let attempt = 0;
  const {feedback, node} = setup(async (url, options) => {
    requests.push({url, body: options.body});
    if (++attempt === 1) throw new Error('network lost after upload');
    return {ok: true, json: async () => ({case_id: 'case'})};
  });
  node('[name="attach"]').checked = true;
  await feedback.submit();
  assert.equal(node('fieldset').disabled, true);
  node('[name="comment"]').value = 'changed after uncertain delivery';
  await feedback.submit();
  assert.deepEqual(requests[0], requests[1]);
  assert.equal(JSON.parse(requests[0].body).transcript, 'PRIVATE TRANSCRIPT');
});

test('generation attachment refers to the owned server job instead of browser text', async () => {
  let body;
  const {feedback, node} = setup(async (url, options) => {
    body = JSON.parse(options.body);
    return {ok: true, json: async () => ({case_id: 'case'})};
  });
  feedback.generations.set('job', {transcript: 'Exact input', summary: 'Original summary'});
  node('[name="target"]').value = 'job';
  node('[name="attach"]').checked = true;
  await feedback.submit();
  assert.equal(body.job_id, 'job');
  assert.ok(!('transcript' in body));
});

test('definitive rejection permits correction without a success message', async () => {
  const {feedback, node} = setup(async () => ({ok: false, status: 404}));
  await feedback.submit();
  assert.equal(feedback.pending, null);
  assert.equal(node('fieldset').disabled, false);
  assert.doesNotMatch(node('[role="status"]').textContent, /Takk/);
});

test('audio is explicitly selected, uploaded first and referenced by checksum', async () => {
  const requests = [];
  const {feedback, node} = setup(async (url, options) => {
    requests.push({url, body: options.body});
    return {ok: true, json: async () => url.endsWith('/audio') ? {sha256: 'a'.repeat(64)} : {case_id: 'case'}};
  });
  feedback.config.audio = true;
  feedback.config.max_audio_bytes = 1000;
  node('[name="attach"]').checked = true;
  node('[name="audio"]').checked = true;
  const audio = new Blob(['fictional audio'], {type: 'audio/wav'});
  node('[name="audio-file"]').files = [audio];
  await feedback.submit();
  assert.equal(requests.length, 2);
  assert.ok(requests[0].url.endsWith('/audio'));
  assert.equal(requests[0].body, audio);
  assert.equal(JSON.parse(requests[1].body).audio_sha256, 'a'.repeat(64));
});

test('a retry after case failure reuses already uploaded audio', async () => {
  const requests = [];
  let attempt = 0;
  const {feedback, node} = setup(async (url, options) => {
    requests.push({url, body: options.body});
    if (++attempt === 2) throw new Error('connection lost');
    return {ok: true, json: async () => url.endsWith('/audio') ? {sha256: 'a'.repeat(64)} : {case_id: 'case'}};
  });
  feedback.config.audio = true;
  feedback.config.max_audio_bytes = 1000;
  node('[name="attach"]').checked = true;
  node('[name="audio"]').checked = true;
  node('[name="audio-file"]').files = [new Blob(['audio'], {type: 'audio/wav'})];
  await feedback.submit();
  await feedback.submit();
  assert.equal(requests.length, 3);
  assert.deepEqual(requests[1], requests[2]);
});

test('rating and unavailable audio are explained before any submission', async () => {
  let calls = 0;
  const {feedback, node} = setup(async () => { calls++; });
  node('[name="rating"]').value = '';
  await feedback.submit();
  assert.match(node('[role="status"]').textContent, /1 til 5/);
  node('[name="rating"]').value = '4';
  feedback.config.audio = true;
  node('[name="attach"]').checked = true;
  node('[name="audio"]').checked = true;
  await feedback.submit();
  assert.match(node('[role="status"]').textContent, /Velg en lydfil/);
  assert.equal(calls, 0);
});
