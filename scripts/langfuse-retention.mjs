import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';

const DAY = 86_400_000;

function date(value) {
  const result = new Date(value);
  if (!Number.isFinite(result.getTime())) throw new Error('Invalid retention timestamp');
  return result;
}

// Collect the complete bounded plan before requesting any deletion.
export async function cleanup({ request, projectId, from, days = 30, maxTraces = 100,
  apply = false, confirmProject, now = new Date() }) {
  if (!projectId || !Number.isInteger(days) || days < 3 || !Number.isInteger(maxTraces)
    || maxTraces < 1 || maxTraces > 1000) throw new Error('Invalid retention policy');
  if (apply && confirmProject !== projectId) throw new Error('Explicit project confirmation required');
  const cutoff = new Date(date(now).getTime() - days * DAY);
  const start = date(from);
  if (start >= cutoff) throw new Error('Scan start must precede the retention cutoff');
  const candidates = new Set();
  let pages = 0;

  async function page(params) {
    if (++pages > 1000) throw new Error('Retention scan page limit exceeded; no deletion requested');
    const body = await request('GET', '/api/public/v2/observations', { fields: 'time', limit: '100', ...params });
    if (!Array.isArray(body.data) || !body.meta || typeof body.meta !== 'object') {
      throw new Error('Invalid observations response');
    }
    for (const row of body.data) {
      if (row.projectId !== projectId || typeof row.traceId !== 'string' || !row.traceId
        || typeof row.startTime !== 'string') throw new Error('Observation scope validation failed');
    }
    return body;
  }

  // V4 observations use bounded time windows and cursor pagination, never offset deletion.
  for (let windowStart = start; windowStart < cutoff;) {
    const windowEnd = new Date(Math.min(windowStart.getTime() + 30 * DAY, cutoff.getTime()));
    let cursor;
    const seen = new Set();
    do {
      const body = await page({ fromStartTime: windowStart.toISOString(), toStartTime: windowEnd.toISOString(),
        isRootObservation: 'true', ...(cursor ? { cursor } : {}) });
      for (const row of body.data) {
        if (date(row.startTime) < windowStart || date(row.startTime) > windowEnd) {
          throw new Error('Observation outside requested time window');
        }
        if (!row.endTime || date(row.endTime) >= cutoff || !row.updatedAt || date(row.updatedAt) >= cutoff) continue;
        candidates.add(row.traceId);
        if (candidates.size > maxTraces) throw new Error('Deletion cap exceeded; narrow the scan');
      }
      cursor = body.meta.cursor;
      if (cursor !== undefined && (typeof cursor !== 'string' || !cursor || seen.has(cursor))) {
        throw new Error('Invalid or repeated pagination cursor');
      }
      if (cursor) seen.add(cursor);
    } while (cursor);
    windowStart = windowEnd;
  }

  const eligible = [];
  for (const traceId of candidates) {
    // An old root must not cause deletion of a trace that still has recent observations.
    const recent = await page({ traceId, fromStartTime: cutoff.toISOString(), toStartTime: date(now).toISOString(), limit: '1' });
    if (recent.data.some(row => row.traceId !== traceId)) throw new Error('Trace scope validation failed');
    if (recent.data.length === 0) eligible.push(traceId);
  }
  if (apply && eligible.length) {
    await request('DELETE', '/api/public/traces', {}, { traceIds: eligible });
  }
  return { mode: apply ? 'apply' : 'dry-run', cutoff: cutoff.toISOString(), scanned_from: start.toISOString(),
    candidates: candidates.size, eligible: eligible.length, recent_traces_skipped: candidates.size - eligible.length,
    deletion_requested: apply ? eligible.length : 0 };
}

export function apiRequest(baseUrl, publicKey, secretKey, fetcher = fetch) {
  const base = new URL(baseUrl);
  if (base.username || base.password || base.search || base.hash || base.pathname !== '/') {
    throw new Error('Use a plain API origin without credentials');
  }
  if (base.protocol !== 'https:' && !(base.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname))) {
    throw new Error('HTTPS required outside loopback');
  }
  if (!publicKey?.startsWith('pk-lf-') || !secretKey?.startsWith('sk-lf-')) throw new Error('Project keys required');
  const authorization = `Basic ${Buffer.from(`${publicKey}:${secretKey}`).toString('base64')}`;
  return async (method, path, params, body) => {
    const url = new URL(path, base);
    url.search = new URLSearchParams(params).toString();
    let response;
    try {
      response = await fetcher(url, { method, redirect: 'error', signal: AbortSignal.timeout(15_000),
        headers: { authorization, 'content-type': 'application/json' },
        ...(body ? { body: JSON.stringify(body) } : {}) });
    } catch {
      throw new Error('Retention API connection failed');
    }
    if (!response.ok) throw new Error(`Retention API HTTP ${response.status}`);
    try { return await response.json(); } catch { throw new Error('Invalid retention API response'); }
  };
}

async function main() {
  const { values } = parseArgs({ options: { from: { type: 'string' }, days: { type: 'string', default: '30' },
    'max-traces': { type: 'string', default: '100' }, apply: { type: 'boolean', default: false },
    'confirm-project': { type: 'string' } } });
  const env = process.env;
  const request = apiRequest(env.LANGFUSE_RETENTION_URL || 'http://127.0.0.1:3000',
    env.LANGFUSE_PUBLIC_KEY || env.LANGFUSE_INIT_PROJECT_PUBLIC_KEY,
    env.LANGFUSE_SECRET_KEY || env.LANGFUSE_INIT_PROJECT_SECRET_KEY);
  const result = await cleanup({ request,
    projectId: env.LANGFUSE_RETENTION_PROJECT_ID || env.LANGFUSE_INIT_PROJECT_ID,
    from: values.from, days: Number(values.days), maxTraces: Number(values['max-traces']),
    apply: values.apply, confirmProject: values['confirm-project'] });
  console.log(JSON.stringify(result));
}

if (process.argv[1] === '-' || (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
