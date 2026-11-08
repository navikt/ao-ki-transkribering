/* View changes move existing nodes; recording and generation keep their state. */
class MeetingWorkspace {
  constructor() {
    this.definition = null;
    this.drafts = new Map();
    this.loading = 0;
    this.busy = false;
    this.fields = ["system", "user", "temperature", "top-p", "max-tokens", "repeat"];
    this.transcript = document.querySelector(".transkripsjon-kolonne");
    for (const id of ["transkripsjon-modell", "referat-modell"]) {
      this.el("workspace-models").appendChild(this.el(id).closest("label"));
    }
    this.el("workspace-audio-diagnostics").appendChild(this.el("mikrofon-diagnostikk"));
    for (const id of ["veileder-variant-valg", "referat-avgrensning", "referat-egne-instruksjoner"]) {
      this.el("workspace-prompt").appendChild(this.el(id));
    }
    for (const field of this.fields) this.el("workspace-" + field).addEventListener("input", () => this.changed());
    this.el("workspace-prompt-diff").addEventListener("toggle", () => this.renderDiff());
    this.el("advanced-workspace").querySelector('[role="tablist"]').addEventListener("keydown", event => {
      const tabs = ["transcription", "prompt", "diagnostics"];
      const index = tabs.indexOf(document.body.dataset.advancedTab);
      const next = event.key === "ArrowRight" ? tabs[(index + 1) % 3]
        : event.key === "ArrowLeft" ? tabs[(index + 2) % 3]
        : event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[2] : null;
      if (next) { event.preventDefault(); this.setTab(next); this.el("tab-" + next).focus(); }
    });
    this.setView("simple");
    this.syncPrompt();
  }

  el(id) { return document.getElementById(id); }

  versioned(scenario) { return /^(basis|stikkord|utfyllende)_\d+_\d+$/.test(scenario); }

  setView(view) {
    if (!["simple", "advanced"].includes(view)) return;
    document.body.dataset.view = view;
    this.el("advanced-workspace").hidden = view !== "advanced";
    this.el("simple-transcription").hidden = view !== "simple";
    this.el(view === "advanced" ? "workspace-transcription" : "simple-transcription-slot").appendChild(this.transcript);
    for (const name of ["simple", "advanced"]) this.el("view-" + name).setAttribute("aria-pressed", String(view === name));
    this.setTab(document.body.dataset.advancedTab || "transcription");
  }

  setTab(tab) {
    if (!["transcription", "prompt", "diagnostics"].includes(tab)) return;
    document.body.dataset.advancedTab = tab;
    for (const name of ["transcription", "prompt", "diagnostics"]) {
      this.el("workspace-" + name).hidden = tab !== name;
      this.el("tab-" + name).setAttribute("aria-selected", String(tab === name));
      this.el("tab-" + name).tabIndex = tab === name ? 0 : -1;
    }
  }

  values() {
    return {
      system_prompt: this.el("workspace-system").value,
      user_prompt: this.el("workspace-user").value,
      temperature: Number(this.el("workspace-temperature").value),
      top_p: Number(this.el("workspace-top-p").value),
      max_tokens: Number(this.el("workspace-max-tokens").value),
      ...(this.definition?.repeat_penalty != null ? {repeat_penalty: Number(this.el("workspace-repeat").value)} : {}),
    };
  }

  baseline() {
    const {system_prompt, user_prompt, temperature, top_p, max_tokens, repeat_penalty} = this.definition;
    return {system_prompt, user_prompt, temperature, top_p, max_tokens,
      ...(repeat_penalty != null ? {repeat_penalty} : {})};
  }

  overrides() {
    if (!this.definition || this.definition.prompt.id !== this.el("referat-scenario").value) return null;
    const values = this.values();
    const baseline = this.baseline();
    return Object.keys(values).some(key => values[key] !== baseline[key]) ? values : null;
  }

  validate(reveal = true) {
    if (!this.definition && this.versioned(this.el("referat-scenario").value)) {
      this.el("workspace-prompt-status").textContent = "Tekstmalen er ikke klar. Hent tekstmalen på nytt før generering.";
      if (reveal) { this.setView("advanced"); this.setTab("prompt"); }
      return false;
    }
    if (!this.overrides()) return true;
    const v = this.values();
    const valid = v.system_prompt.trim() && v.system_prompt.length <= 12000 && v.user_prompt.length <= 18000
      && v.user_prompt.split("{transkripsjon}").length === 2
      && this.fields.slice(2).filter(field => field !== "repeat" || v.repeat_penalty != null)
        .every(field => this.el("workspace-" + field).value.trim() !== "")
      && Number.isFinite(v.temperature) && v.temperature >= 0 && v.temperature <= 2
      && Number.isFinite(v.top_p) && v.top_p > 0 && v.top_p <= 1
      && Number.isInteger(v.max_tokens) && v.max_tokens >= 128 && v.max_tokens <= 8192
      && (v.repeat_penalty == null || Number.isFinite(v.repeat_penalty) && v.repeat_penalty >= 1 && v.repeat_penalty <= 2);
    if (!valid) {
      if (reveal) { this.setView("advanced"); this.setTab("prompt"); }
      this.el("workspace-prompt-status").textContent = "Kontroller parametergrensene. Brukerprompten må inneholde {transkripsjon} nøyaktig én gang.";
    }
    return Boolean(valid);
  }

  changed() {
    if (this.definition) this.drafts.set(this.definition.prompt.id, this.values());
    const custom = Boolean(this.overrides());
    this.el("workspace-custom-indicator").hidden = !custom;
    this.el("workspace-prompt-title").textContent = this.definition
      ? this.definition.prompt.title + (custom ? " · Ulagret variant" : "") : "Tekstmal";
    this.el("workspace-prompt-status").textContent = custom ? "Ulagret variant · gjelder også i enkel visning" : "";
    this.renderDiff();
  }

  renderDiff() {
    if (!this.definition || !this.el("workspace-prompt-diff").open) return;
    const before = this.baseline(), after = this.values();
    const text = v => Object.entries(v).map(([key, value]) => `${key}\n${value}`).join("\n\n");
    this.el("workspace-prompt-diff-text").innerHTML = summaryDiffHtml(text(before), text(after));
  }

  fill(values) {
    for (const [field, key] of [["system", "system_prompt"], ["user", "user_prompt"],
      ["temperature", "temperature"], ["top-p", "top_p"], ["max-tokens", "max_tokens"]]) {
      this.el("workspace-" + field).value = String(values[key]);
    }
    this.el("workspace-context").textContent = this.definition.context_tokens.toLocaleString("nb-NO");
    this.el("workspace-repeat-field").hidden = this.definition.repeat_penalty == null;
    this.el("workspace-repeat").value = values.repeat_penalty != null ? String(values.repeat_penalty) : "";
    this.changed();
  }

  async syncPrompt() {
    const scenario = this.el("referat-scenario").value || "basis_0_2";
    if (scenario === this.definition?.prompt.id) return;
    const revision = ++this.loading;
    this.definition = null;
    this.el("workspace-prompt-fields").disabled = true;
    this.el("workspace-prompt-retry").hidden = true;
    this.el("workspace-custom-indicator").hidden = true;
    this.el("workspace-prompt-diff-text").textContent = "";
    for (const field of this.fields) this.el("workspace-" + field).value = "";
    if (!this.versioned(scenario)) {
      this.el("workspace-prompt-status").textContent = "Tidligere mal · parameterredigering er ikke tilgjengelig";
      return;
    }
    this.el("workspace-prompt-status").textContent = "Henter tekstmal …";
    try {
      const response = await appFetch("/llm/prompt-definitions/" + encodeURIComponent(scenario), {cache: "no-store"});
      if (!response.ok) throw new Error("prompt unavailable");
      const definition = await response.json();
      if (revision !== this.loading) return;
      this.definition = definition;
      this.fill(this.drafts.get(scenario) || this.baseline());
      this.setBusy(this.busy);
    } catch {
      if (revision === this.loading) {
        this.el("workspace-prompt-status").textContent = "Kunne ikke hente tekstmalen. Prøv igjen.";
        this.el("workspace-prompt-retry").hidden = false;
      }
    }
  }

  resetPrompt() {
    if (!this.busy && this.definition) this.fill(this.baseline());
  }

  setBusy(busy) {
    this.busy = busy;
    this.el("workspace-prompt-fields").disabled = busy || !this.definition;
  }

  captureRequest(payload) {
    const definition = this.definition?.prompt.id === payload.scenario ? this.definition : null;
    const prompt = payload.overrides || definition;
    this.el("workspace-request-source").value = payload.transkripsjon;
    this.el("workspace-request-system").value = prompt?.system_prompt || "Serverdefinert mal · promptdetaljer er ikke tilgjengelige";
    this.el("workspace-request-user").value = prompt?.user_prompt?.replace("{transkripsjon}", () => payload.transkripsjon) || "";
    this.el("workspace-request-info").textContent = JSON.stringify({
      model: payload.modell || "Standardmodell", template: payload.scenario,
      custom: Boolean(payload.overrides), source_characters: payload.transkripsjon.length,
      parameters: payload.overrides ? {
        temperature: payload.overrides.temperature, top_p: payload.overrides.top_p,
        max_tokens: payload.overrides.max_tokens,
        ...(payload.overrides.repeat_penalty != null ? {repeat_penalty: payload.overrides.repeat_penalty} : {}),
      } : definition ? {temperature: definition.temperature, top_p: definition.top_p,
        max_tokens: definition.max_tokens,
        ...(definition.repeat_penalty != null ? {repeat_penalty: definition.repeat_penalty} : {})} : null,
    }, null, 2);
  }

  exportPrompt(button) {
    if (!this.definition || !this.validate()) return;
    const variant = {based_on: this.definition.prompt, model: this.el("referat-modell").value || null,
      ...this.values(), context_tokens: this.definition.context_tokens};
    const url = URL.createObjectURL(new Blob([JSON.stringify(variant, null, 2)], {type: "application/json"}));
    const link = document.createElement("a");
    link.href = url; link.download = this.definition.prompt.id + "-variant.json"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    button.textContent = "Eksportert";
    setTimeout(() => {button.textContent = "Eksporter variant";}, 1800);
  }
}

globalThis.meetingWorkspace = null;
