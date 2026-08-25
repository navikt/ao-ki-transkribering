// ---- Samtykke ----
document.getElementById("samtykke-hake").addEventListener("change", function () {
  document.getElementById("samtykke-knapp").disabled = !this.checked;
});

function bekreftSamtykke() {
  visSeksjon("opptak");
}

// ---- Seksjonsnavigasjon ----
function visSeksjon(navn) {
  document.querySelectorAll(".seksjon").forEach(s => s.classList.remove("aktiv"));
  document.getElementById("seksjon-" + navn).classList.add("aktiv");
}

// ---- MediaRecorder-opptak ----
let mediaRecorder = null;
let opptakChunks = [];
let timerIntervall = null;
let opptakSekunder = 0;

async function startOpptak() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    opptakChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = e => { if (e.data.size > 0) opptakChunks.push(e.data); };
    mediaRecorder.onstop = () => {
      stream.getTracks().forEach(t => t.stop());
      const blob = new Blob(opptakChunks, { type: "audio/webm" });
      sendTilServer(blob, "opptak.webm");
    };
    mediaRecorder.start(1000);

    document.getElementById("knapp-start").disabled = true;
    document.getElementById("knapp-stopp").disabled = false;
    document.getElementById("timer").style.display = "inline";
    opptakSekunder = 0;
    oppdaterTimer();
    timerIntervall = setInterval(() => { opptakSekunder++; oppdaterTimer(); }, 1000);
  } catch (err) {
    viseFeil("Mikrofontilgang nektet: " + err.message);
  }
}

function stoppOpptak() {
  clearInterval(timerIntervall);
  document.getElementById("knapp-start").disabled = false;
  document.getElementById("knapp-stopp").disabled = true;
  document.getElementById("timer").style.display = "none";
  if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
}

function oppdaterTimer() {
  const m = Math.floor(opptakSekunder / 60);
  const s = String(opptakSekunder % 60).padStart(2, "0");
  document.getElementById("timer-tekst").textContent = m + ":" + s;
}

// ---- Filopplasting ----
function lastOppFil() {
  const fil = document.getElementById("fil-input").files[0];
  if (!fil) return;
  sendTilServer(fil, fil.name);
}

// ---- Send til server ----
async function sendTilServer(blob, filnavn) {
  document.getElementById("feil-boks").style.display = "none";
  const fremdrift = document.getElementById("fremdrift");
  fremdrift.classList.add("synlig");
  settFremdriftTekst("Laster opp …");

  const skjema = new FormData();
  skjema.append("lydfil", blob, filnavn);
  const nTalere = document.getElementById("n-talere-velger")?.value ?? "2";
  skjema.append("n_talere", nTalere);

  let jobbId;
  try {
    const res = await fetch("/transkriber", { method: "POST", body: skjema });
    if (!res.ok) throw new Error("Opplasting feilet: " + res.status);
    const data = await res.json();
    jobbId = data.jobb_id;
  } catch (err) {
    fremdrift.classList.remove("synlig");
    viseFeil(err.message);
    return;
  }

  settFremdriftTekst("Transkriberer …");
  pollStatus(jobbId, Date.now());
}

// ---- Polling ----
let _timerIntervall = null;

function _formaterTid(sekunder) {
  const s = Math.round(sekunder);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s/60)}m ${s%60}s`;
}

async function pollStatus(jobbId, startMs) {
  const bar  = document.getElementById("fremdrift-bar");
  const fase = document.getElementById("fremdrift-fase");
  const tid  = document.getElementById("fremdrift-tid");

  // Oppdater timer hvert sekund
  if (_timerIntervall) clearInterval(_timerIntervall);
  _timerIntervall = setInterval(() => {
    const elapsed = (Date.now() - startMs) / 1000;
    const current = tid.dataset.estimert ? parseFloat(tid.dataset.estimert) : null;
    if (current) {
      const gjenstår = Math.max(0, current - elapsed);
      tid.textContent = `${_formaterTid(elapsed)} · ca. ${_formaterTid(gjenstår)} igjen`;
    } else {
      tid.textContent = _formaterTid(elapsed);
    }
  }, 1000);

  const FASE_TEKST = { konverterer: "Forbereder lyd", transkriberer: "Transkriberer", diariserer: "Identifiserer talere" };

  const intervall = setInterval(async () => {
    try {
      const res  = await fetch("/status/" + jobbId);
      const data = await res.json();

      if (data.status === "ferdig") {
        clearInterval(intervall);
        clearInterval(_timerIntervall);
        hentResultat(jobbId);
        return;
      } else if (data.status === "feil") {
        clearInterval(intervall);
        clearInterval(_timerIntervall);
        document.getElementById("fremdrift").classList.remove("synlig");
        viseFeil("Transkribering feilet på server.");
        return;
      }

      // Oppdater fase-tekst
      if (data.fase) fase.textContent = FASE_TEKST[data.fase] || data.fase;

      // Oppdater progress-bar
      if (data.fremdrift != null) {
        bar.classList.remove("ubestemt");
        bar.style.width = (data.fremdrift * 100).toFixed(1) + "%";
      }

      // Lagre estimert total for timeren
      if (data.estimert_total_s) tid.dataset.estimert = data.estimert_total_s;

    } catch { /* ignorer nettverksfeil under polling */ }
  }, 2000);
}

async function hentResultat(jobbId) {
  const res = await fetch("/resultat/" + jobbId);
  const data = await res.json();
  document.getElementById("fremdrift").classList.remove("synlig");
  // Nullstill progress-elementer for neste kjøring
  const bar = document.getElementById("fremdrift-bar");
  bar.classList.add("ubestemt");
  bar.style.width = "0%";
  document.getElementById("fremdrift-fase").textContent = "";
  document.getElementById("fremdrift-tid").textContent = "";
  delete document.getElementById("fremdrift-tid").dataset.estimert;
  visResultat(data);
}

// ---- Vis resultat (batch) ----
let _sistResultat = null;  // cache for navnebytte

const TALER_FARGER = ["#3b82f6", "#22c55e", "#f97316", "#a855f7"];
const TALER_STANDARDNAVN = ["Nav-veileder", "Bruker", "Tolk/Observatør", "Taler 4"];

function talerNavn(id) {
  const match = id && id.match(/SPEAKER_(\d+)/);
  if (match) {
    const n = parseInt(match[1]);
    // Prøv batch-prefix først, deretter legacy (sanntid bruker sanntid-prefix)
    const el = document.getElementById(`batch-taler-${n}-navn`)
            || document.getElementById(`sanntid-taler-${n}-navn`)
            || document.getElementById(`taler-${n}-navn`);
    return el ? (el.value || TALER_STANDARDNAVN[n] || `Taler ${n+1}`) : (TALER_STANDARDNAVN[n] || `Taler ${n+1}`);
  }
  return id;
}

function talerKlasse(id) {
  const match = id && id.match(/SPEAKER_(\d+)/);
  if (match) {
    const n = parseInt(match[1]);
    return n < 4 ? `taler-${n}` : "taler-ukjent";
  }
  return "taler-ukjent";
}

function settTalerRolle(n, rolle, radId) {
  const input = document.getElementById(`${radId}-taler-${n}-navn`);
  if (!input) return;
  input.value = rolle;
  oppdaterTalerNavn();
  // Oppdater aktiv chip
  const felt = input.closest(".taler-navn-felt");
  if (felt) felt.querySelectorAll(".rolle-chip").forEach(c =>
    c.classList.toggle("aktiv", c.dataset.rolle === rolle));
}

function _byggRolleChips(n, radId) {
  return ["Veileder", "Bruker", "Tolk"].map(rolle =>
    `<button class="rolle-chip${rolle === _chipAktivRolle(n, radId) ? ' aktiv' : ''}"
       data-rolle="${rolle}"
       onclick="settTalerRolle(${n}, '${rolle}', '${radId}')">${rolle}</button>`
  ).join("");
}

function _chipAktivRolle(n, radId) {
  const el = document.getElementById(`${radId}-taler-${n}-navn`);
  return el ? el.value : "";
}

function byggTalerNavnefelter(segmenter) {
  const unike = [...new Set((segmenter || []).map(s => s.taler).filter(Boolean))].sort();
  _byggTalerRad(unike, "taler-navn-rad", "batch");
}

function _byggTalerRad(unike, radElId, radId) {
  const rad = document.getElementById(radElId);
  if (!rad) return;
  rad.innerHTML = "";
  unike.forEach(id => {
    const match = id.match(/SPEAKER_(\d+)/);
    if (!match) return;
    const n = parseInt(match[1]);
    const farge = TALER_FARGER[n] || "#999";
    const felt = document.createElement("div");
    felt.className = "taler-navn-felt";
    felt.innerHTML = `
      <span class="taler-navn-farge" style="background:${farge}"></span>
      <label>Taler ${n+1}:</label>
      <input type="text" id="${radId}-taler-${n}-navn"
             value="${TALER_STANDARDNAVN[n] || `Taler ${n+1}`}"
             placeholder="Veileder, Bruker …"
             oninput="oppdaterTalerNavn()" />
      <div class="rolle-chips">${_byggRolleChips(n, radId)}</div>
    `;
    rad.appendChild(felt);
  });
}

function visResultat(data) {
  _sistResultat = data;

  const advarsel = document.getElementById("resultat-advarsel");
  if (data.advarsler?.length) {
    advarsel.style.display = "block";
    advarsel.textContent = data.advarsler.join("\n");
  } else {
    advarsel.style.display = "none";
    advarsel.textContent = "";
  }

  // Bygg navnefelter dynamisk basert på talere i resultatet
  const harTaler = (data.segmenter || []).some(s => s.taler);
  if (harTaler) {
    byggTalerNavnefelter(data.segmenter);
  }

  visResultatMeta(data);

  // Bygg dialogvisning
  byggDialog(data.segmenter || [], data.tekst);

  // Populer segment-tabell (skjult som standard)
  const tbody = document.querySelector("#segment-tabell tbody");
  tbody.innerHTML = "";
  const fragment = document.createDocumentFragment();
  (data.segmenter || []).forEach(s => {
    const rad = document.createElement("tr");
    const startCell = rad.insertCell();
    const sluttCell = rad.insertCell();
    const talerCell = rad.insertCell();
    const tekstCell = rad.insertCell();
    startCell.textContent = formaterTid(s.start);
    sluttCell.textContent = formaterTid(s.slutt);
    if (s.taler) {
      talerCell.textContent = talerNavn(s.taler);
      talerCell.className = talerKlasse(s.taler);
      talerCell.style.fontWeight = "600";
    }
    tekstCell.textContent = s.tekst;
    fragment.appendChild(rad);
  });
  tbody.appendChild(fragment);

  // Oppdater rå tekst (for kopiering)
  document.getElementById("resultat-tekst").textContent = data.tekst;

  visSeksjon("resultat");
}

function visResultatMeta(data) {
  const meta = document.getElementById("resultat-meta");
  const segmenter = data.segmenter || [];
  const talere = new Set(segmenter.map(s => s.taler).filter(Boolean));
  const ord = (data.tekst || "").trim().split(/\s+/).filter(Boolean).length;
  const sisteSlutt = segmenter.reduce((maks, s) => Math.max(maks, Number(s.slutt) || 0), 0);

  const deler = [
    `${ord} ord`,
    `${segmenter.length} segmenter`,
  ];
  if (talere.size) deler.push(`${talere.size} talere`);
  if (sisteSlutt) deler.push(`ca. ${formaterTid(sisteSlutt)}`);

  meta.innerHTML = "";
  const fragment = document.createDocumentFragment();
  for (const del of deler) {
    const span = document.createElement("span");
    span.textContent = del;
    fragment.appendChild(span);
  }
  meta.appendChild(fragment);
}

function byggDialog(segmenter, fallbackTekst) {
  const container = document.getElementById("dialog-container");
  container.innerHTML = "";

  if (!segmenter.length) {
    container.textContent = fallbackTekst || "";
    return;
  }

  // Slå sammen påfølgende segmenter fra samme taler
  const linjer = [];
  for (const seg of segmenter) {
    const taler = seg.taler || "SPEAKER_00";
    if (linjer.length && linjer[linjer.length - 1].taler === taler) {
      linjer[linjer.length - 1].tekst += " " + seg.tekst;
      linjer[linjer.length - 1].slutt = seg.slutt;
    } else {
      linjer.push({ taler, tekst: seg.tekst, start: seg.start, slutt: seg.slutt });
    }
  }

  const fragment = document.createDocumentFragment();
  for (const linje of linjer) {
    const div = document.createElement("div");
    div.className = "dialog-linje";
    div.dataset.taler = linje.taler;

    const brikke = document.createElement("div");
    brikke.className = `dialog-taler-brikke ${talerKlasse(linje.taler)}`;
    brikke.textContent = talerNavn(linje.taler);

    const høyre = document.createElement("div");
    const tekst = document.createElement("div");
    tekst.className = "dialog-tekst";
    tekst.textContent = linje.tekst;
    const tid = document.createElement("div");
    tid.className = "dialog-tid";
    tid.textContent = formaterTid(linje.start);
    høyre.appendChild(tekst);
    høyre.appendChild(tid);

    div.appendChild(brikke);
    div.appendChild(høyre);
    fragment.appendChild(div);
  }
  container.appendChild(fragment);
}

function oppdaterTalerNavn() {
  // Oppdater brikker i dialogvisning uten å gjenoppbygge
  document.querySelectorAll(".dialog-linje").forEach(div => {
    const brikke = div.querySelector(".dialog-taler-brikke");
    if (brikke) brikke.textContent = talerNavn(div.dataset.taler);
  });
  // Oppdater segment-tabell
  const tbody = document.querySelector("#segment-tabell tbody");
  Array.from(tbody.rows).forEach((rad, i) => {
    const seg = (_sistResultat?.segmenter || [])[i];
    if (seg?.taler) rad.cells[2].textContent = talerNavn(seg.taler);
  });
}

function kopierDialog() {
  const container = document.getElementById("dialog-container");
  const linjer = [];
  container.querySelectorAll(".dialog-linje").forEach(div => {
    const navn = talerNavn(div.dataset.taler);
    const tekst = div.querySelector(".dialog-tekst")?.textContent || "";
    const tid = div.querySelector(".dialog-tid")?.textContent || "";
    linjer.push(`[${tid}] ${navn}: ${tekst}`);
  });
  // Sjekk sanntid om batch-container er tom
  if (!linjer.length) {
    navigator.clipboard.writeText(kopierSanntidTekstRaw());
    return;
  }
  navigator.clipboard.writeText(linjer.join("\n"));
}

function formaterTid(sek) {
  const m = Math.floor(sek / 60), s = Math.floor(sek % 60);
  return m + ":" + String(s).padStart(2, "0");
}

// ---- Hjelpefunksjoner ----
function toggleSegmenter() {
  const w = document.getElementById("segment-wrapper");
  const btn = document.querySelector(".detaljer-toggle");
  const synlig = w.style.display !== "none";
  w.style.display = synlig ? "none" : "block";
  btn.textContent = synlig ? "Vis tidsstempler" : "Skjul tidsstempler";
}

function nyttOpptak() {
  document.getElementById("fil-input").value = "";
  visSeksjon("opptak");
}

function settFremdriftTekst(tekst) {
  document.getElementById("fremdrift-tekst").textContent = tekst;
  document.getElementById("fremdrift-fase").textContent = "";
  document.getElementById("fremdrift-tid").textContent = "";
}

function viseFeil(melding) {
  const boks = document.getElementById("feil-boks");
  boks.textContent = "⚠️ " + melding;
  boks.style.display = "block";
}

// ---- Fanebytte ----
function byttFane(fane) {
  document.getElementById("panel-batch").style.display   = fane === "batch"   ? "block" : "none";
  document.getElementById("panel-sanntid").style.display = fane === "sanntid" ? "block" : "none";
  document.getElementById("fane-batch").classList.toggle("aktiv",   fane === "batch");
  document.getElementById("fane-sanntid").classList.toggle("aktiv", fane === "sanntid");
}

// ============================================================
// SANNTIDSMODUS – AudioWorklet + PCM-streaming + server-side VAD
// ============================================================
let sanntidWs        = null;
let sanntidStream    = null;
let sanntidAktivt    = false;
let sanntidTimer     = null;
let sanntidSekunder  = 0;
let audioCtx         = null;
let workletNode      = null;
let pcmBuffer        = [];
const PCM_SEND_SAMPLES = 2560;

// ---- Valgfritt lydopptak for nedlasting ----
let sanntidOpptaker      = null;
let sanntidOpptakChunks  = [];

// ---- Live rullerende referat ----
let _liveReferatAktivt       = true;   // auto-oppdatering på/av
let _liveReferatOppdaterer   = false;  // LLM-kall pågår
let _liveReferatOrdTalt      = 0;      // ord i transcript ved sist trigger
let _liveReferatTekst        = "";     // siste ferdigstilte utkast
let _liveReferatReader       = null;   // aktiv fetch-reader
const _LIVE_REFERAT_TERSKEL  = 200;    // ord før første oppdatering
const _LIVE_REFERAT_DELTA    = 150;    // min nye ord mellom oppdateringer

function _telSanntidOrd() {
  const boks = document.getElementById("sanntid-tekst");
  if (!boks) return 0;
  return boks.innerText.trim().split(/\s+/).filter(Boolean).length;
}

function _hentSanntidTranskripsjonTekst() {
  const boks = document.getElementById("sanntid-tekst");
  if (!boks) return "";
  const linjer = [];
  boks.querySelectorAll(".dialog-linje").forEach(div => {
    const navn = talerNavn(div.dataset.taler || "");
    const tekst = div.querySelector(".dialog-tekst")?.textContent?.trim() || "";
    if (tekst) linjer.push(`${navn}: ${tekst}`);
  });
  return linjer.join("\n");
}

function toggleLiveReferat() {
  const body = document.getElementById("live-referat-body");
  const chevron = document.getElementById("live-referat-chevron");
  const synlig = body.style.display !== "none";
  body.style.display = synlig ? "none" : "block";
  chevron.textContent = synlig ? "▼" : "▲";
}

function toggleLiveReferatAuto() {
  _liveReferatAktivt = !_liveReferatAktivt;
  const knapp = document.getElementById("live-referat-auto-knapp");
  knapp.textContent = _liveReferatAktivt ? "⏸ Pause auto" : "▶ Start auto";
}

function kopierLiveReferat() {
  if (_liveReferatTekst) navigator.clipboard.writeText(_liveReferatTekst);
}

function oppdaterLiveReferatNaa() {
  const transkripsjon = _hentSanntidTranskripsjonTekst();
  if (!transkripsjon.trim()) return;
  _triggerLiveReferat(transkripsjon);
}

function _settLiveReferatStatus(tekst, oppdaterer) {
  document.getElementById("live-referat-status").textContent = tekst;
  const puls = document.getElementById("live-referat-puls");
  puls.style.display = oppdaterer ? "block" : "none";
}

async function _triggerLiveReferat(transkripsjon) {
  if (_liveReferatOppdaterer) return; // allerede pågår
  _liveReferatOppdaterer = true;
  _liveReferatOrdTalt = _telSanntidOrd();

  const wrap = document.getElementById("live-referat-wrap");
  const body = document.getElementById("live-referat-body");
  const tekstEl = document.getElementById("live-referat-tekst");
  wrap.style.display = "block";
  body.style.display = "block";
  document.getElementById("live-referat-chevron").textContent = "▲";
  _settLiveReferatStatus("oppdaterer …", true);

  let buffer = "";
  tekstEl.innerHTML = '<span class="live-referat-tom">Genererer …</span>';

  try {
    const res = await fetch("/referat/rullerende/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transkripsjon })
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const reader = res.body.getReader();
    _liveReferatReader = reader;
    const dec = new TextDecoder();
    let rest = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = rest + dec.decode(value, { stream: true });
      const linjer = chunk.split("\n");
      rest = linjer.pop();
      for (const linje of linjer) {
        if (!linje.startsWith("data: ")) continue;
        try {
          const evt = JSON.parse(linje.slice(6));
          if (evt.type === "token") {
            buffer += evt.tekst;
            tekstEl.textContent = buffer;
            tekstEl.scrollTop = tekstEl.scrollHeight;
          } else if (evt.type === "ferdig") {
            buffer = evt.tekst;
            _liveReferatTekst = evt.tekst;
            tekstEl.textContent = buffer;
            _settLiveReferatStatus(
              "sist oppdatert " + new Date().toLocaleTimeString("nb-NO", {hour:"2-digit",minute:"2-digit"}),
              false
            );
            document.getElementById("live-referat-tid").textContent =
              "Modell: " + (evt.modell || "");
          } else if (evt.type === "feil") {
            tekstEl.textContent = "⚠️ " + evt.melding;
            _settLiveReferatStatus("feil", false);
          }
        } catch {}
      }
    }
  } catch (e) {
    if (!e.message.includes("abort")) {
      tekstEl.textContent = "⚠️ Kunne ikke oppdatere: " + e.message;
      _settLiveReferatStatus("feil", false);
    }
  } finally {
    _liveReferatOppdaterer = false;
    _liveReferatReader = null;
  }
}

function _sjekkLiveReferatTerskel() {
  if (!sanntidAktivt || !_liveReferatAktivt) return;
  const ord = _telSanntidOrd();
  const forsteGang = _liveReferatOrdTalt === 0 && ord >= _LIVE_REFERAT_TERSKEL;
  const oppdatering = _liveReferatOrdTalt > 0 && (ord - _liveReferatOrdTalt) >= _LIVE_REFERAT_DELTA;
  if ((forsteGang || oppdatering) && !_liveReferatOppdaterer) {
    _triggerLiveReferat(_hentSanntidTranskripsjonTekst());
  }
}

async function startSanntid() {
  try {
    sanntidStream = await navigator.mediaDevices.getUserMedia({ audio: true, sampleRate: 16000 });
  } catch (err) {
    document.getElementById("feil-boks").style.display = "block";
    document.getElementById("feil-boks").textContent = "⚠️ Mikrofontilgang nektet: " + err.message;
    return;
  }

  sanntidAktivt    = true;
  sanntidSegmenter = [];
  sanntidSekunder  = 0;
  pcmBuffer        = [];
  sanntidOpptakChunks = [];

  // Fjern eventuell tidligere nedlastingslenke
  const gammelLenke = document.getElementById("sanntid-nedlasting");
  if (gammelLenke) gammelLenke.remove();

  document.getElementById("sanntid-start-knapp").disabled = true;
  document.getElementById("sanntid-stopp-knapp").disabled = false;
  document.getElementById("sanntid-timer").style.display  = "inline";
  document.getElementById("sanntid-status").style.display = "flex";
  document.getElementById("modul-transkripsjon").style.display = "block";
  document.getElementById("sanntid-tekst").style.display  = "block";
  document.getElementById("sanntid-knapper").style.display = "flex";
  document.getElementById("sanntid-footer").style.display  = "block";
  const opptakHake = document.getElementById("sanntid-lagre-opptak");
  if (opptakHake) opptakHake.disabled = true;
  settSanntidStatus("Kobler til …");

  sanntidTimer = setInterval(() => {
    sanntidSekunder++;
    const m = Math.floor(sanntidSekunder / 60);
    const s = String(sanntidSekunder % 60).padStart(2, "0");
    document.getElementById("sanntid-timer-tekst").textContent = m + ":" + s;
  }, 1000);

  // WebSocket til server
  const wsUrl = `ws://${location.host}/ws/sanntid`;
  sanntidWs = new WebSocket(wsUrl);
  sanntidWs.binaryType = "arraybuffer";

  sanntidWs.onopen = async () => {
    settSanntidStatus("Tar opp …");
    await startAudioWorklet();

    // Start parallelt lydopptak for nedlasting hvis valgt
    if (document.getElementById("sanntid-lagre-opptak")?.checked) {
      sanntidOpptakChunks = [];
      sanntidOpptaker = new MediaRecorder(sanntidStream);
      sanntidOpptaker.ondataavailable = e => { if (e.data.size > 0) sanntidOpptakChunks.push(e.data); };
      sanntidOpptaker.onstop = _tilbyNedlasting;
      sanntidOpptaker.start(1000);
    }
  };

  sanntidWs.onmessage = (evt) => {
    const data = JSON.parse(evt.data);
    if (data.type === "feil") {
      settSanntidStatus("Feil: " + data.melding);
      stoppSanntid();
      return;
    }
    if (data.type === "segment" && data.tekst) {
      // Legg til segmenter fra dette chunk-et i dialogvisningen
      const boks = document.getElementById("sanntid-tekst");
      for (const seg of (data.segmenter || [{ tekst: data.tekst, taler: "SPEAKER_00" }])) {
        if (!seg.tekst) continue;
        const taler = seg.taler || "SPEAKER_00";

        // Prøv å slå sammen med siste linje hvis samme taler
        const siste = boks.lastElementChild;
        if (siste && siste.dataset.taler === taler) {
          const tekst = siste.querySelector(".dialog-tekst");
          if (tekst) tekst.textContent += " " + seg.tekst;
        } else {
          const div = document.createElement("div");
          div.className = "dialog-linje";
          div.dataset.taler = taler;

          const brikke = document.createElement("div");
          brikke.className = `dialog-taler-brikke ${talerKlasse(taler)}`;
          brikke.textContent = talerNavn(taler);

          const høyre = document.createElement("div");
          const tekst = document.createElement("div");
          tekst.className = "dialog-tekst";
          tekst.textContent = seg.tekst;
          høyre.appendChild(tekst);
          div.appendChild(brikke);
          div.appendChild(høyre);
          boks.appendChild(div);

          // Oppdater sanntid-rollerad med nye talere
          const unike = [...new Set(
            [...boks.querySelectorAll(".dialog-linje")].map(d => d.dataset.taler)
          )].sort();
          _byggTalerRad(unike, "sanntid-taler-rad", "sanntid");
        }
        boks.scrollTop = boks.scrollHeight;
      }
      settSanntidStatus("Tar opp …");
      _sjekkLiveReferatTerskel();
    }
  };

  sanntidWs.onerror = () => settSanntidStatus("WebSocket-feil", false);
  sanntidWs.onclose = () => {
    sanntidWs = null;
    if (!sanntidAktivt) {
      settSanntidStatus("Opptak avsluttet", false);
    } else {
      settSanntidStatus("Tilkobling avbrutt", false);
    }
  };
}

async function startAudioWorklet() {
  // AudioContext – be om 16 kHz (støttes ikke alltid, men AudioWorklet resample
  // eller vi sender til server som resampler)
  audioCtx = new AudioContext({ sampleRate: 16000 });

  await audioCtx.audioWorklet.addModule("/static/audio-processor.js");

  const kilde = audioCtx.createMediaStreamSource(sanntidStream);
  workletNode = new AudioWorkletNode(audioCtx, "audio-processor");

  workletNode.port.onmessage = (evt) => {
    if (!sanntidAktivt) return;
    const samples = evt.data;  // Float32Array
    for (let i = 0; i < samples.length; i++) pcmBuffer.push(samples[i]);

    // Send til server i batcher
    while (pcmBuffer.length >= PCM_SEND_SAMPLES) {
      const chunk = new Float32Array(pcmBuffer.splice(0, PCM_SEND_SAMPLES));
      if (sanntidWs && sanntidWs.readyState === WebSocket.OPEN) {
        sanntidWs.send(chunk.buffer);
      }
    }
  };

  kilde.connect(workletNode);
  workletNode.connect(audioCtx.destination);
}

function stoppSanntid() {
  sanntidAktivt = false;
  clearInterval(sanntidTimer);

  // Send eventuell rest-buffer
  if (pcmBuffer.length > 0 && sanntidWs && sanntidWs.readyState === WebSocket.OPEN) {
    const rest = new Float32Array(pcmBuffer.splice(0));
    sanntidWs.send(rest.buffer);
  }

  // Rydd opp AudioWorklet
  if (workletNode) { workletNode.disconnect(); workletNode = null; }
  if (audioCtx)    { audioCtx.close(); audioCtx = null; }

  // Stopp lydopptak før stream — onstop trenger aktiv stream for siste data
  if (sanntidOpptaker && sanntidOpptaker.state !== "inactive") {
    sanntidOpptaker.stop();
    sanntidOpptaker = null;
  }

  if (sanntidStream) { sanntidStream.getTracks().forEach(t => t.stop()); sanntidStream = null; }

  // Be server om å flush VAD-buffer – behold WS åpen til server lukker
  if (sanntidWs && sanntidWs.readyState === WebSocket.OPEN) {
    sanntidWs.send(JSON.stringify({ type: "stopp" }));
    // Ikke null-still sanntidWs her – server kan sende siste segment(er) før den lukker
  }

  document.getElementById("sanntid-start-knapp").disabled = false;
  document.getElementById("sanntid-stopp-knapp").disabled = true;
  document.getElementById("sanntid-timer").style.display  = "none";
  const opptakHake = document.getElementById("sanntid-lagre-opptak");
  if (opptakHake) opptakHake.disabled = false;
  settSanntidStatus("Avslutter …");
}

function _tilbyNedlasting() {
  if (!sanntidOpptakChunks.length) return;
  const blob = new Blob(sanntidOpptakChunks, { type: "audio/webm" });
  const url  = URL.createObjectURL(blob);
  const dato = new Date().toISOString().slice(0, 16).replace("T", "_").replace(":", "-");
  const div  = document.createElement("div");
  div.id = "sanntid-nedlasting";
  div.style.cssText = "margin-top:.75rem; padding:.6rem .9rem; background:#f0f8ff; border:1px solid #b3d7f5; border-radius:6px; font-size:.875rem; display:flex; align-items:center; gap:.75rem;";
  div.innerHTML = `
    <span>🎙️ Opptak klart</span>
    <a href="${url}" download="opptak_${dato}.webm"
       style="color:#0067c5; font-weight:600; text-decoration:underline">
      Last ned opptak
    </a>
    <span style="color:#888; font-size:.8rem">(slettes når du lukker siden)</span>
  `;
  document.getElementById("sanntid-knapper").after(div);
}

function settSanntidStatus(tekst, aktiv = true) {
  document.getElementById("sanntid-status-tekst").textContent = tekst;
  const puls = document.getElementById("sanntid-puls");
  if (aktiv) {
    puls.style.animation = "";
    puls.style.background = "#0067c5";
    puls.style.opacity = "1";
  } else {
    puls.style.animation = "none";
    puls.style.background = "#888";
    puls.style.opacity = "0.5";
  }
}

function kopierSanntidTekstRaw() {
  const boks = document.getElementById("sanntid-tekst");
  const linjer = [];
  boks.querySelectorAll(".dialog-linje").forEach(div => {
    const navn = talerNavn(div.dataset.taler);
    const tekst = div.querySelector(".dialog-tekst")?.textContent || "";
    linjer.push(`${navn}: ${tekst}`);
  });
  return linjer.join("\n");
}

function kopierSanntidTekst() {
  navigator.clipboard.writeText(kopierSanntidTekstRaw());
}

function nullstillSanntid() {
  document.getElementById("sanntid-tekst").innerHTML = "";
  document.getElementById("sanntid-taler-rad").innerHTML = "";
  // Skjul referat-panelet hvis det vises fra sanntid
  document.getElementById("referat-panel").style.display = "none";
  // Avbryt evt. pågående live-referat og nullstill tilstand
  if (_liveReferatReader) { try { _liveReferatReader.cancel(); } catch {} }
  _liveReferatOppdaterer = false;
  _liveReferatOrdTalt    = 0;
  _liveReferatTekst      = "";
  _liveReferatAktivt     = true;
  document.getElementById("live-referat-wrap").style.display = "none";
  document.getElementById("live-referat-body").style.display = "none";
  document.getElementById("live-referat-tekst").innerHTML =
    '<span class="live-referat-tom">Utkast vises her etter ca. 2–3 minutters tale …</span>';
  document.getElementById("live-referat-status").textContent = "";
  document.getElementById("live-referat-puls").style.display = "none";
  document.getElementById("live-referat-auto-knapp").textContent = "⏸ Pause auto";
}

// ============================================================
// MØTEREFERAT OG SAMMENDRAG – Ollama-integrasjon
// ============================================================

function _hentTranskripsjonTekst(kilde) {
  if (kilde === "batch") {
    // Fra batch-resultat: bruk formatert dialog (taler: tekst)
    const container = document.getElementById("dialog-container");
    const linjer = [];
    container.querySelectorAll(".dialog-linje").forEach(div => {
      const navn = talerNavn(div.dataset.taler);
      const tekst = div.querySelector(".dialog-tekst")?.textContent || "";
      if (tekst.trim()) linjer.push(`${navn}: ${tekst.trim()}`);
    });
    return linjer.join("\n");
  } else {
    // Fra sanntid: samme som kopierSanntidTekstRaw
    return kopierSanntidTekstRaw();
  }
}

function _visReferatPanel(tittel) {
  const panel = document.getElementById("referat-panel");
  panel.style.display = "block";
  document.getElementById("referat-panel-tittel").textContent = tittel;
  document.getElementById("referat-laster").style.display = "flex";
  document.getElementById("referat-laster-tekst").textContent = "Kobler til …";
  document.getElementById("referat-progress-wrap").style.display = "none";
  document.getElementById("referat-progress-bar").style.width = "0%";
  document.getElementById("referat-tekst").style.display = "none";
  document.getElementById("referat-tekst").textContent = "";
  document.getElementById("referat-footer").style.display = "none";
  setTimeout(() => panel.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
}

let _referatProgressTimer = null;

function _startReferatProgress(estimertSek, modell) {
  document.getElementById("referat-progress-wrap").style.display = "block";
  const bar = document.getElementById("referat-progress-bar");
  const lasterTekst = document.getElementById("referat-laster-tekst");
  const start = Date.now();
  if (_referatProgressTimer) clearInterval(_referatProgressTimer);
  _referatProgressTimer = setInterval(() => {
    const elapsed = (Date.now() - start) / 1000;
    const pct = Math.min((elapsed / estimertSek) * 100, 95);
    bar.style.width = pct + "%";
    const gjenst = Math.max(0, Math.round(estimertSek - elapsed));
    lasterTekst.textContent = gjenst > 0
      ? `Genererer … (~${gjenst} sek igjen)`
      : "Fullfører …";
  }, 500);
}

function _stopReferatProgress() {
  if (_referatProgressTimer) { clearInterval(_referatProgressTimer); _referatProgressTimer = null; }
  document.getElementById("referat-progress-bar").style.width = "100%";
  setTimeout(() => { document.getElementById("referat-progress-wrap").style.display = "none"; }, 400);
}

function _visReferatStream(tekst) {
  const boks = document.getElementById("referat-tekst");
  boks.textContent = tekst;   // rå tekst under generering
  boks.style.display = "block";
}

function _visReferatResultat(tekst, modell) {
  _stopReferatProgress();
  document.getElementById("referat-laster").style.display = "none";
  const boks = document.getElementById("referat-tekst");
  boks.innerHTML = tekst
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/^(#{1,3}) (.+)$/gm, (_, h, t) => `<strong>${t}</strong>`)
    .replace(/\n/g, "<br>");
  boks.style.display = "block";
  const footer = document.getElementById("referat-footer");
  footer.style.display = "flex";
  document.getElementById("referat-modell-info").textContent = `Modell: ${modell}`;
}

function _visReferatFeil(melding) {
  _stopReferatProgress();
  document.getElementById("referat-laster").style.display = "none";
  const boks = document.getElementById("referat-tekst");
  boks.innerHTML = `<span style="color:#ba3a26">⚠️ ${melding}</span>`;
  boks.style.display = "block";
}

async function _streamReferat(endepunkt, tekst, tittel) {
  const res = await fetch(endepunkt, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transkripsjon: tekst }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    _visReferatFeil(err.detail || "Ukjent feil");
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let samletTekst = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const linjer = buffer.split("\n");
    buffer = linjer.pop();
    for (const linje of linjer) {
      if (!linje.startsWith("data: ")) continue;
      let data;
      try { data = JSON.parse(linje.slice(6)); } catch { continue; }
      if (data.type === "start") {
        _startReferatProgress(data.estimert_sek, data.modell);
      } else if (data.type === "token") {
        samletTekst += data.tekst;
        _visReferatStream(samletTekst);
      } else if (data.type === "ferdig") {
        _visReferatResultat(data.tekst, data.modell);
      } else if (data.type === "feil") {
        _visReferatFeil(data.melding);
      }
    }
  }
}

async function hentSammendrag() {
  const tekst = _hentTranskripsjonTekst("sanntid");
  if (!tekst.trim()) { alert("Ingen transkripsjon å oppsummere ennå."); return; }
  _visReferatPanel("📋 Løpende sammendrag");
  document.getElementById("referat-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    await _streamReferat("/sammendrag/stream", tekst, "📋 Løpende sammendrag");
  } catch (err) {
    _visReferatFeil("Nettverksfeil: " + err.message);
  }
}

async function hentReferat(kilde) {
  const tekst = _hentTranskripsjonTekst(kilde);
  if (!tekst.trim()) { alert("Ingen transkripsjon å generere referat fra."); return; }
  _visReferatPanel("📝 Møtereferat");
  document.getElementById("referat-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    await _streamReferat("/referat/stream", tekst, "📝 Møtereferat");
  } catch (err) {
    _visReferatFeil("Nettverksfeil: " + err.message);
  }
}

function kopierReferat() {
  const boks = document.getElementById("referat-tekst");
  // Hent ren tekst (uten HTML-tagger)
  navigator.clipboard.writeText(boks.innerText || boks.textContent);
}

// ---- Modell-statussjekk ved oppstart ----

async function sjekkModellStatus() {
  try {
    const res = await fetch("/modell/status");
    const data = await res.json();
    const banner = document.getElementById("modell-banner");
    if (data.tilgjengelig === false) {
      document.getElementById("modell-banner-tittel").textContent =
        `⚠️ LLM-modellen «${data.modell}» er ikke installert`;
      document.getElementById("modell-banner-tekst").textContent =
        "Sammendrag og møtereferat vil ikke fungere. Du kan laste ned modellen nå (krever ca. 20 GB diskplass og god internettforbindelse).";
      const knapp = document.getElementById("modell-last-ned-knapp");
      knapp.dataset.modell = data.modell;
      banner.style.display = "block";
    } else if (data.tilgjengelig === true) {
      banner.style.display = "none";
    }
    // tilgjengelig === null = Ollama ikke tilgjengelig, vis ingenting
  } catch { /* ignorer nettverksfeil */ }
}

async function lastNedModell() {
  const knapp = document.getElementById("modell-last-ned-knapp");
  const spinner = document.getElementById("modell-banner-spinner");
  const logg = document.getElementById("modell-nedlasting-logg");
  const banner = document.getElementById("modell-banner");

  knapp.disabled = true;
  spinner.style.display = "inline";
  logg.style.display = "block";
  logg.textContent = "";

  try {
    const res = await fetch("/modell/last-ned", { method: "POST" });
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const linjer = buf.split("\n");
      buf = linjer.pop();
      for (const l of linjer) {
        if (!l.startsWith("data:")) continue;
        try {
          const d = JSON.parse(l.slice(5).trim());
          if (d.linje) {
            logg.textContent += d.linje + "\n";
            logg.scrollTop = logg.scrollHeight;
          } else if (d.ferdig) {
            if (d.suksess) {
              banner.className = "";
              document.getElementById("modell-banner-tittel").textContent = "✅ Modellen er installert";
              document.getElementById("modell-banner-tekst").textContent =
                "Nedlasting fullført. Sammendrag og møtereferat er nå tilgjengelig.";
              knapp.style.display = "none";
              spinner.style.display = "none";
              setTimeout(() => { banner.style.display = "none"; }, 4000);
            } else {
              document.getElementById("modell-banner-tekst").textContent = "Nedlasting feilet. Prøv igjen.";
              knapp.disabled = false;
              spinner.style.display = "none";
            }
          } else if (d.feil) {
            document.getElementById("modell-banner-tekst").textContent = "Feil: " + d.feil;
            knapp.disabled = false;
            spinner.style.display = "none";
          }
        } catch { /* ignorer JSON-parsefeil */ }
      }
    }
  } catch (err) {
    document.getElementById("modell-banner-tekst").textContent = "Nettverksfeil: " + err.message;
    knapp.disabled = false;
    spinner.style.display = "none";
  }
}

// Sjekk modellstatus ved sidelast
sjekkModellStatus();

// ── Systemstatus (lastes ved sideoppstart) ───────────────────────────────────
async function lastSystemstatus() {
  const el = document.getElementById("systeminfo-footer");
  if (!el) return;
  try {
    const d = await fetch("/system/info").then(r => r.json());
    const par = (label, val) =>
      `<span class="sif-par"><b>${label}</b> ${val}</span>`;
    el.innerHTML = [
      par("ASR sanntid", kortNavn(d.asr.sanntid_modell)),
      par("ASR batch", kortNavn(d.asr.batch_modell)),
      par("Backend", d.asr.backend),
      par("Taler-ID", "ECAPA-TDNN"),
      par("Vindu", d.diarisering.vindu_s + "s"),
      par("Stillhet", d.vad.stillhet_s + "s"),
      par("LLM", d.llm.modell),
    ].join(" · ");
    el.style.display = "flex";
  } catch {
    /* silent – footer is optional */
  }
}

function kortNavn(sti) {
  return sti.split("/").pop();
}

lastSystemstatus();
