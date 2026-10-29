/* Capture health is independent of upload health. No audio or device labels in reports. */
class RecordingLifecycle {
  constructor({doc = document, nav = navigator, now = () => Date.now(),
    onstate = () => {}, ongaps = () => {}, report = () => {}} = {}) {
    Object.assign(this, {doc, nav, now, onstate, ongaps, report});
    this.active = false;
    this.gaps = [];
    this.listeners = [];
    this.captured = 0;
    this.wake = "unsupported";
  }
  listen(target, name, handler) {
    target.addEventListener(name, handler);
    this.listeners.push(() => target.removeEventListener(name, handler));
  }
  start(context, tracks) {
    this.context = context;
    this.tracks = tracks;
    this.active = true;
    this.started = this.lastWall = this.now();
    this.lastAudio = context.currentTime;
    this.lastSample = this.started;
    this.listen(this.doc, "visibilitychange", () => {
      this.emit("visibility_changed");
      this.check();
      if (this.doc.visibilityState === "visible") {
        this.acquireWake();
        this.resume();
      }
    });
    this.listen(context, "statechange", () => {
      this.emit("audio_state_changed");
      this.check();
      if (context.state === "interrupted" && this.doc.visibilityState === "visible") this.resume();
    });
    for (const track of tracks) {
      for (const name of ["mute", "unmute", "ended"]) {
        this.listen(track, name, () => { this.emit("track_changed"); this.check(); });
      }
    }
    this.acquireWake();
    this.check();
  }
  emit(kind, duration_s = 0) {
    this.report({kind, elapsed_s: Math.max(0, (this.now() - this.started) / 1000),
      duration_s, visibility: this.doc.visibilityState === "hidden" ? "hidden" : "visible",
      track_state: this.tracks.some(t => t.readyState === "ended") ? "ended"
        : this.tracks.some(t => t.muted) ? "muted" : "live",
      reason: this.pending?.reason || null,
      audio_state: ["running", "suspended", "interrupted", "closed"].includes(this.context.state)
        ? this.context.state : "unknown", wake_lock: this.wake});
  }
  publish() {
    const state = {paused: !!this.pending, ended: this.tracks.some(t => t.readyState === "ended"),
      wake: this.wake, active: this.active};
    const key = JSON.stringify(state);
    if (key !== this.lastState) { this.lastState = key; this.onstate(state); }
  }
  async acquireWake() {
    if (!this.active || this.doc.visibilityState !== "visible" || this.lock || this.requesting) return;
    if (!this.nav.wakeLock) { this.publish(); return; }
    this.requesting = true;
    try {
      const lock = await this.nav.wakeLock.request("screen");
      if (!this.active || this.doc.visibilityState !== "visible") {
        await lock.release();
        return;
      }
      if (lock.released) { this.wake = "released"; return; }
      this.lock = lock;
      this.wake = "held";
      this.listen(lock, "release", () => {
        if (this.lock !== lock) return;
        this.lock = null;
        this.wake = "released";
        this.emit("wake_lock_changed");
        this.publish();
      });
    } catch { this.wake = "unavailable"; }
    finally {
      this.requesting = false;
      if (this.active) { this.emit("wake_lock_changed"); this.publish(); }
    }
  }
  interrupt(reason, start = this.now()) {
    if (this.pending) return;
    this.pending = {sample_offset: this.captured,
      elapsed_start_s: Math.max(0, (start - this.started) / 1000), duration_s: 0, reason};
    this.gaps.push(this.pending);
    this.emit("capture_interrupted");
  }
  updateGap() {
    if (!this.pending) return;
    this.pending.duration_s = Math.max(0,
      (this.now() - this.started) / 1000 - this.pending.elapsed_start_s);
    this.ongaps(this.gaps.map(gap => ({...gap})));
  }
  check(watchFrames = true) {
    if (!this.active) return;
    const now = this.now();
    const wall = now - this.lastWall;
    const audio = (this.context.currentTime - this.lastAudio) * 1000;
    if (this.tracks.some(t => t.readyState === "ended")) this.interrupt("track_ended");
    else if (this.tracks.some(t => t.muted)) this.interrupt("track_muted");
    else if (this.context.state !== "running") this.interrupt("audio_suspended");
    else if (wall - audio > 2000) this.interrupt("clock_gap", this.lastWall);
    else if (watchFrames && wall < 2500 && this.doc.visibilityState === "visible" &&
        now - this.lastSample > 3000) this.interrupt("no_frames", this.lastSample);
    this.lastWall = now;
    this.lastAudio = this.context.currentTime;
    this.updateGap();
    this.publish();
  }
  samples(count) {
    if (!this.active) return;
    // Compare the audio clock before accepting frames queued during a browser suspension.
    if (this.now() - this.lastWall >= 1000) this.check(false);
    this.lastSample = this.now();
    if (this.pending && this.context.state === "running" &&
        this.tracks.every(t => !t.muted && t.readyState !== "ended")) {
      this.updateGap();
      this.emit("capture_recovered", this.pending.duration_s);
      this.pending = null;
      this.publish();
    }
    this.captured += count;
  }
  async resume(force = false) {
    if (!this.active || (this.resuming && !force)) return;
    this.resuming = true;
    try {
      this.acquireWake();
      if (this.context.state !== "running" && this.context.state !== "closed") await this.context.resume();
    } catch { if (this.active) this.emit("resume_failed"); }
    finally { this.resuming = false; this.check(); }
  }
  stop() {
    if (!this.active) return;
    this.check();
    this.active = false;
    this.emit("capture_stopped");
    this.listeners.splice(0).forEach(remove => remove());
    const lock = this.lock;
    this.lock = null;
    if (lock) Promise.resolve(lock.release()).catch(() => {});
    this.publish();
  }
}

if (typeof module !== "undefined") module.exports = {RecordingLifecycle};
