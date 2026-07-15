export type Severity = 'critical' | 'high' | 'medium' | 'low';
export type IncidentStatus =
  | 'pending' | 'new' | 'acknowledged'
  | 'detecting' | 'analyzing'
  | 'rca_in_progress' | 'rca_completed'
  | 'healing' | 'awaiting_approval'
  | 'resolved' | 'failed' | 'escalated' | 'closed' | 'cancelled';
export type AgentStatus = 'running' | 'stopped' | 'error';

export interface Alert {
  is_anomaly: boolean;
  confidence: number;
  algorithms_voted: string[];
}

export interface CandidateCause {
  cause: string;
  score: number;
}

export interface RCA {
  root_cause: string;
  confidence: number;
  impact_chain: string[];
  suggested_actions: string[];
  // 候选根因 Top-K(旧数据/context 兜底路径可能缺失,按可选处理)
  candidate_causes?: CandidateCause[];
}

export interface Heal {
  action: string;
  level: 'L0' | 'L1' | 'L2';
  dry_run_result: string;
  blast_radius: number;
}

export interface Change {
  approval_status: string;
  risk_score: number;
  approver?: string;
}

export interface Incident {
  id: string;
  service: string;
  metric: string;
  severity: Severity;
  status: IncidentStatus;
  alert?: Alert;
  rca?: RCA;
  heal?: Heal;
  change?: Change;
  created_at: string;
  updated_at: string;
}

export interface Agent {
  id: string;
  name: string;
  description: string;
  status: AgentStatus;
  last_activity: string;
  type?: string;
}

export interface EvalMetric {
  name: string;
  score: number;
  weight: number;
}

export interface EvaluationResult {
  id: string;
  dimension: string;
  overall_score: number;
  metrics: EvalMetric[];
  timestamp: string;
}

export interface MemoryEntry {
  id: string;
  key: string;
  value: string;
  type: 'incident' | 'playbook' | 'config' | 'knowledge';
  created_at: string;
  importance: number;
  tags?: string[];
}

export interface TopologyNode {
  id: string;
  name: string;
  service: string;
  status: 'healthy' | 'warning' | 'critical' | 'unknown';
  dependencies: string[];
  metrics?: {
    cpu: number;
    memory: number;
    latency: number;
    error_rate: number;
  };
}

export interface TopologyEdge {
  source: string;
  target: string;
  type: 'http' | 'rpc' | 'db' | 'mq';
  latency?: number;
}

export interface TopologyData {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

export interface WebSocketMessage {
  type: 'incident_update' | 'agent_status' | 'evaluation_result' | 'heartbeat';
  payload: Record<string, unknown>;
  timestamp: string;
}

export interface IncidentStats {
  total: number;
  resolved: number;
  processing: number;
  failed: number;
  avg_mttr: number;
}

export interface AgentStats {
  total: number;
  running: number;
  stopped: number;
  error: number;
}

export interface TrendPoint {
  timestamp: string;
  score: number;
  dimension: string;
}
