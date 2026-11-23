"""Shared synthetic-demo access, checked only after employee authentication."""

import asyncio
import hashlib
import hmac
import logging
import os
import re
from contextlib import suppress
from dataclasses import dataclass, field
from html import escape
from time import monotonic, time
from urllib.parse import parse_qsl, urlencode

from fastapi import HTTPException
from starlette.requests import Request, ClientDisconnect
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from frontend.identity import authentication_required, owner_id

SESSION_SECONDS = 8 * 60 * 60
MAX_BODY_BYTES = 2048
MAX_PASSWORD_LENGTH = 512
TOKEN_PATTERN = re.compile(r"1\.(\d{1,12})\.(\d{1,12})\.([0-9a-f]{64})\.([0-9a-f]{64})\.([0-9a-f]{64})", re.ASCII)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PasswordGateConfig:
    enabled: bool
    auth_required: bool
    password_digest: bytes = field(repr=False)
    signing_key: bytes = field(repr=False)

    @classmethod
    def from_env(cls):
        password = os.getenv("ACCESS_PASSWORD")
        key = os.getenv("ACCESS_SESSION_KEY")
        enabled = (os.getenv("ACCESS_PASSWORD_REQUIRED", "").lower() == "true"
                   or password is not None or key is not None)
        auth_required = authentication_required()
        if enabled:
            if not password:
                raise RuntimeError("ACCESS_PASSWORD is required when the password gate is enabled")
            if not key or len(key.encode("utf-8")) < 32:
                raise RuntimeError("ACCESS_SESSION_KEY must contain at least 32 UTF-8 bytes")
            if (len(password) > MAX_PASSWORD_LENGTH
                    or len(urlencode({"password": password})) > MAX_BODY_BYTES):
                raise RuntimeError("ACCESS_PASSWORD exceeds the login input limit")
        return cls(enabled, auth_required, hashlib.sha256((password or "").encode("utf-8")).digest(),
                   (key or "").encode("utf-8"))


class LoginLimiter:
    """Bound both attempts and principal storage in a single-replica process."""

    WINDOW_SECONDS = 60
    PRINCIPAL_LIMIT = 5
    GLOBAL_LIMIT = 100
    MAX_PRINCIPALS = 256

    def __init__(self):
        self.window_start = monotonic()
        self.total = 0
        self.principals = {}

    def consume(self, owner):
        now = monotonic()
        if now - self.window_start >= self.WINDOW_SECONDS:
            self.window_start = now
            self.total = 0
            self.principals.clear()
        retry = max(1, int(self.WINDOW_SECONDS - (now - self.window_start)) + 1)
        count = self.principals.get(owner, 0)
        if (self.total >= self.GLOBAL_LIMIT or count >= self.PRINCIPAL_LIMIT
                or (owner not in self.principals and len(self.principals) >= self.MAX_PRINCIPALS)):
            return retry
        self.total += 1
        self.principals[owner] = count + 1
        return 0


async def _wait_until(expiry):
    while (remaining := expiry - time()) > 0:
        await asyncio.sleep(remaining)


class PasswordGateMiddleware:
    def __init__(self, app, config=None):
        self.app = app
        self.config = config if config is not None else PasswordGateConfig.from_env()
        self.limiter = LoginLimiter()
        self.fingerprint = hmac.digest(
            self.config.signing_key, b"password-rotation:" + self.config.password_digest, "sha256"
        ).hex()

    def _cookie_settings(self, scope):
        secure = self.config.auth_required or scope.get("scheme") in {"https", "wss"}
        return ("__Host-ao_gate" if secure else "ao_gate"), secure

    def _sign(self, payload):
        return hmac.digest(self.config.signing_key, b"session:" + payload.encode("ascii"), "sha256").hex()

    def _issue(self, owner):
        issued = int(time())
        payload = f"1.{issued}.{issued + SESSION_SECONDS}.{owner}.{self.fingerprint}"
        return f"{payload}.{self._sign(payload)}"

    def _verify(self, token, owner):
        if not owner or not isinstance(token, str) or len(token) > 320:
            return None
        match = TOKEN_PATTERN.fullmatch(token)
        if not match:
            return None
        issued, expiry = int(match[1]), int(match[2])
        now = time()
        if issued > now or expiry <= now or expiry - issued != SESSION_SECONDS:
            return None
        payload, signature = token.rsplit(".", 1)
        if not (hmac.compare_digest(signature, self._sign(payload))
                and hmac.compare_digest(match[3], owner)
                and hmac.compare_digest(match[4], self.fingerprint)):
            return None
        return expiry

    def _session(self, scope, owner):
        name, _ = self._cookie_settings(scope)
        # Do not hand unbounded or duplicate cookie input to a general parser.
        headers = [value for key, value in scope["headers"] if key.lower() == b"cookie"]
        if sum(map(len, headers)) > 8192:
            return None
        tokens = []
        for header in headers:
            for item in header.decode("latin-1").split(";"):
                key, separator, value = item.strip().partition("=")
                if key == name and separator:
                    tokens.append(value)
                    if len(tokens) > 1:
                        return None
        return self._verify(tokens[0], owner) if len(tokens) == 1 else None

    @staticmethod
    def _check_origin(scope):
        # Reuse the deployed explicit allowlist. WS browser origins use HTTP(S).
        from frontend.feedback import check_origin

        request_scope = dict(scope, type="http")
        if scope["type"] == "websocket":
            request_scope["scheme"] = "https" if scope.get("scheme") == "wss" else "http"
        request = Request(request_scope)
        if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
            raise HTTPException(403, "Ugyldig avsender")
        if scope["type"] == "websocket" and not request.headers.get("origin"):
            raise HTTPException(403, "Ugyldig avsender")
        check_origin(request)

    @staticmethod
    def _login_page(message="", status=200, headers=None):
        error = f'<p id="error" role="alert">{escape(message)}</p>' if message else ""
        described_by = ' aria-describedby="error"' if message else ""
        page = f"""<!doctype html>
<html lang="nb"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tilgang til demo | Transkribering</title>
<style>
* {{ box-sizing: border-box; }} body {{ margin: 0; color: #262626; background: #fff;
font: 1rem/1.6 system-ui, sans-serif; }} main {{ max-width: 32rem; margin: 3rem auto; padding: 1.5rem; }}
h1 {{ font-size: 1.375rem; }} label {{ display: block; font-weight: 600; }}
input {{ width: 100%; padding: .6rem; border: 1px solid #6f6f6f; border-radius: 4px; font: inherit; }}
button {{ margin-top: 1rem; padding: .6rem 1rem; background: #005aa8; color: #fff;
border: 0; border-radius: 4px; font: inherit; cursor: pointer; }}
:focus-visible {{ outline: 3px solid #262626; outline-offset: 3px; }}
[role=alert] {{ color: #ba3a26; }}
</style></head><body><main>
<h1>Tilgang til demo</h1>
<p>Demoen skal bare brukes med syntetiske data. Ikke bruk personopplysninger eller ekte møteinnhold.</p>
<p>Skriv inn det delte passordet. Tilgangen varer i åtte timer.</p>
{error}<form method="post" action="/login">
<label for="password">Passord</label>
<input id="password" name="password" type="password" autocomplete="current-password"
maxlength="{MAX_PASSWORD_LENGTH}" required{described_by}>
<button type="submit">Åpne demoen</button></form>
</main></body></html>"""
        return HTMLResponse(page, status_code=status, headers={
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                                       "base-uri 'none'; frame-ancestors 'none'",
            **(headers or {}),
        })

    async def _login(self, scope, receive, owner):
        if scope["method"] in {"GET", "HEAD"}:
            response = self._login_page()
            if scope["method"] == "HEAD":
                response.body = b""
            return response
        retry = self.limiter.consume(owner)
        if retry:
            return self._login_page("For mange forsøk. Vent litt og prøv igjen.", 429,
                                    {"Retry-After": str(retry)})
        try:
            self._check_origin(scope)
        except HTTPException:
            return self._login_page("Ugyldig avsender. Åpne innloggingssiden på nytt.", 403)
        request = Request(scope, receive)
        content_types = request.headers.getlist("content-type")
        if len(content_types) != 1 or content_types[0].split(";")[0].strip().lower() != "application/x-www-form-urlencoded":
            return self._login_page("Send passordet med innloggingsskjemaet.", 415)
        raw = bytearray()
        try:
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_BODY_BYTES:
                    return self._login_page("Skjemaet er for stort.", 413)
                raw.extend(chunk)
        except ClientDisconnect:
            return self._login_page("Innsendingen ble avbrutt. Prøv igjen.", 400)
        try:
            form = parse_qsl(raw.decode("utf-8"), keep_blank_values=True, strict_parsing=True,
                             encoding="utf-8", errors="strict", max_num_fields=1)
        except (UnicodeError, ValueError):
            return self._login_page("Kontroller passordfeltet og prøv igjen.", 400)
        if len(form) != 1 or form[0][0] != "password" or len(form[0][1]) > MAX_PASSWORD_LENGTH:
            return self._login_page("Kontroller passordfeltet og prøv igjen.", 400)
        digest = hashlib.sha256(form[0][1].encode("utf-8")).digest()
        if not hmac.compare_digest(digest, self.config.password_digest):
            log.warning("Access password login rejected")
            return self._login_page("Feil passord. Prøv igjen.", 401)
        response = RedirectResponse("/", status_code=303, headers={"Cache-Control": "no-store"})
        name, secure = self._cookie_settings(scope)
        response.set_cookie(name, self._issue(owner), max_age=SESSION_SECONDS, secure=secure,
                            httponly=True, samesite="lax", path="/")
        return response

    async def _websocket(self, scope, receive, send, expiry):
        finished = False
        expiring = False
        send_lock = asyncio.Lock()

        async def tracked_receive():
            nonlocal finished
            message = await receive()
            if message["type"] == "websocket.disconnect":
                finished = True
            return message

        async def tracked_send(message):
            nonlocal finished
            async with send_lock:
                if finished:
                    return
                closing = message["type"] == "websocket.close"
                if expiring:
                    if not closing:
                        return
                    message = {"type": "websocket.close", "code": 1008}
                await send(message)
                if closing:
                    finished = True

        connection = asyncio.create_task(self.app(scope, tracked_receive, tracked_send))
        timer = asyncio.create_task(_wait_until(expiry))
        try:
            done, _ = await asyncio.wait({connection, timer}, return_when=asyncio.FIRST_COMPLETED)
            if connection in done:
                await connection
                return
            await timer
            # Close immediately, not after transcription workers finish cleanup.
            expiring = True
            connection.cancel()
            await tracked_send({"type": "websocket.close", "code": 1008})
            with suppress(asyncio.CancelledError):
                await connection
        finally:
            for task in (connection, timer):
                task.cancel()
            for task in (connection, timer):
                with suppress(asyncio.CancelledError):
                    await task

    async def __call__(self, scope, receive, send):
        if not self.config.enabled or scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        method = scope.get("method")
        if scope["type"] == "http" and method in {"GET", "HEAD"} and scope["path"] in {"/isAlive", "/isReady"}:
            await self.app(scope, receive, send)
            return
        owner = owner_id(scope.get("state", {}).get("user"), auth_required=self.config.auth_required,
                         local_owner=hashlib.sha256(b"local").hexdigest())
        if scope["type"] == "websocket":
            try:
                self._check_origin(scope)
            except HTTPException:
                await send({"type": "websocket.close", "code": 1008})
                return
        if owner and scope["type"] == "http" and scope["path"] == "/login" and method in {"GET", "HEAD", "POST"}:
            response = await self._login(scope, receive, owner)
            await response(scope, receive, send)
            return
        expiry = self._session(scope, owner)
        if expiry is not None:
            scope.setdefault("state", {})["access_password_expires_at"] = expiry
            if scope["type"] == "websocket":
                await self._websocket(scope, receive, send, expiry)
            else:
                await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        request = Request(scope)
        headers = {"Cache-Control": "no-store", "X-Access-Password-Required": "true"}
        if (owner and scope["path"] in {"/", "/docs", "/redoc", "/docs/oauth2-redirect"}
                and method in {"GET", "HEAD"} and "text/html" in request.headers.get("accept", "")
                and request.headers.get("sec-fetch-dest", "document") == "document"
                and request.headers.get("sec-fetch-mode", "navigate") == "navigate"):
            response = RedirectResponse("/login", status_code=303, headers=headers)
        else:
            response = JSONResponse({"detail": "Skriv inn passordet for å åpne demoen.",
                                     "reason": "access_password"}, status_code=401, headers=headers)
        await response(scope, receive, send)
