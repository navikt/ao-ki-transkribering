/* A lost browser session must not discard audio or reload the recording page. */
class SessionAccess {
  constructor({request = (...args) => fetch(...args), onChange = () => {}} = {}) {
    Object.assign(this, {request, onChange});
    this.required = false;
    this.waiters = new Set();
  }
  requireLogin() {
    if (this.required) return;
    this.required = true;
    this.onChange(true);
  }
  async fetch(url, options) {
    if (this.required) {
      options?.signal?.throwIfAborted();
      return {ok: false, status: 401};
    }
    const response = await this.request(url, options);
    if (response.status === 401) this.requireLogin();
    return response;
  }
  async recover() {
    if (!this.required) return true;
    if (this.recovering) return this.recovering;
    this.recovering = (async () => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await this.request("/system/status", {
          credentials: "same-origin", cache: "no-store", redirect: "error", signal: controller.signal,
        });
        if (!response.ok) return false;
        this.required = false;
        for (const resolve of this.waiters) resolve();
        this.waiters.clear();
        this.onChange(false);
        return true;
      } catch { return false; }
      finally { clearTimeout(timeout); }
    })();
    try { return await this.recovering; }
    finally { this.recovering = null; }
  }
  waitForAuthentication(signal) {
    signal?.throwIfAborted();
    if (!this.required) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const done = () => { cleanup(); resolve(); };
      const abort = () => { cleanup(); reject(signal.reason); };
      const cleanup = () => {
        this.waiters.delete(done);
        signal?.removeEventListener("abort", abort);
      };
      this.waiters.add(done);
      signal?.addEventListener("abort", abort, {once: true});
    });
  }
}

if (typeof module !== "undefined") module.exports = {SessionAccess};
