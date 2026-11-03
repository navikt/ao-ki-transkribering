const {test} = require("node:test");
const assert = require("node:assert/strict");
const {summaryDiffHtml, SummaryComparison} = require("../frontend/static/summary-comparison.js");

test("word diff highlights Norwegian changes and escapes model output", () => {
  const html = summaryDiffHtml("Vi møtes tirsdag.\n<script>", "Vi møtes torsdag.\n<img src=x>");
  assert.match(html, /<del>tirsdag<\/del>/);
  assert.match(html, /<ins>torsdag<\/ins>/);
  assert.doesNotMatch(html, /<script>|<img/);
  assert.match(html, /&lt;/);
});

test("diff handles unchanged text, whitespace and fully replaced summaries", () => {
  assert.match(summaryDiffHtml("Uendret", "Uendret"), /Ingen endringer/);
  assert.match(summaryDiffHtml("A", "A\nB"), /<ins>\nB<\/ins>/);
  assert.match(summaryDiffHtml("Gammelt", "Nytt"), /<del>Gammelt<\/del><ins>Nytt<\/ins>/);
});

test("expensive comparisons fall back to safe complete-text differences", () => {
  const html = summaryDiffHtml("<gammelt>", "<nytt>", () => undefined);
  assert.equal(html, '<del>&lt;gammelt&gt;</del><ins>&lt;nytt&gt;</ins>');
});

test("comparison is lazy, retains only adjacent successes and reset clears history", () => {
  const listeners = {};
  const details = {open: false, addEventListener: (name, handler) => { listeners[name] = handler; }};
  const body = {innerHTML: ""};
  const comparison = new SummaryComparison(details, body);
  comparison.update("Første");
  assert.equal(details.hidden, true);
  comparison.update("Andre");
  assert.equal(details.hidden, false);
  assert.equal(body.innerHTML, "");
  details.open = true;
  listeners.toggle();
  assert.match(body.innerHTML, /<del>Første<\/del>/);
  comparison.update("Tredje");
  assert.match(body.innerHTML, /<del>Andre<\/del>/);
  assert.doesNotMatch(body.innerHTML, /Første/);
  comparison.reset();
  assert.equal(details.hidden, true);
  assert.equal(details.open, false);
  assert.equal(body.innerHTML, "");
  assert.equal(comparison.previous, null);
  assert.equal(comparison.current, null);
});
