const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

test("header and page title do not contain NAV branding", () => {
  const html = fs.readFileSync(path.join(__dirname, "../frontend/static/index.html"), "utf8");
  assert.doesNotMatch(html.match(/<header[\s\S]*?<\/header>/)[0], /nav-logo|NAV logo/);
  assert.equal(html.match(/<title>(.*?)<\/title>/)[1], "Transkriberit");
});

test("top bar places confirmation before recording controls and keeps a compact header", () => {
  const html = fs.readFileSync(path.join(__dirname, "../frontend/static/index.html"), "utf8");
  assert.ok(html.indexOf('id="lydkilde"') < html.indexOf('id="samtykke-hake"'));
  assert.ok(html.indexOf('id="samtykke-hake"') < html.indexOf('id="sanntid-start-knapp"'));
  assert.ok(html.indexOf('id="paragraf14-hake"') > html.indexOf('id="referat-scenario"'));
  assert.doesNotMatch(html.match(/<header[\s\S]*?<\/header>/)[0], /class="ingress"/);
});

function workspace() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) {
      elements.set(id, {
        style: {}, dataset: {}, hidden: false, innerHTML: "", innerText: "", textContent: "", value: "",
        classList: { toggle() {}, add() {}, remove() {} },
        addEventListener() {},
        after() {}, remove() {}, appendChild() {},
        setAttribute(name, value) { this[name] = value; },
      });
    }
    return elements.get(id);
  };
  const context = vm.createContext({
    crypto: require("node:crypto").webcrypto,
    AbortController,
    setTimeout: callback => { queueMicrotask(callback); return 0; },
    clearTimeout() {},
    document: {
      getElementById: element,
      querySelector: element,
      querySelectorAll: () => [],
      createElement: () => element("created"),
    },
    fetch: () => new Promise(() => {}),
    setInterval() {},
    clearInterval() {},
    navigator: { clipboard: { writeText: async () => {} } },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/static/app.js"), "utf8"), context);
  vm.runInContext('globalThis.RecordingTransport = class { constructor() { return new WebSocket("/recordings"); } };', context);
  return { element, run: code => vm.runInContext(code, context) };
}

function transcriptWorkspace() {
  const w = workspace();
  w.run(`
    class TranscriptNode {
      children = []; dataset = {}; style = {}; className = ''; textContent = '';
      get lastElementChild() { return this.children.at(-1); }
      set innerHTML(value) { this.children = []; }
      appendChild(child) {
        if (child.tagName === 'fragment') this.children.push(...child.children);
        else this.children.push(child);
      }
      querySelectorAll(selector) {
        return this.children.flatMap(child => [
          ...(selector.startsWith('.') ? child.className.split(' ').includes(selector.slice(1)) : child.tagName === selector) ? [child] : [],
          ...child.querySelectorAll(selector),
        ]);
      }
      querySelector(selector) { return this.querySelectorAll(selector)[0]; }
    }
    document.createElement = tag => Object.assign(new TranscriptNode(), {tagName: tag});
    document.createDocumentFragment = () => document.createElement('fragment');
    globalThis.transcript = document.createElement('div');
    const getElement = document.getElementById;
    document.getElementById = id => ['sanntid-tekst', 'dialog-container'].includes(id) ? transcript : getElement(id);
    _byggTalerRad = () => {};
    _sjekkLiveReferatTerskel = () => {};
  `);
  return w;
}

test("live sentence continues across audio chunks and keeps its original start time", async () => {
  const {run} = transcriptWorkspace();
  run(`
    navigator.mediaDevices = {getUserMedia: async () => ({getTracks: () => []})};
    globalThis.WebSocket = class {};
  `);
  await run('startSanntid()');
  run(`
    for (const [sequence, start, tekst] of [[0, 12, 'Vi skal se på'], [1, 14, 'saken i morgen.']]) {
      sanntidWs.onmessage({data: JSON.stringify({type: 'segment', sequence, tekst,
        segmenter: [{tekst, start, slutt: start + 2, taler: 'SPEAKER_00'}]})});
    }
  `);
  assert.equal(run('transcript.children.length'), 1);
  assert.equal(run('transcript.querySelector(".dialog-tekst").textContent'), 'Vi skal se på saken i morgen.');
  assert.equal(run('transcript.querySelector("time").textContent'), '0:12');
  run(`sanntidWs.onmessage({data: JSON.stringify({type: 'segment', sequence: 2, tekst: 'Neste punkt',
    segmenter: [
      {tekst: 'Neste punkt', start: 16, slutt: 17, taler: 'SPEAKER_00'},
      {tekst: 'Ja.', start: 18, slutt: 19, taler: 'SPEAKER_01'}
    ]})})`);
  assert.equal(run('transcript.children.length'), 3);
  assert.equal(run('transcript.children[1].querySelector("time").textContent'), '0:16');
  assert.equal(run('transcript.children[2].dataset.taler'), 'SPEAKER_01');
  assert.match(run('kopierSanntidTekstRaw()'), /Vi skal se på saken i morgen\.\n/);
});

test("sentence endings recognize closing quotes but keep abbreviations and hesitation open", () => {
  const {run} = workspace();
  for (const text of ['Ferdig.', 'Er det riktig?', 'Ja!', 'Hun sa «ja.»']) {
    assert.equal(run(`erSetningsslutt(${JSON.stringify(text)})`), true, text);
  }
  for (const text of ['Vi trenger f.eks.', 'Møtet er kl.', 'Jeg tror ...', 'Jeg tror …', '   ']) {
    assert.equal(run(`erSetningsslutt(${JSON.stringify(text)})`), false, text);
  }
});

test("uploaded transcript groups sentence fragments but preserves sentence and speaker boundaries", () => {
  const {run} = transcriptWorkspace();
  run(`byggDialog([
    {start: 1, slutt: 2, taler: 'SPEAKER_00', tekst: 'Vi følger'},
    {start: 2, slutt: 4, taler: 'SPEAKER_00', tekst: 'opp i morgen.'},
    {start: 5, slutt: 6, taler: 'SPEAKER_00', tekst: 'Et nytt tema'},
    {start: 7, slutt: 8, taler: 'SPEAKER_01', tekst: 'Ja.'}
  ], '')`);
  assert.equal(run('transcript.children.length'), 3);
  assert.equal(run('transcript.children[0].querySelector(".dialog-tekst").textContent'), 'Vi følger opp i morgen.');
  assert.equal(run('transcript.children[1].querySelector(".dialog-tid").textContent'), '0:05');
  assert.equal(run('transcript.children[2].dataset.taler'), 'SPEAKER_01');
});

test("audio actions sit below recording controls and copy sits below the transcript", () => {
  const html = fs.readFileSync(path.join(__dirname, "../frontend/static/index.html"), "utf8");
  assert.ok(html.indexOf('id="opptak-handlinger"') > html.indexOf('id="sanntid-stopp-knapp"'));
  assert.ok(html.indexOf('id="opptak-handlinger"') < html.indexOf('id="sanntid-status"'));
  assert.ok(html.indexOf('onclick="kopierSanntidTekst()"') > html.indexOf('id="sanntid-tekst"'));
  const {element, run} = workspace();
  let attached = false;
  element('opptak-handlinger').appendChild = () => { attached = true; };
  run('_tilbyNedlasting()');
  assert.equal(attached, true);
});

test("failed uploaded transcription shows the server's actionable error", async () => {
  const {element, run} = workspace();
  run(`
    globalThis.intervals = [];
    setInterval = callback => {intervals.push(callback); return intervals.length;};
    fetch = async () => ({ok: true, json: async () => ({status: 'feil',
      feilmelding: 'Lydfilen er for stor for modell-API-et.'})});
    globalThis.Date = {now: () => 1000};
  `);
  await run('pollStatus("test-job", 1000)');
  await run('intervals.at(-1)()');
  assert.match(element('feil-boks').textContent, /for stor/);
});

test("technical diagnostics are grouped in developer mode and collapsed by default", () => {
  const html = fs.readFileSync(path.join(__dirname, "../frontend/static/index.html"), "utf8");
  const details = html.match(/<details id="mikrofon-diagnostikk"[\s\S]*?<\/details>/)[0];
  assert.match(details, /<summary>Utviklermodus<\/summary>/);
  assert.match(details, /id="mikrofon-diagnostikk-tekst"/);
  assert.doesNotMatch(details.split(">")[0], /\bopen\b/);
  assert.doesNotMatch(details, /id="(?:mikrofonniva|sanntid-status|sanntid-dekning)"/);
});

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

test("audio diagnostics show actual track settings without identifiers or HTML injection", () => {
  const {element, run} = workspace();
  run(`
    sanntidStream = {getAudioTracks: () => [{
      label: '<img src=x onerror=alert(1)> USB microphone', readyState: 'live', muted: false, enabled: true,
      getSettings: () => ({sampleRate: 48000, channelCount: 1, echoCancellation: true,
        noiseSuppression: false, autoGainControl: true, deviceId: 'PRIVATE-ID', groupId: 'PRIVATE-GROUP'})
    }]};
    audioCtx = {sampleRate: 16000};
    oppdaterLyddiagnostikk();
  `);
  const output = element('mikrofon-diagnostikk-tekst');
  assert.match(output.textContent, /USB microphone/);
  assert.match(output.textContent, /inngang \(Hz\): 48000/);
  assert.match(output.textContent, /behandling \(Hz\): 16000/);
  assert.match(output.textContent, /Ekkokansellering: På/);
  assert.match(output.textContent, /Støydemping: Av/);
  assert.doesNotMatch(output.textContent, /PRIVATE/);
  assert.equal(output.innerHTML, '');
  assert.equal(element('mikrofon-diagnostikk').hidden, false);
});

test("unreported audio settings are unknown rather than disabled", () => {
  const {element, run} = workspace();
  run(`sanntidStream = {getAudioTracks: () => [{readyState: 'ended', getSettings: () => ({})}]}; oppdaterLyddiagnostikk();`);
  const output = element('mikrofon-diagnostikk-tekst').textContent;
  assert.match(output, /Lydspor: Avsluttet/);
  assert.match(output, /Ekkokansellering: Ikke rapportert/);
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

test("unexpected socket close reports only connection metadata with the server recording ID", async () => {
  const { element, run } = workspace();
  element("sanntid-nedlasting").remove = () => {};
  run(`
    navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [] }) };
    navigator.onLine = true;
    document.visibilityState = "visible";
    globalThis.location = { protocol: "https:", host: "meeting.example" };
    globalThis.WebSocket = class { static OPEN = 1; readyState = 3; bufferedAmount = 128; };
    globalThis.console = { warn() {} };
    globalThis.reports = [];
    globalThis.fetch = async (url, options) => { reports.push({url, body: JSON.parse(options.body)}); };
  `);
  await run("startSanntid()");
  run(`sanntidWs.onmessage({data: JSON.stringify({type: "session", recording_id: "00000000-0000-4000-8000-000000000000"})});
       sanntidWs.onclose({code: 1006, reason: "PRIVATE"});`);
  const report = JSON.parse(run("JSON.stringify(reports[0])"));
  assert.equal(report.url, "/diagnostics/recording-close");
  assert.equal(report.body.recording_id, "00000000-0000-4000-8000-000000000000");
  assert.equal(report.body.code, 1006);
  assert.equal(report.body.buffered_bytes, 128);
  assert.equal(report.body.visibility, "visible");
  assert.equal(report.body.was_clean, false);
  assert.equal(report.body.heartbeat_count, 0);
  assert.equal(report.body.last_heartbeat_age_s, null);
  assert.doesNotMatch(JSON.stringify(report), /PRIVATE/);
  assert.equal(run("sanntidAktivt"), true);
});

test("browser acknowledges heartbeat sequence and reports receipt on disconnect", async () => {
  const { element, run } = workspace();
  element("sanntid-nedlasting").remove = () => {};
  run(`
    navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [] }) };
    globalThis.location = { protocol: "https:", host: "meeting.example" };
    globalThis.sent = [];
    globalThis.reports = [];
    globalThis.console = { warn() {} };
    globalThis.WebSocket = class {
      static OPEN = 1; readyState = 1;
      send(data) { sent.push(JSON.parse(data)); }
    };
    globalThis.fetch = async (url, options) => { reports.push(JSON.parse(options.body)); };
  `);
  await run("startSanntid()");
  run(`
    sanntidWs.onmessage({data: JSON.stringify({type: "session", recording_id: "00000000-0000-4000-8000-000000000000"})});
    sanntidWs.onmessage({data: JSON.stringify({type: "heartbeat", sequence: 3})});
    sanntidWs.onclose({code: 1006, wasClean: false});
  `);
  assert.deepEqual(JSON.parse(run("JSON.stringify(sent)")), [{type: "heartbeat_ack", sequence: 3}]);
  assert.equal(run("reports[0].heartbeat_count"), 1);
  assert.ok(run("reports[0].last_heartbeat_age_s") >= 0);
});

for (const [protocol, expected] of [["https:", "wss:"], ["http:", "ws:"]]) {
  test(`microphone uses recoverable transport on ${protocol}`, async () => {
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
    assert.equal(run("requestedSocketUrl"), "/recordings");
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

test("restricted output is optional and does not revoke recording acknowledgement", () => {
  const { element, run } = workspace();
  element("samtykke-hake").checked = true;
  run("oppdaterMoteBekreftelse()");
  assert.equal(element("fil-input").disabled, false);
  element("paragraf14-hake").checked = true;
  element("referat-scenario").value = "veiledermote";
  run("oppdaterReferatMal()");
  assert.match(element("paragraf15-advarsel").textContent, /§15/);
  assert.equal(element("samtykke-hake").checked, true);
  assert.equal(element("fil-input").disabled, false);
  assert.equal(element("sanntid-start-knapp").disabled, false);
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
  assert.doesNotMatch(element("paragraf15-advarsel").textContent, /§15/);
  assert.equal(element("referat-egne-instruksjoner").hidden, false);
  assert.equal(element("referat-redigert").value, "Reviewed notes");
});

test("adviser template defaults to the new format and sends restriction only for that template", () => {
  const {element, run} = workspace();
  element("referat-scenario").value = "veiledermote";
  element("paragraf14-hake").checked = false;
  run("oppdaterReferatMal()");
  assert.equal(element("referat-avgrensning").hidden, false);
  assert.equal(run("_referatValg().begrenset_referat"), false);
  assert.equal(run("_referatValg().veileder_variant"), "oppfolging");
  assert.equal(element("veileder-variant-valg").hidden, false);
  element("paragraf14-hake").checked = true;
  assert.equal(run("_referatValg().begrenset_referat"), true);
  assert.match(run("_referatValgTittel(_referatValg())"), /Begrenset/);
  element("referat-scenario").value = "generelt_mote";
  run("oppdaterReferatMal()");
  assert.equal(element("referat-avgrensning").hidden, true);
  assert.equal(run("_referatValg().begrenset_referat"), undefined);
  assert.equal(run("_referatValg().veileder_variant"), undefined);
  assert.equal(element("veileder-variant-valg").hidden, true);
});

test("output format is locked while a final or live draft is in flight", () => {
  const {element, run} = workspace();
  run("_referatGenererer = true; _oppdaterReferatValgLaas()");
  assert.equal(element("paragraf14-hake").disabled, true);
  assert.equal(element("referat-scenario").disabled, true);
  assert.equal(element("veileder-variant").disabled, true);
  run("_referatGenererer = false; _liveReferatOppdaterer = true; _oppdaterReferatValgLaas()");
  assert.equal(element("paragraf14-hake").disabled, true);
  run("_liveReferatOppdaterer = false; _oppdaterReferatValgLaas()");
  assert.equal(element("paragraf14-hake").disabled, false);
  assert.equal(element("veileder-variant").disabled, false);
});

test("adviser purpose changes payload and result title without touching notes or recording", () => {
  const {element, run} = workspace();
  element("referat-scenario").value = "veiledermote";
  element("veileder-variant").value = "kartlegging";
  element("paragraf14-hake").checked = false;
  element("samtykke-hake").checked = true;
  element("referat-redigert").value = "Reviewed notes";
  run("oppdaterReferatMal()");
  assert.equal(run("_referatValg().veileder_variant"), "kartlegging");
  assert.match(run("_referatValgTittel(_referatValg())"), /Kartlegging/);
  element("paragraf14-hake").checked = true;
  assert.match(run("_referatValgTittel(_referatValg())"), /Begrenset.*Kartlegging/);
  element("veileder-variant").value = "oppfolging";
  run("oppdaterReferatMal()");
  assert.equal(run("_referatValg().veileder_variant"), "oppfolging");
  assert.match(run("_referatValgTittel(_referatValg())"), /Oppfølging/);
  assert.equal(element("samtykke-hake").checked, true);
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

test("live summary uses a summary job, not a referat scenario", async () => {
  const { element, run } = workspace();
  element("referat-scenario").value = "sammendrag";
  run('fetch = async (url, options) => { document.getElementById("request").value = url; document.getElementById("body").value = options.body; return {ok: true, json: async () => ({status: "ferdig", tekst: "Resultat"})}; }');
  await run('_triggerLiveReferat("Meeting transcript")');
  assert.match(element("request").value, /^\/llm\/handlinger\/sammendrag\/jobber\//);
  assert.deepEqual(JSON.parse(element("body").value), {transkripsjon: "Meeting transcript"});
});

test("summary generation shows section-processing progress instead of a stale countdown", async () => {
  const {element, run} = workspace();
  run(`
    _visReferatResultat = text => {document.getElementById('result').textContent = text;};
    const events = [
      {status: 'genererer', melding: 'Behandler del 2 av 4 …'},
      {status: 'ferdig', tekst: 'Hele møtet er oppsummert.'},
    ];
    fetch = async () => ({ok: true, json: async () => events.shift()});
  `);
  await run('_streamReferat("/referat/stream", "Langt møte", "Referat")');
  assert.equal(element('referat-laster-tekst').textContent, 'Behandler del 2 av 4 …');
  assert.equal(element('referat-progress-wrap').style.display, 'none');
  assert.equal(run('_referatProgressTimer'), null);
  assert.equal(element('result').textContent, 'Hele møtet er oppsummert.');
});

test("summary recovers a lost submission response and a disconnected status request", async () => {
  const {element, run} = workspace();
  run(`
    globalThis.crypto = {randomUUID: () => '00000000-0000-4000-8000-000000000000'};
    globalThis.TextDecoder = class {decode(value) {return value;}};
    globalThis.setTimeout = callback => {callback();};
    document.getElementById('referat-panel').scrollIntoView = () => {};
    _hentTranskripsjonTekst = () => 'Vi avtaler fredag.';
    _visReferatPanel = () => {};
    _visReferatResultat = text => {document.getElementById('result').textContent = text;};
    _visReferatFeil = text => {document.getElementById('error').textContent = text;};
    globalThis.submissions = [];
    let polls = 0;
    fetch = async (url, options) => {
      if (url.endsWith('/stream')) return {ok: true, body: {getReader: () => ({read: async () => {
        throw new TypeError('network error');
      }})}};
      if (options?.method === 'POST') {
        submissions.push(url);
        if (submissions.length === 1) throw new TypeError('network error');
        return {ok: true, json: async () => ({status: 'genererer', melding: 'Behandler del 1 av 2 …'})};
      }
      if (++polls === 1) throw new TypeError('network error');
      return {ok: true, json: async () => ({status: 'ferdig', tekst: 'Avtalt fredag.', modell: 'borealis-12b'})};
    };
  `);
  element('referat-scenario').value = 'generelt_mote';
  await run('hentReferat("sanntid")');
  assert.equal(element('error').textContent, '');
  assert.equal(element('result').textContent, 'Avtalt fredag.');
  assert.equal(run('submissions.length'), 2);
  assert.equal(run('submissions[0] === submissions[1]'), true);
});

test("summary stops retrying when login expires", async () => {
  const {run} = workspace();
  run(`
    globalThis.requests = 0;
    fetch = async () => { requests++; return {ok: false, status: 401}; };
  `);
  await assert.rejects(run('_genererLlmJobb("referat", {}, () => {})'), /Innlogging kreves/);
  assert.equal(run('requests'), 1);
});

test("summary polls the same accepted job without resubmitting after a reset", async () => {
  const {run} = workspace();
  run(`
    globalThis.posts = 0;
    let polls = 0;
    fetch = async (url, options) => {
      if (options.method === 'POST') {
        posts++;
        return {ok: true, json: async () => ({status: 'genererer'})};
      }
      if (++polls === 1) throw new TypeError('network error');
      return {ok: true, json: async () => ({status: 'ferdig', tekst: 'Resultat'})};
    };
  `);
  assert.equal((await run('_genererLlmJobb("referat", {}, () => {})')).tekst, 'Resultat');
  assert.equal(run('posts'), 1);
});

test("intentional cancellation stops summary polling", async () => {
  const {run} = workspace();
  run(`
    globalThis.controller = new AbortController();
    fetch = async () => {
      controller.abort();
      throw new TypeError('network error');
    };
  `);
  await assert.rejects(run('_genererLlmJobb("referat", {}, () => {}, controller.signal)'), {name: 'AbortError'});
});
