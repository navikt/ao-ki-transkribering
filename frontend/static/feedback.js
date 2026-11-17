/* Feedback remains in page memory until the user explicitly submits it. */
class SessionFeedback {
  constructor({root, request, transcript, audio = () => null, launcher = null}) {
    Object.assign(this, {root, request, transcript, audio, launcher});
    this.generations = new Map();
    this.pending = null;
    this.dirty = false;
    this.sending = false;
    this.config = {enabled: false, attachments: false, audio: false};
    root.querySelector('form').addEventListener('submit', event => {
      event.preventDefault();
      this.submit();
    });
    launcher?.addEventListener('click', () => {
      root.open = true;
      root.scrollIntoView({behavior: 'smooth', block: 'start'});
      root.querySelector('[data-rating]')?.focus({preventScroll: true});
    });
    root.querySelector('form').addEventListener('input', () => { this.dirty = true; });
    root.querySelectorAll('[data-rating]').forEach(button => button.addEventListener('click', () => {
      root.querySelector('[name="rating"]').value = button.dataset.rating;
      this.dirty = true;
      root.querySelectorAll('[data-rating]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    }));
    ['attach', 'audio', 'audio-file'].forEach(name => {
      root.querySelector(`[name="${name}"]`).addEventListener('change', () => this.preview());
    });
    root.querySelector('[name="target"]').addEventListener('change', () => {
      root.querySelector('[name="audio"]').checked = false;
      root.querySelector('[name="audio-file"]').value = '';
      this.preview();
    });
    root.querySelector('[data-feedback-new]').addEventListener('click', () => {
      this.pending = null;
      this.dirty = false;
      root.querySelector('form').reset();
      root.querySelectorAll('[data-rating]').forEach(item => item.setAttribute('aria-pressed', 'false'));
      root.querySelector('[type="submit"]').hidden = false;
      root.querySelector('[type="submit"]').textContent = 'Send tilbakemelding';
      root.querySelector('fieldset').disabled = false;
      root.querySelector('fieldset').hidden = false;
      root.querySelector('[data-feedback-new]').hidden = true;
      root.querySelector('[data-feedback-receipt]').hidden = true;
      root.querySelector('[data-feedback-case]').textContent = '';
      root.querySelector('[role="status"]').textContent = '';
      this.preview();
    });
    root.addEventListener('toggle', () => { if (root.open && !this.pending) this.preview(); });
  }
  async load() {
    try {
      const response = await this.request('/feedback/config', {cache: 'no-store'});
      if (!response.ok) return;
      this.config = await response.json();
      this.root.hidden = !this.config.enabled;
      if (this.launcher) this.launcher.hidden = !this.config.enabled;
      this.root.querySelector('[data-attachment-option]').hidden = !this.config.attachments;
      this.root.querySelector('[data-audio-option]').hidden = !this.config.audio;
    } catch { /* Feedback availability must not affect recording. */ }
  }
  capture(key, payload, result, audio = null) {
    this.generations.set(key, {transcript: payload.transkripsjon, summary: result.tekst, audio});
    const option = document.createElement('option');
    option.value = key;
    option.textContent = `${result.prompt?.title || 'Referat'} · ${new Date().toLocaleTimeString('nb-NO', {hour: '2-digit', minute: '2-digit'})}`;
    const targets = this.root.querySelector('[name="target"]');
    targets.appendChild(option);
    if (!this.dirty && !this.pending) targets.value = key;
    if (this.generations.size > 10) {
      const oldest = [...this.generations.keys()].find(id => id !== targets.value);
      this.generations.delete(oldest);
      Array.from(targets.options).find(item => item.value === oldest)?.remove();
    }
    if (!this.pending) this.preview();
  }
  selection() {
    const target = this.root.querySelector('[name="target"]').value;
    return {target, content: this.generations.get(target) || {transcript: this.transcript(target), audio: this.audio(target)}};
  }
  preview() {
    const attach = this.root.querySelector('[name="attach"]').checked;
    this.root.querySelector('[data-attachment-details]').hidden = !attach;
    const {content} = this.selection();
    this.root.querySelector('[data-feedback-includes]').textContent = content.summary
      ? 'Du deler transkripsjonen som ble brukt, og det opprinnelige referatet. Bearbeidet tekst deles ikke.'
      : 'Du deler den valgte transkripsjonen.';
    this.root.querySelector('[data-feedback-preview]').textContent = attach
      ? `Transkripsjon:\n${content.transcript || '(ingen tekst)'}${content.summary ? '\n\nReferat:\n' + content.summary : ''}` : '';
    const withAudio = attach && this.root.querySelector('[name="audio"]').checked;
    this.root.querySelector('[data-audio-details]').hidden = !withAudio;
    const file = this.root.querySelector('[name="audio-file"]').files?.[0];
    this.root.querySelector('[data-audio-label]').textContent = file
      ? `Valgt lydfil: ${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`
      : content.audio?.label || 'Ingen lokal lyd tilgjengelig. Velg lydfilen du vil dele.';
  }
  async submit() {
    if (this.sending) return;
    const status = this.root.querySelector('[role="status"]');
    const fieldset = this.root.querySelector('fieldset');
    const button = this.root.querySelector('[type="submit"]');
    if (!this.pending) {
      const rating = Number(this.root.querySelector('[name="rating"]').value);
      if (!Number.isInteger(rating) || rating < 1 || rating > 5) {
        status.textContent = 'Velg en vurdering fra 1 til 5.';
        this.root.querySelector('[data-rating]')?.focus();
        return;
      }
      const {target, content} = this.selection();
      const attach = this.config.attachments && this.root.querySelector('[name="attach"]').checked;
      if (attach && !content.transcript?.trim()) {
        status.textContent = 'Ingen transkripsjon å dele. Velg et referat eller en transkripsjon ovenfor.';
        return;
      }
      const includeAudio = attach && this.config.audio && this.root.querySelector('[name="audio"]').checked;
      const file = this.root.querySelector('[name="audio-file"]').files?.[0];
      const source = file ? {read: async () => file} : content.audio;
      if (includeAudio && !source) {
        status.textContent = 'Velg en lydfil, eller fjern avhukingen for lyd.';
        return;
      }
      const job = this.generations.has(target);
      this.pending = {id: crypto.randomUUID(), audioSource: includeAudio ? source : null, audio: null, body: {
        rating, category: this.root.querySelector('[name="category"]').value,
        comment: this.root.querySelector('[name="comment"]').value,
        attach, ...(job ? {job_id: target} : attach ? {transcript: content.transcript} : {}),
      }};
    }
    this.sending = true;
    fieldset.disabled = true;
    button.disabled = true;
    button.textContent = 'Sender …';
    let definitive = false;
    let message = '';
    try {
      if (this.pending.audioSource && !this.pending.body.audio_sha256) {
        status.textContent = 'Klargjør lyd …';
        if (!this.pending.audio) {
          try { this.pending.audio = await this.pending.audioSource.read(); }
          catch {
            definitive = true;
            throw new Error('Kunne ikke hente lyden. Stopp opptaket, eller velg lydfilen manuelt.');
          }
        }
        const audio = this.pending.audio;
        if (!audio?.size || audio.size > this.config.max_audio_bytes) {
          definitive = true;
          throw new Error('Velg en lydfil på under 350 MB.');
        }
        const suffix = audio.name?.split('.').pop()?.toLowerCase();
        const types = {wav: 'audio/wav', mp3: 'audio/mpeg', m4a: 'audio/mp4', mp4: 'audio/mp4',
          webm: 'audio/webm', ogg: 'audio/ogg', flac: 'audio/flac'};
        const type = audio.type && audio.type !== 'application/octet-stream' ? audio.type : types[suffix];
        status.textContent = 'Laster opp lyd … Dette kan ta litt tid.';
        const response = await this.request(`/feedback/${this.pending.id}/audio`, {
          method: 'PUT', headers: {'Content-Type': type || 'application/octet-stream'}, body: audio,
        });
        if (!response.ok) {
          definitive = [403, 404, 409, 413, 415, 422].includes(response.status);
          const error = await response.json().catch(() => ({}));
          throw new Error(typeof error.detail === 'string' ? error.detail : 'Kunne ikke laste opp lyden.');
        }
        const uploaded = await response.json();
        if (!/^[0-9a-f]{64}$/.test(uploaded.sha256 || '')) throw new Error('Ufullstendig svar fra lydopplastingen.');
        this.pending.body.audio_sha256 = uploaded.sha256;
      }
      status.textContent = 'Lagrer tilbakemeldingen …';
      const response = await this.request(`/feedback/${this.pending.id}`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(this.pending.body),
      });
      if (!response.ok) {
        definitive = [403, 404, 409, 413, 422].includes(response.status);
        const error = await response.json().catch(() => ({}));
        throw new Error(typeof error.detail === 'string' ? error.detail : 'Kunne ikke lagre tilbakemeldingen.');
      }
      const result = await response.json();
      status.textContent = 'Takk! Tilbakemeldingen din hjelper oss å forbedre Transkriberit.';
      this.root.querySelector('[data-feedback-case]').textContent = result.case_id;
      this.root.querySelector('[data-feedback-receipt]').hidden = false;
      button.hidden = true;
      fieldset.hidden = true;
      this.root.querySelector('[data-feedback-new]').hidden = false;
      // Release the prepared audio Blob after confirmed delivery.
      this.pending.audio = null;
    } catch (error) {
      message = definitive ? error.message : 'Kunne ikke bekrefte lagring. Prøv igjen – du trenger ikke fylle ut på nytt.';
      if (definitive) {
        this.pending = null;
        fieldset.disabled = false;
      }
      status.textContent = message;
      button.textContent = definitive ? 'Send tilbakemelding' : 'Prøv igjen';
    } finally {
      this.sending = false;
      button.disabled = false;
    }
  }
}
if (typeof module !== 'undefined') module.exports = {SessionFeedback};
