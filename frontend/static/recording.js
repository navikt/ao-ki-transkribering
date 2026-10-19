/* Sample-addressed audio archive and retryable uploads. No transcript in logs. */
class RecordingArchive {
  async open() {
    this.db = await new Promise((resolve, reject) => {
      const request = indexedDB.open("transkriberit-audio", 1);
      request.onupgradeneeded = () => request.result.createObjectStore("chunks", {keyPath: "key"});
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    await this.cleanup();
  }
  transaction(mode, action) {
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction("chunks", mode);
      const request = action(tx.objectStore("chunks"));
      tx.oncomplete = () => resolve(request?.result);
      tx.onerror = tx.onabort = () => reject(tx.error || new Error("Storage failed"));
    });
  }
  put(row) { return this.transaction("readwrite", store => store.put(row)); }
  get(key) { return this.transaction("readonly", store => store.get(key)); }
  all() { return this.transaction("readonly", store => store.getAll()); }
  async cleanup(session = null) {
    await this.transaction("readwrite", store => {
      const request = store.openCursor();
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor) return;
        if (cursor.value.expires <= Date.now() || cursor.value.session === session) cursor.delete();
        cursor.continue();
      };
    });
  }
}

class RecordingTransport {
  constructor({archive = new RecordingArchive(), request = (...args) => fetch(...args), retryMs = 3000, restored = []} = {}) {
    this.archive = archive;
    this.request = request;
    this.retryMs = retryMs;
    this.recordingId = restored[0]?.session || crypto.randomUUID();
    this.readyState = 1;
    this.rows = restored.map(({pcm, ...row}) => row).sort((a, b) => a.sequence - b.sequence);
    this.buffer = new Float32Array(0);
    this.start = 0;
    this.captured = this.rows.at(-1)?.end || 0;
    this.next = 0;
    this.stopped = restored.length > 0;
    this.disposed = false;
    this.writing = Promise.resolve();
    this.fallback = new Map();
    this.expires = restored[0]?.expires || Date.now() + 8 * 3600 * 1000;
    this.initialized = archive.open().then(() => {
      this.expiryTimer = setTimeout(() => this.expire(), Math.max(0, this.expires - Date.now()));
      this.onmessage?.({data: JSON.stringify({type: "session", recording_id: this.recordingId})});
      if (this.stopped) {
        while (this.rows[this.next]?.result) {
          this.onmessage?.({data: JSON.stringify(this.rows[this.next++].result)});
        }
        this.oncoverage?.(this.rows[this.next - 1]?.end || 0, this.captured);
        return this.pump();
      }
      return this.onopen?.();
    }).catch(() => this.onfatal?.("Lokal lydlagring er utilgjengelig. Opptaket er stoppet."));
  }
  send(value) {
    if (this.disposed) return;
    if (typeof value === "string") {
      if (JSON.parse(value).type === "stopp") this.stop();
      return;
    }
    if (this.stopped) return;
    const samples = new Float32Array(value);
    this.captured += samples.length;
    this.oncoverage?.(this.rows[this.next - 1]?.end || 0, this.captured);
    const joined = new Float32Array(this.buffer.length + samples.length);
    joined.set(this.buffer); joined.set(samples, this.buffer.length);
    this.buffer = joined;
    while (this.buffer.length >= 240000) {
      this.enqueue(this.buffer.slice(0, 240000));
      this.buffer = this.buffer.slice(224000);
      this.start += 224000;
    }
    if (this.captured * 4 >= 512 * 1024 * 1024) {
      this.onfatal?.("Grensen for lokal lydlagring er nadd. Last ned opptaket.");
    }
  }
  enqueue(pcm) {
    const sequence = this.rows.length;
    const row = {key: `${this.recordingId}:${sequence}`, session: this.recordingId,
      sequence, start: this.start, end: this.start + pcm.length,
      state: "captured", expires: this.expires};
    this.rows.push(row);
    this.writing = this.writing.then(async () => {
      try { await this.archive.put({...row, pcm}); }
      catch {
        this.fallback.set(row.key, pcm);
        this.onfatal?.("Lydlageret er fullt eller utilgjengelig. Last ned opptaket.");
      }
    });
    this.writing.then(() => this.pump());
  }
  async pump() {
    if (this.busy || this.disposed || this.retryTimer) return;
    this.busy = true;
    try {
      while (this.next < this.rows.length && !this.disposed) {
        await this.writing;
        const row = this.rows[this.next];
        const pcm = this.fallback.get(row.key) || (await this.archive.get(row.key)).pcm;
        this.controller = new AbortController();
        const timeout = setTimeout(() => this.controller.abort(), 90000);
        row.state = "sending";
        let response;
        try {
          response = await this.request(`/recordings/${this.recordingId}/chunks/${row.sequence}?start_sample=${row.start}`, {
            method: "POST", headers: {"Content-Type": "application/octet-stream"},
            body: pcm.buffer, signal: this.controller.signal,
          });
        } finally { clearTimeout(timeout); }
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const result = await response.json();
        if (result.state !== "processed" || result.sequence !== row.sequence ||
            result.start_sample !== row.start || result.end_sample !== row.end) throw new Error("Timeline mismatch");
        if (this.disposed) return;
        row.state = "processed";
        row.result = result;
        try { await this.archive.put({...row, pcm}); }
        catch { this.fallback.set(row.key, pcm); }
        this.next++;
        this.oncoverage?.(row.end, this.captured);
        this.onmessage?.({data: JSON.stringify(result)});
        this.onstate?.(this.next === this.rows.length ? "Tar opp …" : "Behandler ventende lyd …");
      }
      if (this.stopped && this.next === this.rows.length && !this.disposed) {
        this.readyState = 3;
        this.onclose?.({code: 1000, wasClean: true});
      }
    } catch {
      if (!this.disposed) {
        if (this.rows[this.next]) this.rows[this.next].state = "failed";
        this.onstate?.("Prover igjen – lyden er lagret lokalt");
        this.retryTimer = setTimeout(() => { this.retryTimer = null; this.pump(); }, this.retryMs);
      }
    } finally { this.busy = false; }
  }
  stop() {
    if (this.stopped) return;
    this.stopped = true;
    const previousEnd = this.rows.at(-1)?.end || 0;
    if (this.start + this.buffer.length > previousEnd) this.enqueue(this.buffer);
    this.buffer = new Float32Array(0);
    this.writing.then(() => this.pump());
  }
  async wav() {
    await this.writing;
    const parts = [];
    let end = 0;
    for (const row of this.rows) {
      const pcm = this.fallback.get(row.key) || (await this.archive.get(row.key)).pcm;
      const skip = Math.max(0, end - row.start);
      const bytes = new ArrayBuffer((pcm.length - skip) * 2);
      const view = new DataView(bytes);
      for (let i = skip; i < pcm.length; i++) view.setInt16((i - skip) * 2, Math.round(Math.max(-1, Math.min(1, pcm[i])) * 32767), true);
      parts.push(bytes);
      end = row.end;
    }
    const header = new ArrayBuffer(44), v = new DataView(header);
    const text = (offset, value) => [...value].forEach((c, i) => v.setUint8(offset + i, c.charCodeAt(0)));
    text(0, "RIFF"); v.setUint32(4, 36 + end * 2, true); text(8, "WAVEfmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, 16000, true); v.setUint32(28, 32000, true); v.setUint16(32, 2, true);
    v.setUint16(34, 16, true); text(36, "data"); v.setUint32(40, end * 2, true);
    return new Blob([header, ...parts], {type: "audio/wav"});
  }
  async expire() {
    this.onfatal?.("Lokal oppbevaringstid er utlopt. Opptaket slettes.");
    await this.dispose();
    this.onexpired?.();
  }
  async dispose() {
    this.disposed = true;
    clearTimeout(this.retryTimer); clearTimeout(this.expiryTimer);
    this.controller?.abort();
    await this.writing;
    await this.archive.cleanup(this.recordingId);
    this.archive.db?.close();
    this.fallback.clear(); this.buffer = new Float32Array(0); this.rows = [];
  }
}

if (typeof module !== "undefined") module.exports = {RecordingTransport, RecordingArchive};
