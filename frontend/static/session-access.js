/* A lost browser session must not discard audio or reload the recording page. */
class SessionAccess {
  constructor({request = (...args) => fetch(...args), onChange = () => {}} = {}) {
    Object.assign(this, {request, onChange});
    this.required = false;
    this.loginUrl = "/oauth2/login";
    this.waiters = new Set();
  }
  requireLogin(loginUrl = "/oauth2/login") {
    if (this.required && this.loginUrl === loginUrl) return;
    this.required = true;
    this.loginUrl = loginUrl;
    this.onChange(true, this.loginUrl);
  }
  async fetch(url, options) {
    if (this.required) {
      options?.signal?.throwIfAborted();
      return {ok: false, status: 401};
    }
    const response = await this.request(url, options);
    if (response.status === 401) {
      const passwordRequired = response.headers?.get("X-Access-Password-Required") === "true";
      this.requireLogin(passwordRequired ? "/login" : "/oauth2/login");
    }
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
        if (response.status === 401) {
          const passwordRequired = response.headers?.get("X-Access-Password-Required") === "true";
          this.requireLogin(passwordRequired ? "/login" : "/oauth2/login");
        }
        if (!response.ok) return false;
        this.required = false;
        this.loginUrl = "/oauth2/login";
        for (const resolve of this.waiters) resolve();
        this.waiters.clear();
        this.onChange(false, this.loginUrl);
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
