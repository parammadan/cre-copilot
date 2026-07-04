import { useEffect, useState } from 'react';
import { Shield, Check, Circle } from 'lucide-react';
import { api } from '../../lib/api';
import type { SecurityStatus } from '../../lib/types';

export default function SecurityCard() {
  const [d, setD] = useState<SecurityStatus | null>(null);

  useEffect(() => {
    let alive = true;
    api.securityStatus().then(x => alive && setD(x)).catch(() => {});
    return () => { alive = false; };
  }, []);

  const mark = (status: string) => {
    if (status === 'implemented') return { color: '#22c55e', bg: 'rgba(34,197,94,0.12)', icon: <Check size={11} /> };
    if (status === 'dev') return { color: '#e0a915', bg: 'rgba(224,169,21,0.12)', icon: <Circle size={9} /> };
    return { color: 'var(--text-dim)', bg: 'rgba(150,150,160,0.12)', icon: <Circle size={9} /> };
  };
  const label = (i: SecurityStatus['items'][number]) =>
    i.status === 'planned' ? `${i.label} — Planned` : i.status === 'dev' ? `${i.label} (dev: .env)` : i.label;

  return (
    <div className="card p-5">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <Shield size={16} style={{ color: 'var(--accent-green)' }} />
          <h3 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Security &amp; Governance</h3>
        </div>
        {d && (
          <span className="text-xs font-bold px-2.5 py-1 rounded-full"
            style={{ color: '#4ade80', background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.35)' }}>
            {d.implemented}/{d.total} · {d.environment}
          </span>
        )}
      </div>
      <p className="text-xs mb-4" style={{ color: 'var(--text-dim)' }}>
        Real posture — Microsoft-style patterns. Items show <b>Planned</b> if not implemented.
      </p>

      {!d && <p className="text-xs" style={{ color: 'var(--text-dim)' }}>loading…</p>}

      {d && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {d.items.map(i => {
            const m = mark(i.status);
            return (
              <div key={i.key} className="flex items-start gap-2.5 rounded-lg p-2.5"
                style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)' }}>
                <span className="w-4 h-4 rounded flex items-center justify-center flex-shrink-0 mt-0.5"
                  style={{ color: m.color, background: m.bg }}>{m.icon}</span>
                <div>
                  <div className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>{label(i)}</div>
                  <div className="text-[11px] mt-0.5" style={{ color: 'var(--text-dim)', lineHeight: 1.45 }}>{i.detail}</div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
