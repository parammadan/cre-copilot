export interface WorkspaceStatus {
  overall: 'READY' | 'DEGRADED' | 'OFFLINE' | string;
  adx: { connected?: boolean; latency_ms?: number; cluster?: string; database?: string; last_query?: string; error?: string };
  aoai: { connected?: boolean; deployment?: string; endpoint?: string; latency_ms?: number; last_request?: string; error?: string };
  collector: { running?: boolean; source?: string; poll_interval_sec?: number; monitored_services?: number; last_sync?: string | null; last_sync_age_sec?: number | null };
  services: { service: string; status: string; response_ms: number | null; checked: string }[];
  checked: string;
}

export interface SecurityItem {
  key: string;
  label: string;
  status: 'implemented' | 'dev' | 'planned' | string;
  detail: string;
}
export interface SecurityStatus {
  items: SecurityItem[];
  implemented: number;
  total: number;
  environment: string;
}

export interface Incident {
  alertService: string;
  severity: string;
  alertTime?: string;
  description?: string;
  rootCause: { service: string; version?: string; confidence: number };
  impact?: { AffectedService?: string; LatencyIncrease?: number }[];
}

export interface AppState {
  services: string[];
  health: Record<string, number>;
  threshold: number;
  incidents: Incident[];
  metrics: {
    total: number;
    autoResolved: number;
    escalated: number;
    mttr: Record<string, number>;
  };
}

export type StreamEvent =
  | { type: 'incident_start'; trace?: string; mode?: string }
  | { type: 'agent_start'; agent: string }
  | { type: 'token'; agent: string; text: string }
  | { type: 'tool_call'; agent: string; tool: string }
  | { type: 'evidence'; agent: string; tool: string; summary: string }
  | { type: 'agent_end'; agent: string }
  | { type: 'error'; message: string }
  | { type: 'done'; mode?: string };
