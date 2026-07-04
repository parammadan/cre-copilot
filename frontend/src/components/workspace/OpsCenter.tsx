import { useEffect, useRef, useState } from 'react';
import { Play, ShieldCheck, Bot } from 'lucide-react';
import { api, streamIncident } from '../../lib/api';
import type { AppState, Incident, StreamEvent } from '../../lib/types';

type WState = 'idle' | 'working' | 'done' | 'waiting';
interface Worker { role: string; state: WState; terminal: string[]; }

const WORKERS: [string, string][] = [
  ['Commander', 'Orchestration & triage'],
  ['Detector', 'Anomaly detection · KQL'],
  ['Correlator', 'Root-cause correlation'],
  ['Impact', 'Blast-radius analysis'],
  ['Gate', 'Deterministic decision'],
  ['Runbook', 'Remediation playbook'],
  ['Verifier', 'Independent recovery check'],
];
const ORDER = WORKERS.map(w => w[0]);
const TOOL_OWNER: Record<string, string> = {
  detect: 'Detector', get_alerts: 'Detector', detect_trend: 'Detector',
  correlate: 'Correlator', get_logs: 'Correlator',
  assess_impact: 'Impact', get_service_health: 'Impact',
  apply_gate: 'Gate', match_runbook: 'Runbook', write_runbook: 'Runbook',
};
const STAT_LABEL: Record<WState, string> = { idle: 'Idle', working: 'Working', done: 'Complete', waiting: 'Waiting' };
const STAT_COLOR: Record<WState, string> = { idle: 'var(--text-dim)', working: 'var(--accent-blue)', done: '#22c55e', waiting: '#e0a915' };

function freshWorkers(): Record<string, Worker> {
  return Object.fromEntries(WORKERS.map(([n, r]) => [n, { role: r, state: 'idle' as WState, terminal: [] }]));
}

export default function OpsCenter() {
  const [workers, setWorkers] = useState<Record<string, Worker>>(freshWorkers);
  const [evidence, setEvidence] = useState<{ ts: string; tool: string; summary: string }[]>([]);
  const [running, setRunning] = useState(false);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [threshold, setThreshold] = useState(0.7);
  const [verdicts, setVerdicts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const esRef = useRef<EventSource | null>(null);

  const loadState = () => api.state().then((s: AppState) => {
    setIncidents(s.incidents || []);
    setThreshold(s.threshold ?? 0.7);
  }).catch(() => {});

  useEffect(() => { loadState(); return () => esRef.current?.close(); }, []);

  const setWorker = (name: string, patch: Partial<Worker>) =>
    setWorkers(prev => ({ ...prev, [name]: { ...prev[name], ...patch } }));

  const startWorker = (name: string) =>
    setWorkers(prev => {
      const idx = ORDER.indexOf(name);
      const next = { ...prev };
      ORDER.forEach((w, i) => { if (i < idx && next[w]?.state === 'working') next[w] = { ...next[w], state: 'done' }; });
      if (next[name]) next[name] = { ...next[name], state: 'working' };
      return next;
    });

  const addLine = (name: string, line: string) =>
    setWorkers(prev => (prev[name] ? { ...prev, [name]: { ...prev[name], terminal: [...prev[name].terminal, line] } } : prev));

  const runIncident = () => {
    setWorkers(freshWorkers());
    setEvidence([]);
    setRunning(true);
    const es = streamIncident(null, (ev: StreamEvent) => {
      if (ev.type === 'agent_start') startWorker(ev.agent);
      else if (ev.type === 'tool_call') {
        const owner = TOOL_OWNER[ev.tool] || ev.agent;
        setWorker(owner, { state: 'working' });
        addLine(owner, `> ${ev.tool}()`);
      } else if (ev.type === 'evidence') {
        const owner = TOOL_OWNER[ev.tool] || ev.agent;
        addLine(owner, `↳ ${ev.summary}`);
        setEvidence(prev => [...prev, { ts: new Date().toTimeString().slice(0, 8), tool: ev.tool, summary: ev.summary }]);
      } else if (ev.type === 'agent_end') setWorker(ev.agent, { state: 'done' });
      else if (ev.type === 'done') {
        setWorkers(prev => {
          const next = { ...prev };
          ORDER.forEach(w => { if (next[w].state === 'working') next[w] = { ...next[w], state: 'done' }; });
          next.Verifier = { ...next.Verifier, state: 'waiting' };
          return next;
        });
        setRunning(false);
        es.close();
        loadState();
      }
    });
    es.onerror = () => { es.close(); setRunning(false); };
    esRef.current = es;
  };

  const approve = async (service: string) => {
    setBusy(p => ({ ...p, [service]: true }));
    try {
      await api.remediate(service);
      setWorker('Verifier', { state: 'working' });
      addLine('Verifier', `> verify(${service})`);
      const { verdict } = await api.verify(service);
      const ok = /CONFIRMED/i.test(verdict) && !/NOT CONFIRMED/i.test(verdict);
      addLine('Verifier', `↳ ${verdict}`);
      setEvidence(prev => [...prev, { ts: new Date().toTimeString().slice(0, 8), tool: 'verify', summary: verdict }]);
      setWorker('Verifier', { state: ok ? 'done' : 'waiting' });
      setVerdicts(p => ({ ...p, [service]: verdict }));
      loadState();
    } finally {
      setBusy(p => ({ ...p, [service]: false }));
    }
  };

  return (
    <div className="card p-5">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <Bot size={16} style={{ color: 'var(--accent-blue)' }} />
          <h3 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Multi-Agent Operations Center</h3>
        </div>
        <button className="btn-primary text-sm px-4 py-2" onClick={runIncident} disabled={running}>
          <Play size={13} /> {running ? 'Agents working…' : 'Run incident response'}
        </button>
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        {/* Agent workers */}
        <div>
          <p className="section-label mb-2">Agent Workers</p>
          <div className="space-y-2 max-h-[440px] overflow-y-auto pr-1">
            {WORKERS.map(([name]) => {
              const w = workers[name];
              return (
                <div key={name} className="rounded-lg p-3" style={{ background: 'var(--bg-elevated)', border: `1px solid ${w.state === 'working' ? 'rgba(59,130,246,0.4)' : 'var(--border-subtle)'}` }}>
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full" style={{ background: STAT_COLOR[w.state] }} />
                    <span className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>{name}</span>
                    <span className="ml-auto text-[10px] font-bold uppercase tracking-wide" style={{ color: STAT_COLOR[w.state] }}>{STAT_LABEL[w.state]}</span>
                  </div>
                  <div className="text-[11px] mt-0.5" style={{ color: 'var(--text-dim)' }}>{w.role}</div>
                  {w.terminal.length > 0 && (
                    <div className="mt-2 font-mono text-[10.5px] space-y-0.5" style={{ color: 'var(--text-muted)' }}>
                      {w.terminal.map((l, i) => (
                        <div key={i} style={{ color: l.startsWith('>') ? 'var(--accent-blue)' : 'var(--text-muted)' }}>{l}</div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Evidence feed */}
        <div>
          <p className="section-label mb-2">Evidence Feed</p>
          <div className="rounded-lg p-3 font-mono text-[11px] max-h-[440px] overflow-y-auto"
            style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)' }}>
            {evidence.length === 0 && <div style={{ color: 'var(--text-dim)' }}>Run an incident — every tool call and result streams here, timestamped.</div>}
            {evidence.map((e, i) => (
              <div key={i} className="flex gap-2 py-0.5">
                <span style={{ color: 'var(--text-dim)' }}>{e.ts}</span>
                <span style={{ color: 'var(--accent-blue)', fontWeight: 600 }}>{e.tool}()</span>
                <span className="truncate" style={{ color: 'var(--text-secondary)' }}>→ {e.summary}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Incidents + approve/verify */}
      <div className="mt-5">
        <p className="section-label mb-2">Active Incidents</p>
        {incidents.length === 0 && <p className="text-xs" style={{ color: 'var(--text-dim)' }}>No active incidents. Break a service in the legacy console, then Run incident response.</p>}
        <div className="space-y-2">
          {incidents.map((inc, i) => {
            const auto = inc.rootCause.confidence >= threshold;
            const svc = inc.rootCause.service;
            const verdict = verdicts[svc];
            const ok = verdict && /CONFIRMED/i.test(verdict) && !/NOT CONFIRMED/i.test(verdict);
            return (
              <div key={i} className="rounded-lg p-3" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)' }}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="badge badge-red">{inc.severity}</span>
                  <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>{inc.alertService}</span>
                  <span className="text-[11px] font-mono" style={{ color: 'var(--text-dim)' }}>
                    root: {svc} {inc.rootCause.version || ''} · conf {inc.rootCause.confidence.toFixed(2)} / gate {threshold.toFixed(2)}
                  </span>
                  <span className={`badge ${auto ? 'badge-green' : 'badge-amber'} ml-1`}>{auto ? '🤖 auto-remediate' : '🧑 escalate'}</span>
                  <button className="btn-primary text-xs ml-auto" disabled={busy[svc]} onClick={() => approve(svc)}>
                    <ShieldCheck size={12} /> {busy[svc] ? 'working…' : 'Approve & remediate'}
                  </button>
                </div>
                {verdict && (
                  <div className="text-[11px] mt-2 flex items-center gap-1.5" style={{ color: ok ? '#4ade80' : '#fcd34d' }}>
                    🔎 {verdict}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
