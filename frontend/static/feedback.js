/* Feedback stays in memory until explicitly submitted. No audio capture or upload. */
class SessionFeedback {
  constructor({root, request, transcript}) {
    this.root = root;
    this.request = request;
    this.transcript = transcript;
    this.generations = new Map();
    this.pending = null;
    this.config = {enabled: false, attachments: false};
    root.querySelector('form').addEventListener('submit', event => {
      event.preventDefault();
      this.submit();
    });
    root.querySelector('[name="attach"]').addEventListener('change', () => this.preview());
    root.querySelector('[name="target"]').addEventListener('change', () => this.preview());
    root.querySelector('[data-feedback-new]').addEventListener('click', () => {
      this.pending = null;
      root.querySelector('[type="submit"]').hidden = false;
      root.querySelector('fieldset').disabled = false;
      root.querySelector('[data-feedback-new]').hidden = true;
      root.querySelector('[role="status"]').textContent = '';
    });
  }
  async load() {
    try {
      const response = await this.request('/feedback/config', {cache: 'no-store'});
      if (!response.ok) return;
      this.config = await response.json();
      this.root.hidden = !this.config.enabled;
      this.root.querySelector('[data-attachment-option]').hidden = !this.config.attachments;
    } catch { /* Feedback availability must not affect recording. */ }
  }
  capture(key, payload, result) {
    this.generations.set(key, {transcript: payload.transkripsjon, summary: result.tekst});
    const option = document.createElement('option');
    option.value = key;
    option.textContent = `${new Date().toLocaleTimeString('nb-NO')} · ${result.prompt?.title || result.handling || 'Referat'} · ${result.modell || ''}`;
    const targets = this.root.querySelector('[name="target"]');
    targets.appendChild(option);
    if (this.generations.size > 10) {
      const oldest = this.generations.keys().next().value;
      this.generations.delete(oldest);
      Array.from(targets.options).find(item => item.value === oldest)?.remove();
    }
  }
  selection() {
    const target = this.root.querySelector('[name="target"]').value;
    return {target, content: this.generations.get(target) || {transcript: this.transcript(target)}};
  }
  preview() {
    const attach = this.root.querySelector('[name="attach"]').checked;
    const preview = this.root.querySelector('[data-feedback-preview]');
    preview.hidden = !attach;
    const {content} = this.selection();
    preview.textContent = attach ? `Transkripsjon:\n${content.transcript || '(ingen tekst)'}${content.summary ? '\n\nGenerert referat:\n' + content.summary : ''}` : '';
  }
  async submit() {
    const status = this.root.querySelector('[role="status"]');
    const fieldset = this.root.querySelector('fieldset');
    const button = this.root.querySelector('[type="submit"]');
    if (!this.pending) {
      const {target, content} = this.selection();
      const attach = this.config.attachments && this.root.querySelector('[name="attach"]').checked;
      if (attach && !content.transcript?.trim()) {
        status.textContent = 'Ingen transkripsjon å legge ved.';
        return;
      }
      const job = this.generations.has(target);
      this.pending = {id: crypto.randomUUID(), body: JSON.stringify({
        rating: Number(this.root.querySelector('[name="rating"]').value),
        category: this.root.querySelector('[name="category"]').value,
        comment: this.root.querySelector('[name="comment"]').value,
        attach, ...(job ? {job_id: target} : attach ? {transcript: content.transcript} : {}),
      })};
    }
    fieldset.disabled = true;
    button.disabled = true;
    status.textContent = 'Sender …';
    try {
      const response = await this.request(`/feedback/${this.pending.id}`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'}, body: this.pending.body,
      });
      if (!response.ok) {
        // A definitive rejection permits correction; ambiguous delivery preserves the snapshot.
        if ([403, 404, 409, 413, 422].includes(response.status)) {
          this.pending = null;
          fieldset.disabled = false;
        }
        throw new Error('submission failed');
      }
      const result = await response.json();
      status.textContent = `Takk! Tilbakemeldingen er lagret. Referanse: ${result.case_id}`;
      button.hidden = true;
      this.root.querySelector('[data-feedback-new]').hidden = false;
    } catch {
      status.textContent = 'Kunne ikke bekrefte lagring. Trykk Send for å prøve igjen. Samme tilbakemelding sendes på nytt.';
    } finally { button.disabled = false; }
  }
}
if (typeof module !== 'undefined') module.exports = {SessionFeedback};
