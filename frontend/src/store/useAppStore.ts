import { create } from 'zustand';
import type {
  Incident,
  Agent,
  EvaluationResult,
  MemoryEntry,
  TopologyData,
  IncidentStats,
  AgentStats,
} from '@/types';

interface AppState {
  // Incidents
  incidents: Incident[];
  selectedIncident: Incident | null;
  setIncidents: (incidents: Incident[]) => void;
  setSelectedIncident: (incident: Incident | null) => void;
  updateIncident: (incident: Incident) => void;

  // Agents
  agents: Agent[];
  agentStats: AgentStats;
  setAgents: (agents: Agent[]) => void;
  updateAgent: (agent: Agent) => void;

  // Evaluations
  evaluations: EvaluationResult[];
  setEvaluations: (evaluations: EvaluationResult[]) => void;

  // Memory
  memories: MemoryEntry[];
  setMemories: (memories: MemoryEntry[]) => void;

  // Topology
  topology: TopologyData | null;
  setTopology: (topology: TopologyData) => void;

  // Stats
  incidentStats: IncidentStats;
  setIncidentStats: (stats: IncidentStats) => void;

  // WebSocket
  wsConnected: boolean;
  setWsConnected: (connected: boolean) => void;

  // UI
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  // Incidents
  incidents: [
    {
      id: 'INC-2024-001',
      service: 'user-service',
      metric: 'p99_latency',
      severity: 'critical',
      status: 'healing',
      alert: {
        is_anomaly: true,
        confidence: 0.95,
        algorithms_voted: ['isolation_forest', 'lstm', 'threshold'],
      },
      rca: {
        root_cause: '数据库连接池耗尽，慢查询导致线程阻塞',
        confidence: 0.92,
        impact_chain: ['db-proxy', 'order-service', 'payment-service'],
        suggested_actions: ['扩容连接池', ' kill慢查询', '开启读写分离'],
      },
      heal: {
        action: 'restart_db_proxy',
        level: 'L1',
        dry_run_result: 'success',
        blast_radius: 0.15,
      },
      change: {
        approval_status: 'pending',
        risk_score: 35,
      },
      created_at: '2024-01-15T08:30:00Z',
      updated_at: '2024-01-15T08:45:00Z',
    },
    {
      id: 'INC-2024-002',
      service: 'order-service',
      metric: 'error_rate',
      severity: 'high',
      status: 'analyzing',
      alert: {
        is_anomaly: true,
        confidence: 0.88,
        algorithms_voted: ['lstm', 'prophet'],
      },
      created_at: '2024-01-15T09:00:00Z',
      updated_at: '2024-01-15T09:15:00Z',
    },
    {
      id: 'INC-2024-003',
      service: 'payment-service',
      metric: 'throughput',
      severity: 'medium',
      status: 'detecting',
      created_at: '2024-01-15T09:30:00Z',
      updated_at: '2024-01-15T09:30:00Z',
    },
    {
      id: 'INC-2024-004',
      service: 'inventory-service',
      metric: 'cpu_usage',
      severity: 'low',
      status: 'resolved',
      heal: {
        action: 'scale_up',
        level: 'L0',
        dry_run_result: 'success',
        blast_radius: 0.05,
      },
      change: {
        approval_status: 'approved',
        risk_score: 10,
        approver: 'auto',
      },
      created_at: '2024-01-15T07:00:00Z',
      updated_at: '2024-01-15T07:20:00Z',
    },
    {
      id: 'INC-2024-005',
      service: 'notification-service',
      metric: 'memory_usage',
      severity: 'high',
      status: 'pending',
      created_at: '2024-01-15T10:00:00Z',
      updated_at: '2024-01-15T10:00:00Z',
    },
  ],
  selectedIncident: null,
  setIncidents: (incidents) => set({ incidents }),
  setSelectedIncident: (selectedIncident) => set({ selectedIncident }),
  updateIncident: (incident) =>
    set((state) => ({
      incidents: state.incidents.map((i) =>
        i.id === incident.id ? incident : i
      ),
    })),

  // Agents
  agents: [
    {
      id: 'monitor-agent',
      name: 'MonitorAgent',
      description: '实时监控系统指标，检测异常并触发告警',
      status: 'running',
      last_activity: '2024-01-15T10:05:00Z',
      type: 'detection',
    },
    {
      id: 'rca-agent',
      name: 'RCAAgent',
      description: '执行根因分析，定位故障源头和影响链',
      status: 'running',
      last_activity: '2024-01-15T09:20:00Z',
      type: 'analysis',
    },
    {
      id: 'heal-agent',
      name: 'HealAgent',
      description: '匹配合适的Playbook并执行恢复操作',
      status: 'running',
      last_activity: '2024-01-15T08:45:00Z',
      type: 'recovery',
    },
    {
      id: 'change-agent',
      name: 'ChangeAgent',
      description: '管理变更审批流程，评估变更风险',
      status: 'running',
      last_activity: '2024-01-15T08:50:00Z',
      type: 'approval',
    },
    {
      id: 'knowledge-agent',
      name: 'KnowledgeAgent',
      description: '维护运维知识库，提供历史经验参考',
      status: 'running',
      last_activity: '2024-01-15T08:00:00Z',
      type: 'knowledge',
    },
    {
      id: 'eval-agent',
      name: 'EvalAgent',
      description: '执行多维度评估，持续优化系统性能',
      status: 'running',
      last_activity: '2024-01-14T22:00:00Z',
      type: 'evaluation',
    },
  ],
  agentStats: { total: 6, running: 6, stopped: 0, error: 0 },
  setAgents: (agents) => set({ agents }),
  updateAgent: (agent) =>
    set((state) => ({
      agents: state.agents.map((a) => (a.id === agent.id ? agent : a)),
    })),

  // Evaluations
  evaluations: [
    {
      id: 'EVAL-001',
      dimension: 'end_to_end',
      overall_score: 87.5,
      metrics: [
        { name: '任务成功率', score: 92, weight: 0.4 },
        { name: 'MTTR', score: 85, weight: 0.3 },
        { name: '自动化率', score: 82, weight: 0.3 },
      ],
      timestamp: '2024-01-15T00:00:00Z',
    },
    {
      id: 'EVAL-002',
      dimension: 'reasoning',
      overall_score: 91.2,
      metrics: [
        { name: '根因准确率', score: 94, weight: 0.5 },
        { name: '置信度校准', score: 88, weight: 0.5 },
      ],
      timestamp: '2024-01-14T00:00:00Z',
    },
    {
      id: 'EVAL-003',
      dimension: 'tool_calling',
      overall_score: 78.9,
      metrics: [
        { name: '工具选择准确率', score: 82, weight: 0.5 },
        { name: '参数正确率', score: 76, weight: 0.5 },
      ],
      timestamp: '2024-01-13T00:00:00Z',
    },
    {
      id: 'EVAL-004',
      dimension: 'rag',
      overall_score: 85.0,
      metrics: [
        { name: '检索精确率', score: 88, weight: 0.5 },
        { name: '回答质量', score: 82, weight: 0.5 },
      ],
      timestamp: '2024-01-12T00:00:00Z',
    },
  ],
  setEvaluations: (evaluations) => set({ evaluations }),

  // Memory
  memories: [
    {
      id: 'MEM-001',
      key: 'db_pool_exhaustion_pattern',
      value: '数据库连接池耗尽的典型特征：活跃连接数持续上升、等待队列增长、P99延迟突增',
      type: 'knowledge',
      created_at: '2024-01-10T00:00:00Z',
      importance: 0.95,
      tags: ['database', 'connection_pool'],
    },
    {
      id: 'MEM-002',
      key: 'playbook_restart_db_proxy',
      value: '{"steps": ["检查当前连接数", "优雅关闭旧连接", "重启代理", "验证连接恢复"]}',
      type: 'playbook',
      created_at: '2024-01-08T00:00:00Z',
      importance: 0.9,
      tags: ['playbook', 'database'],
    },
    {
      id: 'MEM-003',
      key: 'incident_user_service_latency',
      value: 'user-service延迟问题历史：2024-01-05 因Redis缓存穿透导致，解决方案为布隆过滤器+本地缓存',
      type: 'incident',
      created_at: '2024-01-05T00:00:00Z',
      importance: 0.85,
      tags: ['user-service', 'latency', 'redis'],
    },
  ],
  setMemories: (memories) => set({ memories }),

  // Topology
  topology: null,
  setTopology: (topology) => set({ topology }),

  // Stats
  incidentStats: {
    total: 156,
    resolved: 128,
    processing: 23,
    failed: 5,
    avg_mttr: 12.5,
  },
  setIncidentStats: (incidentStats) => set({ incidentStats }),

  // WebSocket
  wsConnected: false,
  setWsConnected: (wsConnected) => set({ wsConnected }),

  // UI
  sidebarCollapsed: false,
  toggleSidebar: () =>
    set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
}));
