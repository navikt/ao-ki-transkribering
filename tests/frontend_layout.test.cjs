const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

test("header and page title do not contain NAV branding", () => {
  const html = fs.readFileSync(path.join(__dirname, "../frontend/static/index.html"), "utf8");
  assert.doesNotMatch(html.match(/<header[\s\S]*?<\/header>/)[0], /nav-logo|NAV logo/);
  assert.equal(html.match(/<title>(.*?)<\/title>/)[1], "Møtetranskribering");
});

function workspace() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) {
      elements.set(id, {
        style: {}, dataset: {}, hidden: false, innerHTML: "", innerText: "", textContent: "", value: "",
        classList: { toggle() {}, add() {}, remove() {} },
        addEventListener() {},
        setAttribute(name, value) { this[name] = value; },
      });
    }
    return elements.get(id);
  };
  const context = vm.createContext({
    document: {
      getElementById: element,
      querySelector: element,
      querySelectorAll: () => [],
    },
    fetch: () => new Promise(() => {}),
    setInterval() {},
    clearInterval() {},
    navigator: { clipboard: { writeText: async () => {} } },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/static/app.js"), "utf8"), context);
  return { element, run: code => vm.runInContext(code, context) };
}

test("temporary model errors do not stop the microphone", async () => {
  const { element, run } = workspace();
  element("sanntid-nedlasting").remove = () => {};
  run(`
    navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [] }) };
    globalThis.location = { protocol: "https:", host: "meeting.example" };
    globalThis.WebSocket = class { static OPEN = 1; readyState = 1; send() {} };
  `);
  await run("startSanntid()");
  run('sanntidWs.onmessage({data: JSON.stringify({type:"feil", melding:"Modellen er opptatt", recoverable:true})})');
  assert.equal(run("sanntidAktivt"), true);
  assert.match(element("sanntid-status-tekst").textContent, /opptatt/);
});

test("fatal model error remains visible after microphone stops and socket closes", async () => {
  const { element, run } = workspace();
  element("sanntid-nedlasting").remove = () => {};
  run(`
    navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [] }) };
    globalThis.location = { protocol: "https:", host: "meeting.example" };
    globalThis.WebSocket = class { static OPEN = 1; readyState = 1; send() {} };
  `);
  await run("startSanntid()");
  run('sanntidWs.onmessage({data: JSON.stringify({type:"feil", melding:"Modellen er utilgjengelig"})}); sanntidWs.onclose({code:1000})');
  assert.equal(run("sanntidAktivt"), false);
  assert.match(element("sanntid-status-tekst").textContent, /utilgjengelig/);
});

for (const [protocol, expected] of [["https:", "wss:"], ["http:", "ws:"]]) {
  test(`microphone opens the correct WebSocket on ${protocol}`, async () => {
    const { element, run } = workspace();
    element("sanntid-nedlasting").remove = () => {};
    run(`
      navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [] }) };
      globalThis.location = { protocol: ${JSON.stringify(protocol)}, host: "meeting.example:8765" };
      globalThis.WebSocket = class {
        constructor(url) { globalThis.requestedSocketUrl = url; }
      };
    `);
    await run("startSanntid()");
    assert.equal(run("requestedSocketUrl"), `${expected}//meeting.example:8765/ws/sanntid`);
  });
}

test("microphone meter distinguishes silence, weak input, sound and clipping", () => {
  const { element, run } = workspace();
  for (const [sample, expected] of [[0, "stille"], [0.005, "svak"], [0.1, "lyd"], [1, "hoy"]]) {
    run(`oppdaterMikrofonNiva(new Float32Array(1600).fill(${sample}))`);
    assert.equal(element("mikrofonniva").dataset.tilstand, expected);
    const value = Number(element("mikrofonniva-meter")["aria-valuenow"]);
    assert.ok(value >= 0 && value <= 100);
  }
  run("nullstillMikrofonNiva()");
  assert.equal(element("mikrofonniva").dataset.tilstand, "av");
  assert.equal(element("mikrofonniva-meter")["aria-valuenow"], "0");
});

test("audio worklet updates the meter without changing audio sent for transcription", async () => {
  const { element, run } = workspace();
  run(`
    globalThis.AudioContext = class {
      audioWorklet = { addModule: async () => {} };
      createMediaStreamSource() { return { connect() {} }; }
    };
    globalThis.AudioWorkletNode = class { port = {}; connect() {} };
    globalThis.WebSocket = { OPEN: 1 };
    sanntidAktivt = true;
    sanntidWs = { readyState: 1, send(data) { globalThis.sentAudio = new Float32Array(data); } };
  `);
  await run("startAudioWorklet()");
  run("workletNode.port.onmessage({ data: new Float32Array(2560).fill(0.125) })");
  assert.equal(element("mikrofonniva").dataset.tilstand, "lyd");
  assert.equal(run("sentAudio.length"), 2560);
  assert.equal(run("sentAudio.every(sample => sample === 0.125)"), true);
  run("sanntidAktivt = false; nullstillMikrofonNiva(); workletNode.port.onmessage({data: new Float32Array(1600).fill(1)})");
  assert.equal(element("mikrofonniva").dataset.tilstand, "av");
});

test("audio modes toggle input and output together without clearing content", () => {
  const { element, run } = workspace();
  element("sanntid-tekst").textContent = "Recorded conversation";
  run('byttLydkilde("sanntid")');
  assert.equal(element("sanntid-resultat").hidden, false);
  assert.equal(element("seksjon-resultat").hidden, true);
  assert.equal(element("panel-batch").style.display, "none");
  assert.equal(element("lydkilde").value, "sanntid");
  run('byttLydkilde("batch")');
  assert.equal(element("sanntid-resultat").hidden, true);
  assert.equal(element("seksjon-resultat").hidden, false);
  assert.equal(element("live-referat-wrap").hidden, true);
  assert.equal(element("sanntid-tekst").textContent, "Recorded conversation");
});

test("recording cannot be hidden by switching audio source", () => {
  const { element, run } = workspace();
  run('byttLydkilde("sanntid"); sanntidAktivt = true; byttLydkilde("batch")');
  assert.equal(element("sanntid-resultat").hidden, false);
  assert.equal(element("panel-sanntid").style.display, "block");
});

test("reset restores the live empty state and hides completed actions", () => {
  const { element, run } = workspace();
  element("sanntid-tekst").innerHTML = "Previous transcript";
  run("nullstillSanntid()");
  assert.equal(element("sanntid-tekst").innerHTML, "");
  assert.equal(element("sanntid-tomtilstand").hidden, false);
  assert.equal(element("modul-transkripsjon").style.display, "none");
  assert.equal(element("sanntid-knapper").style.display, "none");
  assert.equal(element("referat-panel").style.display, "none");
});

test("reset cannot erase an ongoing recording", () => {
  const { element, run } = workspace();
  element("sanntid-tekst").innerHTML = "Ongoing transcript";
  run("sanntidAktivt = true; nullstillSanntid()");
  assert.equal(element("sanntid-tekst").innerHTML, "Ongoing transcript");
});

test("general and collaboration meetings do not require the adviser wording", () => {
  const { element, run } = workspace();
  for (const scenario of ["generelt_mote", "samarbeidsmote"]) {
    element("paragraf14-hake").checked = false;
    run("oppdaterMoteBekreftelse()");
    assert.doesNotMatch(element("mote-bekreftelse-tekst").textContent, /§14|§15/);
    assert.doesNotMatch(element("paragraf15-advarsel").textContent, /§15/);
    assert.equal(element("sanntid-start-knapp").disabled, true);
  }
});

test("changing paragraph confirmation clears general acknowledgement and disables audio", () => {
  const { element, run } = workspace();
  element("samtykke-hake").checked = true;
  run("oppdaterMoteBekreftelse()");
  assert.equal(element("fil-input").disabled, false);
  element("paragraf14-hake").checked = true;
  run("oppdaterMoteBekreftelse(true)");
  assert.match(element("paragraf15-advarsel").textContent, /§15/);
  assert.equal(element("samtykke-hake").checked, false);
  assert.equal(element("fil-input").disabled, true);
  assert.equal(element("sanntid-start-knapp").disabled, true);
});

test("acknowledgement cannot bypass model unavailability", () => {
  const { element, run } = workspace();
  element("samtykke-hake").checked = true;
  run('_settTranskripsjonTilgjengelig(false, "Unavailable"); oppdaterMoteBekreftelse()');
  assert.equal(element("fil-input").disabled, true);
});

test("editor enables copying only when it contains nonblank text", () => {
  const { element, run } = workspace();
  element("referat-redigert").value = "   ";
  run("oppdaterReferatEditor()");
  assert.equal(element("referat-redigert-kopier").disabled, true);
  element("referat-redigert").value = "Revised notes";
  element("referat-editor-status").textContent = "Kopiert";
  run("oppdaterReferatEditor()");
  assert.equal(element("referat-redigert-kopier").disabled, false);
  assert.equal(element("referat-editor-status").textContent, "");
});

test("regeneration and recording reset preserve manually edited notes", () => {
  const { element, run } = workspace();
  element("referat-redigert").value = "My reviewed notes";
  run('_visReferatStream("New generated summary"); nullstillSanntid(); nyttOpptak()');
  assert.equal(element("referat-redigert").value, "My reviewed notes");
});

test("editor copies the edited text rather than the generated output", async () => {
  const { element, run } = workspace();
  element("referat-redigert").value = "Edited notes\nSecond line";
  run('navigator.clipboard.writeText = async text => { document.getElementById("copied").textContent = text; }');
  await run("kopierRedigertReferat()");
  assert.equal(element("copied").textContent, "Edited notes\nSecond line");
  assert.equal(element("referat-editor-status").textContent, "Kopiert");
});

test("editor reports clipboard failures without losing text", async () => {
  const { element, run } = workspace();
  element("referat-redigert").value = "Reviewed notes";
  run('navigator.clipboard.writeText = async () => { throw new Error("Denied"); }');
  await run("kopierRedigertReferat()");
  assert.match(element("referat-editor-status").textContent, /Kunne ikke kopiere/);
  assert.equal(element("referat-redigert").value, "Reviewed notes");
});

test("template changes do not alter recording confirmation or edited notes", () => {
  const { element, run } = workspace();
  element("paragraf14-hake").checked = true;
  element("samtykke-hake").checked = true;
  run("oppdaterMoteBekreftelse()");
  element("referat-redigert").value = "Reviewed notes";
  element("referat-scenario").value = "egendefinert";
  run("oppdaterReferatMal()");
  assert.equal(element("samtykke-hake").checked, true);
  assert.match(element("paragraf15-advarsel").textContent, /§15/);
  assert.equal(element("referat-egne-instruksjoner").hidden, false);
  assert.equal(element("referat-redigert").value, "Reviewed notes");
});

test("custom template requires instructions and preset requests omit them", () => {
  const { element, run } = workspace();
  element("referat-scenario").value = "egendefinert";
  element("referat-instruksjoner").value = "  ";
  assert.equal(run("_referatValg()"), null);
  element("referat-instruksjoner").value = "  Focus on decisions  ";
  assert.equal(run("_referatValg().instruksjoner"), "Focus on decisions");
  element("referat-scenario").value = "generelt_mote";
  run("oppdaterReferatMal()");
  assert.equal(element("referat-egne-instruksjoner").hidden, true);
  assert.equal(run("_referatValg().instruksjoner"), undefined);
});

test("unified generation dispatches summary and referat using the selected audio source", () => {
  const { element, run } = workspace();
  run('hentSammendrag = kilde => "summary:" + kilde; hentReferat = kilde => "referat:" + kilde;');
  element(".app-main-grid").dataset.lydkilde = "batch";
  element("referat-scenario").value = "sammendrag";
  assert.equal(run("hentReferatFraArbeidsflate()"), "summary:batch");
  element(".app-main-grid").dataset.lydkilde = "sanntid";
  assert.equal(run("hentReferatFraArbeidsflate()"), "summary:sanntid");
  element("referat-scenario").value = "veiledermote";
  assert.equal(run("hentReferatFraArbeidsflate()"), "referat:sanntid");
});

test("recording status hides duplicate recording text but preserves exceptional states", () => {
  const { element, run } = workspace();
  run('settSanntidStatus("Tar opp …")');
  assert.equal(element("sanntid-status").style.display, "none");
  run('settSanntidStatus("Kobler til …")');
  assert.equal(element("sanntid-status").style.display, "flex");
  run('settSanntidStatus("WebSocket-feil")');
  assert.equal(element("sanntid-status").style.display, "flex");
  assert.equal(element("sanntid-status-tekst").textContent, "WebSocket-feil");
});

test("live summary uses the summary endpoint, not a referat scenario", async () => {
  const { element, run } = workspace();
  element("referat-scenario").value = "sammendrag";
  run('fetch = async (url, options) => { document.getElementById("request").value = url; document.getElementById("body").value = options.body; return {ok: true, body: { getReader: () => ({read: async () => ({done: true})}) }}; }');
  await run('_triggerLiveReferat("Meeting transcript")');
  assert.equal(element("request").value, "/sammendrag/stream");
  assert.deepEqual(JSON.parse(element("body").value), {transkripsjon: "Meeting transcript"});
});
