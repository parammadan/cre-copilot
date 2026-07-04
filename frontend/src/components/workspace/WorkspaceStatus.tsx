import { useEffect, useState } from 'react';
import { Activity } from 'lucide-react';
import { api } from '../../lib/api';
import type { WorkspaceStatus as WS } from '../../lib/types';

const OVERALL_STYLE: Record<string, { color: string; bg: string; border: string }> = {
  READY: { color: '#4ade80', bg: 'rgba(34,197,94,0.1)', border: 'rgba(34,197,94,0.35)' },
  DEGRADED: { color: '#fcd34d', bg: 'rgba(245,158,11,0.1)', border: 'rgba(245,158,11,0.35)' },
  OFFLINE: { color: '#fca5a5', bg: 'rgba(239,68,68,0.1)', border: 'rgba(239,68,68,0.35)' },
};

function led(color: string) {
  return <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: color }} />;
}

function svcColor(status: string) {
  return status === 'healthy' ? '#22c55e' : status === 'degraded' ? '#e0a915' : '#e5484d';
}

export default function WorkspaceStatus() {
  const [d, setD] = useState<WS | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => api.workspaceStatus().then(x => alive && setD(x)).catch(() => alive && setErr(true));
    load();
    const t = setInterval(load, 20000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const ov = d ? (OVERALL_STYLE[d.overall] ?? OVERALL_STYLE.DEGRADED) : null;
  const tile = 'rounded-lg p-3';
  const tileStyle = { background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)' } as const;

  return (
    <div className="card p-5">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <Activity size={16} style={{ color: 'var(--accent-blue)' }} />
          <h3 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Workspace Status</h3>
        </div>
        {d && ov && (
          <span className="text-xs font-bold px-2.5 py-1 rounded-full"
            style={{ color: ov.color, background: ov.bg, border: `1px solid ${ov.border}` }}>
            {d.overall}
          </span>
        )}
      </div>
      <p className="text-xs mb-4" style={{ color: 'var(--text-dim)' }}>
        Live backend checks — ADX, Azure OpenAI, collector, microservice /health. Nothing hardcoded.
      </p>

      {err && <p className="text-xs" style={{ color: '#fca5a5' }}>status endpoint unreachable</p>}
      {!d && !err && <p className="text-xs" style={{ color: 'var(--text-dim)' }}>checking…</p>}

      {d && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className={tile} style={tileStyle}>
            <div className="flex items-center gap-2 mb-2">
              {led(d.adx.connected ? '#22c55e' : '#e5484d')}
              <span className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>Azure Data Explorer</span>
              <span className="ml-auto text-[11px]" style={{ color: 'var(--text-dim)' }}>{d.adx.connected ? 'Connected' : 'Disconnected'}</span>
            </div>
            {d.adx.connected ? (
              <div className="text-[11px] font-mono space-y-0.5" style={{ color: 'var(--text-muted)' }}>
                <div>latency {d.adx.latency_ms} ms</div>
                <div>{d.adx.cluster}</div>
              </div>
            ) : <div className="text-[11px]" style={{ color: '#fca5a5' }}>{d.adx.error || 'unreachable'}</div>}
          </div>

          <div className={tile} style={tileStyle}>
            <div className="flex items-center gap-2 mb-2">
              {led(d.aoai.connected ? '#22c55e' : '#e5484d')}
              <span className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>Azure OpenAI</span>
              <span className="ml-auto text-[11px]" style={{ color: 'var(--text-dim)' }}>{d.aoai.connected ? 'Connected' : 'Disconnected'}</span>
            </div>
            <div className="text-[11px] font-mono space-y-0.5" style={{ color: 'var(--text-muted)' }}>
              <div>{d.aoai.deployment}</div>
              {d.aoai.connected && <div>latency {d.aoai.latency_ms} ms</div>}
            </div>
          </div>

          <div className={tile} style={tileStyle}>
            <div className="flex items-center gap-2 mb-2">
              {led(d.collector.running ? '#22c55e' : (d.collector.last_sync ? '#e0a915' : '#e5484d'))}
              <span className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>Telemetry Collector</span>
              <span className="ml-auto text-[11px]" style={{ color: 'var(--text-dim)' }}>{d.collector.running ? 'Running' : 'Idle'}</span>
            </div>
            <div className="text-[11px] font-mono space-y-0.5" style={{ color: 'var(--text-muted)' }}>
              <div>{d.collector.source} · {d.collector.monitored_services} svcs · {d.collector.poll_interval_sec}s</div>
              <div>last sync {d.collector.last_sync ?? '—'}</div>
            </div>
          </div>

          <div className={tile} style={tileStyle}>
            <div className="flex items-center gap-2 mb-2">
              {led((d.services.every(s => s.status === 'healthy') && d.services.length) ? '#22c55e' : '#e0a915')}
              <span className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>Microservice Lab</span>
              <span className="ml-auto text-[11px]" style={{ color: 'var(--text-dim)' }}>
                {d.services.filter(s => s.status === 'healthy').length}/{d.services.length} healthy
              </span>
            </div>
            <div className="space-y-1">
              {d.services.map(s => (
                <div key={s.service} className="flex items-center gap-2 text-[11px]">
                  {led(svcColor(s.status))}
                  <span style={{ color: 'var(--text-muted)' }}>{s.service}</span>
                  <span className="ml-auto font-mono" style={{ color: 'var(--text-dim)' }}>
                    {s.response_ms != null ? `${s.response_ms} ms` : s.status}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      {d && <p className="text-[10px] mt-3" style={{ color: 'var(--text-dim)' }}>Last checked {d.checked} · auto-refreshes every 20s</p>}
    </div>
  );
}
