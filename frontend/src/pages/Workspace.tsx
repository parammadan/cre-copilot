import { Link } from 'react-router-dom';
import { Activity, ExternalLink, Network, SlidersHorizontal, MessageSquare, Send } from 'lucide-react';
import { LEGACY_URL } from '../lib/api';
import WorkspaceStatus from '../components/workspace/WorkspaceStatus';
import SecurityCard from '../components/workspace/SecurityCard';
import OpsCenter from '../components/workspace/OpsCenter';

const LEGACY_FEATURES = [
  { icon: Network, label: 'Service Topology' },
  { icon: SlidersHorizontal, label: 'Sandbox Controls' },
  { icon: MessageSquare, label: 'Ask CRE Copilot' },
  { icon: Send, label: 'Post to Teams' },
];

export default function Workspace() {
  return (
    <div className="min-h-screen" style={{ background: 'var(--bg-base)' }}>
      {/* Header */}
      <header className="sticky top-0 z-30" style={{ background: 'rgba(10,10,11,0.85)', backdropFilter: 'blur(12px)', borderBottom: '1px solid var(--border-subtle)' }}>
        <div className="max-w-6xl mx-auto flex items-center justify-between px-6 py-4">
          <Link to="/" className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-md flex items-center justify-center" style={{ background: '#1d4ed8' }}>
              <Activity size={14} color="white" strokeWidth={2.5} />
            </div>
            <span className="font-semibold text-sm" style={{ color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>CRE Copilot</span>
            <span className="badge badge-neutral ml-1">Workspace</span>
          </Link>
          <div className="flex items-center gap-3">
            <a href={LEGACY_URL} className="btn-ghost text-xs" target="_blank" rel="noreferrer">
              Legacy console <ExternalLink size={12} />
            </a>
            <Link to="/" className="btn-ghost text-xs">Home</Link>
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8 space-y-5">
        <div>
          <h1 className="text-xl font-semibold" style={{ color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>Reliability Workspace</h1>
          <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
            Live incident response — detection, evidence-based investigation, deterministic gate, human approval, verification.
          </p>
        </div>

        <WorkspaceStatus />
        <SecurityCard />
        <OpsCenter />

        {/* Legacy features (not yet ported to React) */}
        <div className="card p-5">
          <p className="section-label mb-2">More tools</p>
          <p className="text-xs mb-3" style={{ color: 'var(--text-dim)' }}>
            These panels live in the legacy console for now — they open in a new tab.
          </p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {LEGACY_FEATURES.map(f => {
              const Icon = f.icon;
              return (
                <a key={f.label} href={LEGACY_URL} target="_blank" rel="noreferrer"
                  className="flex items-center gap-2 px-3 py-2.5 rounded-lg"
                  style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)', color: 'var(--text-secondary)' }}>
                  <Icon size={14} style={{ color: 'var(--accent-blue)' }} />
                  <span className="text-xs">{f.label}</span>
                  <ExternalLink size={11} className="ml-auto" style={{ color: 'var(--text-dim)' }} />
                </a>
              );
            })}
          </div>
        </div>
      </main>

      <footer className="py-8" style={{ borderTop: '1px solid var(--border-subtle)' }}>
        <div className="max-w-6xl mx-auto px-6 text-xs" style={{ color: 'var(--text-dim)' }}>
          Demo workspace · synthetic telemetry / local microservice lab · no production systems connected.
        </div>
      </footer>
    </div>
  );
}
