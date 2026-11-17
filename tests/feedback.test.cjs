const test = require('node:test');
const assert = require('node:assert/strict');
const {SessionFeedback} = require('../frontend/static/feedback.js');

function setup(request) {
  const nodes = new Map();
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {value: '', checked: false, hidden: false,
      disabled: false, textContent: '', addEventListener(type, callback) { this[type] = callback; }});
    return nodes.get(selector);
  };
  const root = {hidden: true, querySelector: node};
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
  assert.match(node('[role="status"]').textContent, /Referanse: case/);
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
