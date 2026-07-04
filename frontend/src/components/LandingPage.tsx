import { useState } from 'react';
import { ArrowRight, Shield, Zap, Activity, Bot, Network, Terminal, CheckCircle2, ChevronRight } from 'lucide-react';

interface Props {
  onLaunch: () => void;
}

const FEATURES = [
  { icon: Bot, title: 'Multi-Agent Investigation', desc: '7 specialized agents — Commander, Detector, Correlator, Impact, Gate, Runbook, Verifier — collaborate on every incident.' },
  { icon: Terminal, title: 'Evidence-First Timeline', desc: 'KQL queries, health checks, logs, and deployments stitched into a chronological timeline. Not charts — proof.' },
  { icon: Shield, title: 'Deterministic Confidence Gate', desc: 'A Python confidence.py sets the threshold. The LLM never bypasses the gate. Auto-remediation only above 0.70.' },
  { icon: Network, title: 'Service Topology', desc: 'Interactive dependency graph. Click any service for health, metrics, logs, deployments, and recent investigations.' },
  { icon: Zap, title: 'Human-in-the-Loop Recovery', desc: 'Read-only investigation. Remediation is a separate human-triggered endpoint. Verifier confirms independently.' },
  { icon: Activity, title: 'Live Telemetry', desc: 'Live KQL against Azure Data Explorer. 12-second sync. Real signals, not sampled approximations.' },
];

const STEPS = [
  { num: '01', title: 'Detect', desc: 'Detector agent runs KQL anomaly queries against ADX. Identifies signal vs noise in seconds.' },
  { num: '02', title: 'Correlate', desc: 'Correlator joins deployment history, config changes, and dependency signals to isolate root cause.' },
  { num: '03', title: 'Gate', desc: 'Deterministic confidence calculation. Below 0.70 — human approval required. Above — auto-remediation eligible.' },
  { num: '04', title: 'Recover', desc: 'Runbook agent executes approved playbooks. Verifier confirms service health restored. Postmortem auto-drafted.' },
];

const BULLET_POINTS = [
  'Get to root cause 80% faster with multi-agent AI investigation',
  'Evidence-first timeline — not charts, not alerts, proof',
  'Deterministic gate blocks auto-remediation below confidence 0.70',
  'Human-in-the-loop for every remediation action',
  'Full audit logging and structured postmortems auto-generated',
];

interface DemoFormState {
  firstName: string;
  lastName: string;
  workEmail: string;
  role: string;
  teamSize: string;
  submitted: boolean;
}

function DemoForm() {
  const [form, setForm] = useState<DemoFormState>({
    firstName: '', lastName: '', workEmail: '', role: '', teamSize: '', submitted: false,
  });

  const set = (k: keyof DemoFormState) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm(prev => ({ ...prev, [k]: e.target.value }));

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setForm(prev => ({ ...prev, submitted: true }));
  };

  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '9px 12px',
    background: '#ffffff',
    border: '1px solid #d4d4d8',
    borderRadius: 6,
    fontSize: 13,
    color: '#18181b',
    fontFamily: 'inherit',
    outline: 'none',
    transition: 'border-color 0.15s',
  };

  const labelStyle: React.CSSProperties = {
    display: 'block',
    fontSize: 12,
    fontWeight: 500,
    color: '#3f3f46',
    marginBottom: 5,
  };

  if (form.submitted) {
    return (
      <div className="flex flex-col items-center justify-center h-full py-10 text-center gap-4">
        <div
          className="w-12 h-12 rounded-full flex items-center justify-center"
          style={{ background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.3)' }}
        >
          <CheckCircle2 size={22} style={{ color: '#22c55e' }} />
        </div>
        <h3 className="text-lg font-semibold" style={{ color: '#18181b' }}>You're on the list.</h3>
        <p className="text-sm" style={{ color: '#71717a', lineHeight: 1.6 }}>
          We'll reach out shortly to schedule your demo.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label style={labelStyle}>First name <span style={{ color: '#ef4444' }}>*</span></label>
          <input required style={inputStyle} value={form.firstName} onChange={set('firstName')}
            onFocus={e => e.target.style.borderColor = '#3b82f6'}
            onBlur={e => e.target.style.borderColor = '#d4d4d8'}
          />
        </div>
        <div>
          <label style={labelStyle}>Last name <span style={{ color: '#ef4444' }}>*</span></label>
          <input required style={inputStyle} value={form.lastName} onChange={set('lastName')}
            onFocus={e => e.target.style.borderColor = '#3b82f6'}
            onBlur={e => e.target.style.borderColor = '#d4d4d8'}
          />
        </div>
      </div>

      <div>
        <label style={labelStyle}>Work email <span style={{ color: '#ef4444' }}>*</span></label>
        <input required type="email" style={inputStyle} value={form.workEmail} onChange={set('workEmail')}
          onFocus={e => e.target.style.borderColor = '#3b82f6'}
          onBlur={e => e.target.style.borderColor = '#d4d4d8'}
        />
      </div>

      <div>
        <label style={labelStyle}>Your role <span style={{ color: '#ef4444' }}>*</span></label>
        <select required style={{ ...inputStyle, cursor: 'pointer' }} value={form.role} onChange={set('role')}>
          <option value="">Please select one...</option>
          <option>SRE / Platform Engineer</option>
          <option>Engineering Manager</option>
          <option>CRE / Reliability Lead</option>
          <option>DevOps Engineer</option>
          <option>VP / Director Engineering</option>
          <option>Other</option>
        </select>
      </div>

      <div>
        <label style={labelStyle}>Team size</label>
        <select style={{ ...inputStyle, cursor: 'pointer' }} value={form.teamSize} onChange={set('teamSize')}>
          <option value="">Please select one...</option>
          <option>1–10</option>
          <option>11–50</option>
          <option>51–200</option>
          <option>200+</option>
        </select>
      </div>

      <button
        type="submit"
        className="w-full py-3 rounded-lg text-sm font-semibold transition-all"
        style={{ background: '#18181b', color: '#ffffff', border: 'none', cursor: 'pointer', fontFamily: 'inherit', letterSpacing: '-0.01em' }}
        onMouseEnter={e => e.currentTarget.style.background = '#27272a'}
        onMouseLeave={e => e.currentTarget.style.background = '#18181b'}
      >
        Book a demo
      </button>

      <p className="text-[11px] text-center" style={{ color: '#a1a1aa', lineHeight: 1.5 }}>
        By submitting, you agree to our Privacy Policy and Terms of Service.
      </p>
    </form>
  );
}

export default function LandingPage({ onLaunch }: Props) {
  return (
    <div className="min-h-screen" style={{ background: 'var(--bg-base)' }}>
      {/* Nav */}
      <header className="sticky top-0 z-30" style={{ background: 'rgba(10,10,11,0.85)', backdropFilter: 'blur(12px)', borderBottom: '1px solid var(--border-subtle)' }}>
        <div className="max-w-6xl mx-auto flex items-center justify-between px-6 py-4">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-md flex items-center justify-center" style={{ background: '#1d4ed8' }}>
              <Activity size={14} color="white" strokeWidth={2.5} />
            </div>
            <span className="font-semibold text-sm" style={{ color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>CRE Copilot</span>
            <span className="badge badge-blue ml-2">Beta</span>
          </div>

          <nav className="hidden md:flex items-center gap-7">
            <a href="#features" className="text-xs transition-opacity hover:opacity-100 opacity-70" style={{ color: 'var(--text-primary)' }}>Features</a>
            <a href="#how" className="text-xs transition-opacity hover:opacity-100 opacity-70" style={{ color: 'var(--text-primary)' }}>How it works</a>
            <a href="#security" className="text-xs transition-opacity hover:opacity-100 opacity-70" style={{ color: 'var(--text-primary)' }}>Security</a>
          </nav>

          <div className="flex items-center gap-3">
            <button className="btn-ghost text-xs">Sign in</button>
            <button className="btn-primary text-xs" onClick={onLaunch}>
              Launch Workspace <ArrowRight size={13} />
            </button>
          </div>
        </div>
      </header>

      {/* Hero — two-column split */}
      <section className="relative overflow-hidden">
        {/* Subtle grid */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            backgroundImage: `linear-gradient(var(--border-subtle) 1px, transparent 1px), linear-gradient(90deg, var(--border-subtle) 1px, transparent 1px)`,
            backgroundSize: '56px 56px',
            maskImage: 'radial-gradient(ellipse 90% 70% at 30% 0%, black 30%, transparent 100%)',
            WebkitMaskImage: 'radial-gradient(ellipse 90% 70% at 30% 0%, black 30%, transparent 100%)',
            opacity: 0.45,
          }}
        />
        {/* Glow */}
        <div
          className="absolute pointer-events-none"
          style={{
            top: -160, left: '20%',
            width: 500, height: 400,
            background: 'radial-gradient(ellipse, rgba(29,78,216,0.12), transparent 70%)',
            filter: 'blur(60px)',
          }}
        />

        <div className="relative max-w-6xl mx-auto px-6 py-20 grid md:grid-cols-2 gap-16 items-start">
          {/* Left — value prop */}
          <div className="animate-fade-in">
            {/* Status pill */}
            <div
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full mb-8"
              style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)' }}
            >
              <span className="status-dot active" />
              <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
                AI Reliability Workspace
              </span>
              <ChevronRight size={12} style={{ color: 'var(--text-dim)' }} />
              <span className="text-xs" style={{ color: 'var(--accent-blue)' }}>Now in beta</span>
            </div>

            <h1
              className="text-4xl md:text-5xl font-semibold mb-5"
              style={{ color: 'var(--text-primary)', letterSpacing: '-0.03em', lineHeight: 1.08 }}
            >
              You deserve modern<br />
              <span style={{
                background: 'linear-gradient(90deg, #93c5fd, #3b82f6)',
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
              }}>
                incident reliability.
              </span>
            </h1>

            <p
              className="text-base mb-8"
              style={{ color: 'var(--text-secondary)', lineHeight: 1.7, maxWidth: 440 }}
            >
              Built for fast-moving engineering teams to detect, investigate,
              and resolve incidents faster — with AI agents and human approval.
            </p>

            {/* Bullets */}
            <ul className="space-y-3 mb-10">
              {BULLET_POINTS.map(pt => (
                <li key={pt} className="flex items-start gap-3">
                  <CheckCircle2 size={15} style={{ color: 'var(--accent-blue)', flexShrink: 0, marginTop: 1 }} />
                  <span className="text-sm" style={{ color: 'var(--text-secondary)', lineHeight: 1.5 }}>{pt}</span>
                </li>
              ))}
            </ul>

            <div className="flex items-center gap-3">
              <button className="btn-primary text-sm px-5 py-2.5" onClick={onLaunch}>
                Launch Workspace <ArrowRight size={14} />
              </button>
            </div>
          </div>

          {/* Right — demo form */}
          <div
            className="rounded-xl p-7 animate-fade-in delay-200"
            style={{
              background: '#ffffff',
              boxShadow: '0 8px 40px rgba(0,0,0,0.35), 0 0 0 1px rgba(255,255,255,0.06)',
            }}
          >
            <h2
              className="text-xl font-semibold mb-1"
              style={{ color: '#18181b', letterSpacing: '-0.02em' }}
            >
              Learn how CRE Copilot can<br />help you and your teams.
            </h2>
            <p className="text-sm mb-6" style={{ color: '#71717a' }}>
              Book a personalized demo — 30 minutes.
            </p>
            <DemoForm />
          </div>
        </div>
      </section>

      {/* Product mockup */}
      <section className="relative max-w-5xl mx-auto px-6 pb-24">
        <div
          className="rounded-xl overflow-hidden animate-fade-in delay-300"
          style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-default)', boxShadow: '0 20px 60px rgba(0,0,0,0.5)' }}
        >
          {/* Window chrome */}
          <div className="flex items-center gap-2 px-4 py-3" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
            <div className="flex gap-1.5">
              <div className="w-2.5 h-2.5 rounded-full" style={{ background: '#ef4444' }} />
              <div className="w-2.5 h-2.5 rounded-full" style={{ background: '#f59e0b' }} />
              <div className="w-2.5 h-2.5 rounded-full" style={{ background: '#22c55e' }} />
            </div>
            <span className="text-xs ml-3 font-mono" style={{ color: 'var(--text-dim)' }}>
              cre-copilot.app/workspace/incidents/INC-2849
            </span>
          </div>
          {/* Mock content */}
          <div className="p-5">
            <div className="flex items-center gap-2 mb-4">
              <span className="badge badge-red">P2</span>
              <span className="text-xs font-mono" style={{ color: 'var(--text-muted)' }}>INC-2849</span>
              <span className="badge badge-amber ml-1">investigating</span>
              <span className="ml-auto text-xs font-mono" style={{ color: 'var(--text-dim)' }}>conf 0.67 · gate 0.70</span>
            </div>
            <p className="text-sm font-medium mb-4" style={{ color: 'var(--text-primary)' }}>
              auth-service p99 latency degradation
            </p>
            <div className="space-y-2">
              {[
                { ts: '10:41:02', label: 'Anomaly detected — p99 284ms (5.9x baseline)', type: 'KQL', color: '#3b82f6' },
                { ts: '10:41:08', label: 'Health check failed — connection pool 50/50', type: 'Health', color: '#22c55e' },
                { ts: '10:41:15', label: '847 requests queued in retry buffer', type: 'Logs', color: '#f59e0b' },
                { ts: '10:38:00', label: 'Deployment v1.9.3 — timeout 5s → 30s', type: 'Deploy', color: '#a78bfa' },
              ].map((e, i) => (
                <div key={i} className="flex items-center gap-3 py-2.5 px-3 rounded-md" style={{ background: 'var(--bg-elevated)' }}>
                  <div
                    className="w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0"
                    style={{ border: `1.5px solid ${e.color}` }}
                  >
                    <div className="w-1.5 h-1.5 rounded-full" style={{ background: e.color }} />
                  </div>
                  <span className="text-[10px] font-mono" style={{ color: 'var(--text-dim)' }}>{e.ts}</span>
                  <span className="badge badge-neutral text-[10px]" style={{ color: e.color, borderColor: `${e.color}33` }}>
                    {e.type}
                  </span>
                  <span className="text-xs flex-1" style={{ color: 'var(--text-secondary)' }}>{e.label}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="max-w-6xl mx-auto px-6 py-20">
        <div className="text-center mb-14">
          <p className="section-label mb-3">Features</p>
          <h2 className="text-3xl font-semibold mb-3" style={{ color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
            Everything an SRE needs. Nothing they don't.
          </h2>
          <p className="text-sm max-w-lg mx-auto" style={{ color: 'var(--text-muted)', lineHeight: 1.6 }}>
            A purpose-built reliability workspace — not a dashboard with AI bolted on.
          </p>
        </div>
        <div className="grid md:grid-cols-3 gap-4">
          {FEATURES.map((f, i) => {
            const Icon = f.icon;
            return (
              <div
                key={f.title}
                className="card p-5 animate-fade-in transition-all"
                style={{ animationDelay: `${i * 0.08}s`, opacity: 0 }}
                onMouseEnter={e => e.currentTarget.style.borderColor = 'var(--border-default)'}
                onMouseLeave={e => e.currentTarget.style.borderColor = 'var(--border-subtle)'}
              >
                <div
                  className="w-10 h-10 rounded-lg flex items-center justify-center mb-4"
                  style={{ background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.2)' }}
                >
                  <Icon size={18} style={{ color: 'var(--accent-blue)' }} />
                </div>
                <h3 className="text-base font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>{f.title}</h3>
                <p className="text-sm" style={{ color: 'var(--text-muted)', lineHeight: 1.6 }}>{f.desc}</p>
              </div>
            );
          })}
        </div>
      </section>

      {/* How it works */}
      <section id="how" className="max-w-6xl mx-auto px-6 py-20" style={{ borderTop: '1px solid var(--border-subtle)' }}>
        <div className="text-center mb-14">
          <p className="section-label mb-3">How it works</p>
          <h2 className="text-3xl font-semibold mb-3" style={{ color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
            From detection to recovery in four steps
          </h2>
        </div>
        <div className="grid md:grid-cols-4 gap-8">
          {STEPS.map((s, i) => (
            <div key={s.num} className="relative animate-fade-in" style={{ animationDelay: `${i * 0.1}s`, opacity: 0 }}>
              {i < 3 && (
                <div className="hidden md:block absolute top-5 -right-4 z-0">
                  <ChevronRight size={18} style={{ color: 'var(--border-default)' }} />
                </div>
              )}
              <div className="relative z-10">
                <span className="text-3xl font-semibold font-mono block mb-3" style={{ color: 'var(--accent-blue)', opacity: 0.45 }}>
                  {s.num}
                </span>
                <h3 className="text-base font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>{s.title}</h3>
                <p className="text-sm" style={{ color: 'var(--text-muted)', lineHeight: 1.6 }}>{s.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Security */}
      <section id="security" className="py-20" style={{ borderTop: '1px solid var(--border-subtle)', borderBottom: '1px solid var(--border-subtle)' }}>
        <div className="max-w-4xl mx-auto px-6 text-center">
          <div
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full mb-6"
            style={{ background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.2)' }}
          >
            <Shield size={13} style={{ color: 'var(--accent-green)' }} />
            <span className="text-xs font-medium" style={{ color: '#4ade80' }}>Zero-trust by design</span>
          </div>
          <h2 className="text-3xl font-semibold mb-4" style={{ color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
            Security is not a feature.<br />It's the architecture.
          </h2>
          <p className="text-sm max-w-xl mx-auto mb-10" style={{ color: 'var(--text-muted)', lineHeight: 1.7 }}>
            Managed identity, least-privilege RBAC, read-only investigation tools, deterministic gates,
            human approval for remediation, and full audit logging. Every decision is traceable.
          </p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {['Managed Identity', 'Key Vault', 'Read-Only Tools', 'Deterministic Gate', 'Human Approval', 'RBAC', 'Audit Logging', 'Zero-Trust'].map(s => (
              <div
                key={s}
                className="flex items-center gap-2 px-3 py-2.5 rounded-lg justify-center"
                style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)' }}
              >
                <CheckCircle2 size={13} style={{ color: 'var(--accent-green)' }} />
                <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{s}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Final CTA */}
      <section className="max-w-3xl mx-auto px-6 py-24 text-center">
        <h2 className="text-4xl font-semibold mb-4" style={{ color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
          Stop fighting incidents.<br />Start resolving them.
        </h2>
        <p className="text-sm mb-8" style={{ color: 'var(--text-muted)' }}>
          Launch the demo workspace — no setup required.
        </p>
        <button className="btn-primary text-base px-8 py-3" onClick={onLaunch}>
          Launch Workspace <ArrowRight size={16} />
        </button>
      </section>

      {/* Footer */}
      <footer className="py-10" style={{ borderTop: '1px solid var(--border-subtle)' }}>
        <div className="max-w-6xl mx-auto px-6 flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <div className="w-6 h-6 rounded-md flex items-center justify-center" style={{ background: '#1d4ed8' }}>
              <Activity size={12} color="white" strokeWidth={2.5} />
            </div>
            <span className="text-sm font-semibold" style={{ color: 'var(--text-secondary)' }}>CRE Copilot</span>
            <span className="text-xs ml-2" style={{ color: 'var(--text-dim)' }}>AI Reliability Workspace</span>
          </div>
          <div className="flex items-center gap-6">
            <a href="#features" className="text-xs hover:opacity-80 transition-opacity" style={{ color: 'var(--text-muted)' }}>Features</a>
            <a href="#how" className="text-xs hover:opacity-80 transition-opacity" style={{ color: 'var(--text-muted)' }}>How it works</a>
            <a href="#security" className="text-xs hover:opacity-80 transition-opacity" style={{ color: 'var(--text-muted)' }}>Security</a>
          </div>
          <p className="text-xs" style={{ color: 'var(--text-dim)' }}>
            Built by <span style={{ color: 'var(--text-secondary)', fontWeight: 500 }}>Madan</span> · © 2026 CRE Copilot
          </p>
        </div>
      </footer>
    </div>
  );
}
