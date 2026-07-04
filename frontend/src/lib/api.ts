import type { AppState, SecurityStatus, StreamEvent, WorkspaceStatus } from './types';

// '' = same origin (prod, served by FastAPI) or dev (Vite proxies /api → :8000).
const BASE = import.meta.env.VITE_API_BASE ?? '';

async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  return r.json();
}

export const api = {
  state: () => getJSON<AppState>('/api/state'),
  workspaceStatus: () => getJSON<WorkspaceStatus>('/api/workspace/status'),
  securityStatus: () => getJSON<SecurityStatus>('/api/security/status'),
  remediate: (service: string) => postJSON<{ healed: string[] }>('/api/remediate', { service }),
  verify: (service: string) => postJSON<{ verdict: string }>('/api/verify', { service }),
  breakService: (service: string) => postJSON('/api/break', { service }),
  reset: () => postJSON('/api/reset'),
};

/** Stream a live incident run (SSE). Returns the EventSource so callers can close it. */
export function streamIncident(mode: string | null, onEvent: (e: StreamEvent) => void): EventSource {
  const url = BASE + '/api/incident/stream' + (mode ? `?mode=${encodeURIComponent(mode)}` : '');
  const es = new EventSource(url);
  es.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data) as StreamEvent);
    } catch {
      /* ignore malformed frame */
    }
  };
  return es;
}

/** The legacy console URL (same origin) — for features not yet ported to React. */
export const LEGACY_URL = (BASE || '') + '/legacy';
