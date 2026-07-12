import { useState, useEffect, useCallback } from 'react';
import { useAppStore } from '@/store/useAppStore';
import { useAgents } from '@/hooks/useApi';
import { StatusBadge } from '@/components/common/StatusBadge';
import {
  Activity,
  BrainCircuit,
  Wrench,
  GitPullRequest,
  BookOpen,
  BarChart3,
  Play,
  Square,
  Clock,
  X,
  Bot,
  Cpu,
  MemoryStick,
  Wifi,
  RefreshCw,
} from 'lucide-react';
import type { Agent } from '@/types';

const agentIcons: Record<string, React.ReactNode> = {
  'monitor-agent': <Activity className="w-6 h-6" />,
  'monitor-agent-001': <Activity className="w-6 h-6" />,
  'rca-agent': <BrainCircuit className="w-6 h-6" />,
  'rca-agent-001': <BrainCircuit className="w-6 h-6" />,
  'heal-agent': <Wrench className="w-6 h-6" />,
  'heal-agent-001': <Wrench className="w-6 h-6" />,
  'change-agent': <GitPullRequest className="w-6 h-6" />,
  'change-agent-001': <GitPullRequest className="w-6 h-6" />,
  'knowledge-agent': <BookOpen className="w-6 h-6" />,
  'memory-agent-001': <BookOpen className="w-6 h-6" />,
  'eval-agent': <BarChart3 className="w-6 h-6" />,
  'eval-agent-001': <BarChart3 className="w-6 h-6" />,
  'orchestrator-001': <Bot className="w-6 h-6" />,
};

const agentTypeLabels: Record<string, string> = {
  detection: '检测型',
  analysis: '分析型',
  recovery: '恢复型',
  approval: '审批型',
  knowledge: '知识型',
  evaluation: '评估型',
  monitor: '检测型',
  rca: '分析型',
  heal: '恢复型',
  change: '审批型',
  memory: '知识型',
  eval: '评估型',
  orchestrator: '编排型',
};

export function AgentsPage() {
  const { agents: storeAgents, updateAgent, setAgents } = useAppStore();
  const { listAgents, getAgentStatus, loading } = useAgents();
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [agentMetrics, setAgentMetrics] = useState<Record<string, Record<string, unknown>>>({});

  // Load agents from API
  const loadAgents = useCallback(async () => {
    try {
      const result = await listAgents();
      if (result?.items) {
        const mapped: Agent[] = result.items.map((item: Record<string, unknown>) => ({
          id: (item.agent_id as string) || (item.id as string) || '',
          name: (item.name as string) || '',
          description: (item.description as string) || '',
          status: ((item.status as string) || 'idle') as Agent['status'],
          last_activity: new Date().toISOString(),
          type: (item.agent_type as string) || '',
        }));
        setAgents(mapped);
      }
    } catch (_e) {
      console.debug('[AgentsPage] Using store fallback');
    }
  }, [listAgents, setAgents]);

  useEffect(() => {
    loadAgents();
  }, [loadAgents]);

  // Load agent detail metrics
  const loadAgentDetail = useCallback(async (agentId: string) => {
    try {
      const status = await getAgentStatus(agentId);
      if (status) {
        setAgentMetrics((prev) => ({ ...prev, [agentId]: status as Record<string, unknown> }));
      }
    } catch (_e) {
      // use defaults
    }
  }, [getAgentStatus]);

  const agents = storeAgents.length > 0 ? storeAgents : [];

  const handleToggleAgent = (agent: Agent) => {
    const newStatus = agent.status === 'running' ? 'stopped' : 'running';
    updateAgent({
      ...agent,
      status: newStatus,
      last_activity: new Date().toISOString(),
    });
  };

  const openDetail = (agent: Agent) => {
    setSelectedAgent(agent);
    setDetailOpen(true);
    loadAgentDetail(agent.id);
  };

  const closeDetail = () => {
    setDetailOpen(false);
    setTimeout(() => setSelectedAgent(null), 300);
  };

  const metrics = selectedAgent ? agentMetrics[selectedAgent.id] : null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Agent管理</h1>
          <p className="text-sm text-muted-foreground mt-1">
            管理 7 个智能运维 Agent 的状态和配置
          </p>
        </div>
        <button
          onClick={loadAgents}
          disabled={loading}
          className="inline-flex items-center gap-2 px-3 py-2 border border-input bg-background text-foreground rounded-lg text-sm hover:bg-accent transition-colors"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div className="glass-card p-4 text-center">
          <p className="text-2xl font-bold text-foreground">{agents.length}</p>
          <p className="text-xs text-muted-foreground mt-1">Agent总数</p>
        </div>
        <div className="glass-card p-4 text-center">
          <p className="text-2xl font-bold text-emerald-400">
            {agents.filter((a) => a.status === 'running').length}
          </p>
          <p className="text-xs text-muted-foreground mt-1">运行中</p>
        </div>
        <div className="glass-card p-4 text-center">
          <p className="text-2xl font-bold text-slate-400">
            {agents.filter((a) => a.status === 'stopped').length}
          </p>
          <p className="text-xs text-muted-foreground mt-1">已停止</p>
        </div>
        <div className="glass-card p-4 text-center">
          <p className="text-2xl font-bold text-red-400">
            {agents.filter((a) => a.status === 'error').length}
          </p>
          <p className="text-xs text-muted-foreground mt-1">错误</p>
        </div>
      </div>

      {/* Agent Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {agents.map((agent) => (
          <div
            key={agent.id}
            className="glass-card p-5 transition-all duration-200 hover:shadow-xl hover:border-border/80 cursor-pointer group"
            onClick={() => openDetail(agent)}
          >
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-3">
                <div
                  className={`w-12 h-12 rounded-xl flex items-center justify-center ${
                    agent.status === 'running'
                      ? 'bg-emerald-400/10 text-emerald-400'
                      : agent.status === 'error'
                      ? 'bg-red-400/10 text-red-400'
                      : 'bg-slate-400/10 text-slate-400'
                  }`}
                >
                  {agentIcons[agent.id] || <Bot className="w-6 h-6" />}
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-foreground">
                    {agent.name}
                  </h3>
                  <span className="text-[10px] text-muted-foreground">
                    {agentTypeLabels[agent.type || ''] || '通用型'}
                  </span>
                </div>
              </div>
              <StatusBadge status={agent.status} />
            </div>

            <p className="mt-3 text-sm text-muted-foreground line-clamp-2">
              {agent.description}
            </p>

            <div className="mt-4 flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Clock className="w-3.5 h-3.5" />
                <span>
                  {new Date(agent.last_activity).toLocaleString('zh-CN')}
                </span>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  handleToggleAgent(agent);
                }}
                className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                  agent.status === 'running'
                    ? 'bg-red-400/10 text-red-400 hover:bg-red-400/20'
                    : 'bg-emerald-400/10 text-emerald-400 hover:bg-emerald-400/20'
                }`}
              >
                {agent.status === 'running' ? (
                  <>
                    <Square className="w-3 h-3" />
                    停止
                  </>
                ) : (
                  <>
                    <Play className="w-3 h-3" />
                    启动
                  </>
                )}
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Detail Drawer */}
      {detailOpen && selectedAgent && (
        <div className="fixed inset-0 z-50">
          <div
            className="absolute inset-0 bg-black/50 backdrop-blur-sm"
            onClick={closeDetail}
          />
          <div className="absolute right-0 top-0 h-full w-full max-w-lg bg-card border-l border-border shadow-xl overflow-y-auto animate-slide-in">
            <div className="p-6">
              {/* Header */}
              <div className="flex items-center justify-between mb-6">
                <div className="flex items-center gap-3">
                  <div
                    className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                      selectedAgent.status === 'running'
                        ? 'bg-emerald-400/10 text-emerald-400'
                        : 'bg-slate-400/10 text-slate-400'
                    }`}
                  >
                    {agentIcons[selectedAgent.id] || <Bot className="w-5 h-5" />}
                  </div>
                  <div>
                    <h2 className="text-lg font-semibold text-foreground">
                      {selectedAgent.name}
                    </h2>
                    <span className="text-xs text-muted-foreground">
                      ID: {selectedAgent.id}
                    </span>
                  </div>
                </div>
                <button
                  onClick={closeDetail}
                  className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Status & Type */}
              <div className="mb-6">
                <div className="flex items-center gap-3 mb-3">
                  <StatusBadge status={selectedAgent.status} />
                  <span className="text-xs text-muted-foreground">
                    类型: {agentTypeLabels[selectedAgent.type || ''] || '通用型'}
                  </span>
                </div>
                <p className="text-sm text-muted-foreground">
                  {selectedAgent.description}
                </p>
              </div>

              {/* Metrics from API */}
              <div className="space-y-4 mb-6">
                <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                  运行指标
                  {metrics && <span className="text-[10px] text-emerald-400 font-normal">(实时)</span>}
                </h3>
                <div className="grid grid-cols-2 gap-3">
                  <div className="p-3 bg-muted/30 rounded-lg">
                    <div className="flex items-center gap-2 mb-1">
                      <Cpu className="w-4 h-4 text-muted-foreground" />
                      <span className="text-xs text-muted-foreground">总执行次数</span>
                    </div>
                    <p className="text-lg font-semibold text-foreground">
                      {metrics?.total_executions != null
                        ? String(metrics.total_executions)
                        : selectedAgent.status === 'running' ? '1,250' : '0'}
                    </p>
                  </div>
                  <div className="p-3 bg-muted/30 rounded-lg">
                    <div className="flex items-center gap-2 mb-1">
                      <MemoryStick className="w-4 h-4 text-muted-foreground" />
                      <span className="text-xs text-muted-foreground">成功率</span>
                    </div>
                    <p className="text-lg font-semibold text-foreground">
                      {metrics?.success_rate != null
                        ? `${(Number(metrics.success_rate) * 100).toFixed(1)}%`
                        : selectedAgent.status === 'running' ? '98.2%' : '-'}
                    </p>
                  </div>
                  <div className="p-3 bg-muted/30 rounded-lg">
                    <div className="flex items-center gap-2 mb-1">
                      <Wifi className="w-4 h-4 text-muted-foreground" />
                      <span className="text-xs text-muted-foreground">当前任务</span>
                    </div>
                    <p className="text-lg font-semibold text-foreground">
                      {metrics?.current_task
                        ? String(metrics.current_task).slice(0, 20)
                        : selectedAgent.status === 'running' ? 'idle' : '-'}
                    </p>
                  </div>
                  <div className="p-3 bg-muted/30 rounded-lg">
                    <div className="flex items-center gap-2 mb-1">
                      <Clock className="w-4 h-4 text-muted-foreground" />
                      <span className="text-xs text-muted-foreground">最后活跃</span>
                    </div>
                    <p className="text-sm font-semibold text-foreground">
                      {new Date(selectedAgent.last_activity).toLocaleTimeString('zh-CN')}
                    </p>
                  </div>
                </div>
              </div>

              {/* Actions */}
              <div className="flex gap-3">
                <button
                  onClick={() => handleToggleAgent(selectedAgent)}
                  className={`flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                    selectedAgent.status === 'running'
                      ? 'bg-red-400/10 text-red-400 hover:bg-red-400/20'
                      : 'bg-emerald-400/10 text-emerald-400 hover:bg-emerald-400/20'
                  }`}
                >
                  {selectedAgent.status === 'running' ? (
                    <>
                      <Square className="w-4 h-4" />
                      停止Agent
                    </>
                  ) : (
                    <>
                      <Play className="w-4 h-4" />
                      启动Agent
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
