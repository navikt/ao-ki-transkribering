const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const {summaryDiffHtml} = require("../frontend/static/summary-comparison.js");

function workspace() {
  const nodes = new Map();
  const el = id => {
    if (!nodes.has(id)) nodes.set(id, {
      id, value: "", textContent: "", innerHTML: "", hidden: false, dataset: {}, children: [], listeners: {},
      appendChild(child) {
        if (child.parent) child.parent.children = child.parent.children.filter(item => item !== child);
        this.children.push(child); child.parent = this;
      },
      closest() { return el(id + "-label"); },
      querySelector() { return el("tablist"); },
      addEventListener(type, listener) { this.listeners[type] = listener; },
      setAttribute(name, value) { this[name] = value; },
      focus() { this.focused = true; },
    });
    return nodes.get(id);
  };
  el("referat-scenario").value = "basis_0_1";
  const definition = id => ({prompt: {id, title: id, version: "0.1"}, system_prompt: "System " + id,
    user_prompt: "Source: {transkripsjon}", temperature: 0.2, top_p: 0.9, max_tokens: 2048, context_tokens: 32768});
  const context = vm.createContext({
    document: {getElementById: el, querySelector: () => el("transcript"), body: el("body")},
    summaryDiffHtml,
    appFetch: async url => ({ok: true, json: async () => definition(url.split("/").at(-1))}),
    startSanntid() { throw new Error("View change restarted recording"); },
    stoppSanntid() { throw new Error("View change stopped recording"); },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/static/workspace.js"), "utf8"), context);
  const run = code => vm.runInContext(code, context);
  run("globalThis.meetingWorkspace = new MeetingWorkspace()");
  return {el, run};
}

async function loaded() {
  const w = workspace();
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(w.run("meetingWorkspace.definition"));
  return w;
}

test("switching views reuses the transcript and preserves meeting and editor state", async () => {
  const {el, run} = await loaded();
  el("transcript").textContent = "An ongoing meeting";
  el("referat-redigert").value = "Manual edits";
  el("sanntid-stopp-knapp").disabled = false;
  run("meetingWorkspace.setView('advanced'); meetingWorkspace.setTab('prompt')");
  assert.equal(el("transcript").parent, el("workspace-transcription"));
  assert.equal(el("workspace-prompt").hidden, false);
  run("meetingWorkspace.setView('simple')");
  assert.equal(el("transcript").parent, el("simple-transcription-slot"));
  assert.equal(el("transcript").textContent, "An ongoing meeting");
  assert.equal(el("referat-redigert").value, "Manual edits");
  assert.equal(el("sanntid-stopp-knapp").disabled, false);
  assert.equal(el("advanced-workspace").hidden, true);
  assert.equal(el("view-simple")["aria-pressed"], "true");
});

test("baseline sends no overrides; custom variants survive view and template switches", async () => {
  const {el, run} = await loaded();
  assert.equal(run("meetingWorkspace.overrides()"), null);
  el("workspace-system").value = "Custom system";
  run("meetingWorkspace.changed(); meetingWorkspace.setView('simple')");
  assert.equal(el("workspace-custom-indicator").hidden, false);
  assert.equal(run("meetingWorkspace.overrides().system_prompt"), "Custom system");
  el("referat-scenario").value = "utfyllende_0_1";
  await run("meetingWorkspace.syncPrompt()");
  assert.equal(run("meetingWorkspace.overrides()"), null);
  el("referat-scenario").value = "basis_0_1";
  await run("meetingWorkspace.syncPrompt()");
  assert.equal(el("workspace-system").value, "Custom system");
  run("meetingWorkspace.resetPrompt()");
  assert.equal(run("meetingWorkspace.overrides()"), null);
  assert.equal(el("workspace-custom-indicator").hidden, true);
});

test("invalid parameters and missing source marker block generation", async () => {
  const {el, run} = await loaded();
  for (const [field, value] of [["top-p", "0"], ["temperature", "2.1"], ["max-tokens", "8193"],
    ["max-tokens", "512.5"], ["temperature", ""], ["user", "No source marker"]]) {
    run("meetingWorkspace.resetPrompt()");
    el("workspace-" + field).value = value;
    assert.equal(run("meetingWorkspace.validate()"), false, field + ": " + value);
    assert.equal(el("body").dataset.view, "advanced");
    assert.equal(el("body").dataset.advancedTab, "prompt");
  }
});

test("an out-of-order template response cannot overwrite the current draft or unlock a busy editor", async () => {
  const {el, run} = await loaded();
  run("globalThis.responses = []; appFetch = url => new Promise(resolve => responses.push(resolve));");
  el("referat-scenario").value = "stikkord_0_1";
  const old = run("meetingWorkspace.syncPrompt()");
  el("referat-scenario").value = "utfyllende_0_1";
  const current = run("meetingWorkspace.syncPrompt()");
  run(`meetingWorkspace.setBusy(true); responses[1]({ok:true, json:async()=>({
    prompt:{id:'utfyllende_0_1',title:'Utfyllende'}, system_prompt:'New',user_prompt:'{transkripsjon}',
    temperature:0.2,top_p:0.9,max_tokens:3072,context_tokens:32768})});`);
  await current;
  run(`responses[0]({ok:true,json:async()=>({prompt:{id:'stikkord_0_1'},system_prompt:'Old'})});`);
  await old;
  assert.equal(el("workspace-system").value, "New");
  assert.equal(el("workspace-prompt-fields").disabled, true);
});

test("prompt diff escapes HTML and request diagnostics do not include prompt text", async () => {
  const {el, run} = await loaded();
  el("workspace-system").value = "<script>unsafe</script>";
  el("workspace-prompt-diff").open = true;
  run("meetingWorkspace.changed()");
  assert.doesNotMatch(el("workspace-prompt-diff-text").innerHTML, /<script>/);
  assert.match(el("workspace-prompt-diff-text").innerHTML, /&lt;script&gt;/);
  run(`meetingWorkspace.captureRequest({transkripsjon:'Private source',modell:'borealis-12b',
    scenario:'basis_0_1',overrides:meetingWorkspace.overrides()})`);
  assert.equal(el("workspace-request-source").value, "Private source");
  assert.equal(el("workspace-request-system").value, "<script>unsafe</script>");
  assert.equal(el("workspace-request-user").value, "Source: Private source");
  assert.doesNotMatch(el("workspace-request-info").textContent, /Private source|unsafe/);
  assert.match(el("workspace-request-info").textContent, /borealis-12b/);
});

test("tab keyboard navigation updates selected and focus states", async () => {
  const {el} = await loaded();
  let prevented = false;
  el("tablist").listeners.keydown({key: "ArrowRight", preventDefault() {prevented = true;}});
  assert.equal(prevented, true);
  assert.equal(el("tab-prompt").focused, true);
  assert.equal(el("tab-prompt")["aria-selected"], "true");
  assert.equal(el("tab-prompt").tabIndex, 0);
  assert.equal(el("tab-transcription").tabIndex, -1);
});

test("automatic summaries do not switch the user's view for an invalid draft", async () => {
  const {el, run} = await loaded();
  el("workspace-user").value = "Currently editing";
  run("meetingWorkspace.setView('simple')");
  assert.equal(run("meetingWorkspace.validate(false)"), false);
  assert.equal(el("body").dataset.view, "simple");
});

test("a failed template load blocks generation and offers an explicit retry", async () => {
  const {el, run} = await loaded();
  el("referat-scenario").value = "stikkord_0_1";
  run("appFetch = async () => {throw new Error('offline');}");
  await run("meetingWorkspace.syncPrompt()");
  assert.equal(el("workspace-prompt-retry").hidden, false);
  assert.equal(run("meetingWorkspace.validate()"), false);
  assert.equal(el("workspace-prompt-fields").disabled, true);
  assert.equal(run("meetingWorkspace.overrides()"), null);
});

test("0.2 loads its repetition penalty, validates edits and retains the released baseline", async () => {
  const {el, run} = await loaded();
  el("referat-scenario").value = "basis_0_2";
  run(`appFetch = async () => ({ok:true,json:async()=>({prompt:{id:'basis_0_2',title:'Basis 0.2'},
    system_prompt:'System',user_prompt:'{transkripsjon}',temperature:0.2,top_p:0.9,
    max_tokens:2048,repeat_penalty:1.2,context_tokens:32768})});`);
  await run("meetingWorkspace.syncPrompt()");
  assert.equal(el("workspace-repeat-field").hidden, false);
  assert.equal(el("workspace-repeat").value, "1.2");
  assert.equal(run("meetingWorkspace.overrides()"), null);
  el("workspace-repeat").value = "1.05";
  run("meetingWorkspace.changed()");
  assert.equal(run("meetingWorkspace.overrides().repeat_penalty"), 1.05);
  assert.equal(run("meetingWorkspace.validate()"), true);
  for (const value of ["0.9", "2.1", ""]) {
    el("workspace-repeat").value = value;
    assert.equal(run("meetingWorkspace.validate()"), false);
  }
  run("meetingWorkspace.resetPrompt()");
  assert.equal(el("workspace-repeat").value, "1.2");
  assert.equal(run("meetingWorkspace.overrides()"), null);
});
