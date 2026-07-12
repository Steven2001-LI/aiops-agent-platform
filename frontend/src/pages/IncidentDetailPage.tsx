import { useParams, useNavigate } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { useAppStore } from '@/store/useAppStore';
import { useIncidents } from '@/hooks/useApi';
import { StatusBadge } from '@/components/common/StatusBadge';
import { Timeline } from '@/components/incidents/Timeline';
import type { Incident } from '@/types';
import {
  ArrowLeft,
  RefreshCw,
  Play,
  CheckCircle,
  XCircle,
  AlertTriangle,
  BarChart3,
  Zap,
  ShieldCheck,
  GitPullRequest,
  Target,
  Network,
  Info,
  CheckCircle2,
} from 'lucide-react';

export function IncidentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { incidents, updateIncident } = useAppStore();
  const { getIncident, loading } = useIncidents();
  const [apiIncident, setApiIncident] = useState<Incident | null>(null);

  // Try loading from API if not in store
  useEffect(() => {
    if (!id) return;
    const fetchFromApi = async () => {
      try {
        const result = await getIncident(id);
        if (result) {
          const item = result as unknown as Record<string, unknown>;
          setApiIncident({
            id: (item.incident_id as string) || (item.id as string) || id,
            service: (item.service as string) || '',
            metric: (item.metric as string) || '',
            severity: (item.severity as Incident['severity']) || 'medium',
            status: (item.status as Incident['status']) || (item.state as Incident['status']) || 'pending',
            alert: item.alert as Incident['alert'],
            rca: item.rca as Incident['rca'],
            heal: item.heal as Incident['heal'],
            change: item.change as Incident['change'],
            created_at: (item.created_at as string) || '',
            updated_at: (item.updated_at as string) || '',
          });
        }
      } catch (_e) {
        // fallback to store
      }
    };
    fetchFromApi();
  }, [id, getIncident]);

  const incident = incidents.find((i) => i.id === id) || apiIncident;

  if (!incident && loading) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <RefreshCw className="w-8 h-8 text-muted-foreground mb-4 animate-spin" />
        <h2 className="text-lg font-medium text-foreground">加载中...</h2>
      </div>
    );
  }

  if (!incident) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <AlertTriangle className="w-12 h-12 text-muted-foreground mb-4" />
        <h2 className="text-lg font-medium text-foreground">故障不存在</h2>
        <button
          onClick={() => navigate('/incidents')}
          className="mt-4 text-primary hover:underline"
        >
          返回列表
        </button>
      </div>
    );
  }

  const timelineItems: Array<{
    agent: string;
    status: 'completed' | 'in_progress' | 'pending' | 'failed';
    time?: string;
    detail: string;
  }> = [
    {
      agent: 'MonitorAgent',
      status: incident.alert
        ? 'completed'
        : incident.status === 'pending'
        ? 'pending'
        : 'in_progress',
      time: incident.created_at,
      detail: incident.alert
        ? `检测到异常，置信度 ${(incident.alert.confidence * 100).toFixed(
            1
          )}%，${incident.alert.algorithms_voted.length} 个算法投票`
        : '等待检测...',
    },
    {
      agent: 'RCAAgent',
      status: incident.rca
        ? 'completed'
        : incident.status === 'analyzing' || incident.status === 'healing'
        ? 'in_progress'
        : ['resolved', 'failed'].includes(incident.status) && !incident.rca
        ? 'failed'
        : 'pending',
      time: incident.rca ? incident.updated_at : undefined,
      detail: incident.rca
        ? incident.rca.root_cause
        : incident.status === 'analyzing'
        ? '正在分析根因...'
        : '等待分析...',
    },
    {
      agent: 'HealAgent',
      status: incident.heal
        ? 'completed'
        : incident.status === 'healing'
        ? 'in_progress'
        : incident.status === 'failed'
        ? 'failed'
        : 'pending',
      time: incident.heal ? incident.updated_at : undefined,
      detail: incident.heal
        ? `匹配Playbook: ${incident.heal.action} (${incident.heal.level})，爆炸半径: ${(
            incident.heal.blast_radius * 100
          ).toFixed(1)}%`
        : incident.status === 'healing'
        ? '正在执行恢复...'
        : '等待恢复...',
    },
    {
      agent: 'ChangeAgent',
      status: incident.change
        ? incident.change.approval_status === 'approved'
          ? 'completed'
          : 'in_progress'
        : incident.status === 'resolved'
        ? 'completed'
        : 'pending',
      time: incident.change ? incident.updated_at : undefined,
      detail: incident.change
        ? `风险评分: ${incident.change.risk_score}，审批状态: ${incident.change.approval_status}`
        : '等待审批...',
    },
  ];

  const handleReanalyze = () => {
    updateIncident({
      ...incident,
      status: 'analyzing',
      updated_at: new Date().toISOString(),
    });
  };

  const handleApprove = () => {
    updateIncident({
      ...incident,
      status: 'healing',
      change: {
        ...incident.change,
        approval_status: 'approved',
        risk_score: incident.change?.risk_score ?? 50,
        approver: 'admin',
      },
      updated_at: new Date().toISOString(),
    });
  };

  const handleReject = () => {
    updateIncident({
      ...incident,
      status: 'failed',
      change: {
        ...incident.change,
        approval_status: 'rejected',
        risk_score: incident.change?.risk_score ?? 50,
        approver: 'admin',
      },
      updated_at: new Date().toISOString(),
    });
  };

  return (
    <div className="space-y-6">
      {/* Back + Title */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/incidents')}
            className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-bold text-foreground">
                {incident.id}
              </h1>
              <StatusBadge status={incident.status} />
              <StatusBadge status={incident.severity} />
            </div>
            <p className="text-sm text-muted-foreground mt-1">
              {incident.service} / {incident.metric}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleReanalyze}
            className="inline-flex items-center gap-2 px-3 py-2 border border-input bg-background text-foreground rounded-lg text-sm hover:bg-accent transition-colors"
          >
            <RefreshCw className="w-4 h-4" />
            重新分析
          </button>
          {incident.status === 'analyzing' && (
            <button
              onClick={handleApprove}
              className="inline-flex items-center gap-2 px-3 py-2 bg-emerald-600 text-white rounded-lg text-sm hover:bg-emerald-700 transition-colors"
            >
              <Play className="w-4 h-4" />
              执行修复
            </button>
          )}
          {incident.status === 'healing' && incident.change?.approval_status === 'pending' && (
            <>
              <button
                onClick={handleApprove}
                className="inline-flex items-center gap-2 px-3 py-2 bg-emerald-600 text-white rounded-lg text-sm hover:bg-emerald-700 transition-colors"
              >
                <CheckCircle className="w-4 h-4" />
                审批通过
              </button>
              <button
                onClick={handleReject}
                className="inline-flex items-center gap-2 px-3 py-2 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700 transition-colors"
              >
                <XCircle className="w-4 h-4" />
                拒绝
              </button>
            </>
          )}
        </div>
      </div>

      {/* Basic Info */}
      <div className="glass-card p-5">
        <h3 className="text-base font-semibold text-foreground mb-4">
          基本信息
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <p className="text-xs text-muted-foreground">服务</p>
            <p className="text-sm font-medium text-foreground mt-1">
              {incident.service}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">指标</p>
            <p className="text-sm font-medium text-foreground mt-1">
              {incident.metric}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">严重级别</p>
            <div className="mt-1">
              <StatusBadge status={incident.severity} />
            </div>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">当前状态</p>
            <div className="mt-1">
              <StatusBadge status={incident.status} />
            </div>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">创建时间</p>
            <p className="text-sm font-medium text-foreground mt-1">
              {new Date(incident.created_at).toLocaleString('zh-CN')}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">更新时间</p>
            <p className="text-sm font-medium text-foreground mt-1">
              {new Date(incident.updated_at).toLocaleString('zh-CN')}
            </p>
          </div>
        </div>
      </div>

      {/* Timeline */}
      <div className="glass-card p-5">
        <h3 className="text-base font-semibold text-foreground mb-4">
          处理时间线
        </h3>
        <Timeline items={timelineItems} currentStatus={incident.status} />
      </div>

      {/* Agent Results */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* MonitorAgent Result */}
        {incident.alert && (
          <div className="glass-card p-5">
            <div className="flex items-center gap-2 mb-4">
              <BarChart3 className="w-5 h-5 text-blue-400" />
              <h3 className="text-base font-semibold text-foreground">
                MonitorAgent 检测结果
              </h3>
            </div>
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">异常检测</span>
                <span
                  className={`text-sm font-medium ${
                    incident.alert.is_anomaly
                      ? 'text-red-400'
                      : 'text-emerald-400'
                  }`}
                >
                  {incident.alert.is_anomaly ? '异常' : '正常'}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">置信度</span>
                <div className="flex items-center gap-2">
                  <div className="w-24 h-2 bg-muted rounded-full overflow-hidden">
                    <div
                      className="h-full bg-blue-400 rounded-full transition-all"
                      style={{
                        width: `${incident.alert.confidence * 100}%`,
                      }}
                    />
                  </div>
                  <span className="text-sm font-medium">
                    {(incident.alert.confidence * 100).toFixed(1)}%
                  </span>
                </div>
              </div>
              <div>
                <span className="text-sm text-muted-foreground">
                  投票算法
                </span>
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {incident.alert.algorithms_voted.map((algo) => (
                    <span
                      key={algo}
                      className="px-2 py-0.5 bg-blue-400/10 text-blue-400 rounded text-xs"
                    >
                      {algo}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* RCAAgent Result */}
        {incident.rca && (
          <div className="glass-card p-5">
            <div className="flex items-center gap-2 mb-4">
              <Target className="w-5 h-5 text-amber-400" />
              <h3 className="text-base font-semibold text-foreground">
                RCAAgent 分析结果
              </h3>
            </div>
            <div className="space-y-3">
              <div>
                <span className="text-sm text-muted-foreground">根因</span>
                <p className="text-sm font-medium text-foreground mt-1">
                  {incident.rca.root_cause}
                </p>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">置信度</span>
                <div className="flex items-center gap-2">
                  <div className="w-24 h-2 bg-muted rounded-full overflow-hidden">
                    <div
                      className="h-full bg-amber-400 rounded-full transition-all"
                      style={{
                        width: `${incident.rca.confidence * 100}%`,
                      }}
                    />
                  </div>
                  <span className="text-sm font-medium">
                    {(incident.rca.confidence * 100).toFixed(1)}%
                  </span>
                </div>
              </div>
              <div>
                <span className="text-sm text-muted-foreground">影响链</span>
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {incident.rca.impact_chain.map((item, idx) => (
                    <div key={item} className="flex items-center gap-1.5">
                      <span className="px-2 py-0.5 bg-amber-400/10 text-amber-400 rounded text-xs">
                        {item}
                      </span>
                      {idx < incident.rca!.impact_chain.length - 1 && (
                        <span className="text-muted-foreground">→</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <span className="text-sm text-muted-foreground">建议操作</span>
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {incident.rca.suggested_actions.map((action) => (
                    <span
                      key={action}
                      className="px-2 py-0.5 bg-emerald-400/10 text-emerald-400 rounded text-xs"
                    >
                      {action}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* HealAgent Result */}
        {incident.heal && (
          <div className="glass-card p-5">
            <div className="flex items-center gap-2 mb-4">
              <Zap className="w-5 h-5 text-purple-400" />
              <h3 className="text-base font-semibold text-foreground">
                HealAgent 恢复结果
              </h3>
            </div>
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">执行操作</span>
                <span className="text-sm font-medium text-foreground">
                  {incident.heal.action}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">修复级别</span>
                <span className="px-2 py-0.5 bg-purple-400/10 text-purple-400 rounded text-xs font-medium">
                  {incident.heal.level}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">
                  Dry-run 结果
                </span>
                <span
                  className={`text-sm font-medium flex items-center gap-1 ${
                    incident.heal.dry_run_result === 'success'
                      ? 'text-emerald-400'
                      : 'text-red-400'
                  }`}
                >
                  {incident.heal.dry_run_result === 'success' ? (
                    <CheckCircle2 className="w-4 h-4" />
                  ) : (
                    <XCircle className="w-4 h-4" />
                  )}
                  {incident.heal.dry_run_result}
                </span>
              </div>
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm text-muted-foreground">
                    爆炸半径
                  </span>
                  <span className="text-sm font-medium text-foreground">
                    {(incident.heal.blast_radius * 100).toFixed(1)}%
                  </span>
                </div>
                <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      incident.heal.blast_radius < 0.2
                        ? 'bg-emerald-400'
                        : incident.heal.blast_radius < 0.5
                        ? 'bg-amber-400'
                        : 'bg-red-400'
                    }`}
                    style={{
                      width: `${incident.heal.blast_radius * 100}%`,
                    }}
                  />
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ChangeAgent Result */}
        {incident.change && (
          <div className="glass-card p-5">
            <div className="flex items-center gap-2 mb-4">
              <GitPullRequest className="w-5 h-5 text-cyan-400" />
              <h3 className="text-base font-semibold text-foreground">
                ChangeAgent 变更审批
              </h3>
            </div>
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">审批状态</span>
                <StatusBadge status={incident.change.approval_status} />
              </div>
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm text-muted-foreground">
                    风险评分
                  </span>
                  <span className="text-sm font-medium text-foreground">
                    {incident.change.risk_score} / 100
                  </span>
                </div>
                <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      incident.change.risk_score < 30
                        ? 'bg-emerald-400'
                        : incident.change.risk_score < 70
                        ? 'bg-amber-400'
                        : 'bg-red-400'
                    }`}
                    style={{ width: `${incident.change.risk_score}%` }}
                  />
                </div>
              </div>
              {incident.change.approver && (
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">审批人</span>
                  <div className="flex items-center gap-1.5">
                    <ShieldCheck className="w-4 h-4 text-muted-foreground" />
                    <span className="text-sm font-medium text-foreground">
                      {incident.change.approver}
                    </span>
                  </div>
                </div>
              )}
              {incident.change.approval_status === 'pending' && (
                <div className="flex gap-2 pt-2">
                  <button
                    onClick={handleApprove}
                    className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-2 bg-emerald-600 text-white rounded-lg text-sm hover:bg-emerald-700 transition-colors"
                  >
                    <CheckCircle className="w-4 h-4" />
                    通过
                  </button>
                  <button
                    onClick={handleReject}
                    className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-2 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700 transition-colors"
                  >
                    <XCircle className="w-4 h-4" />
                    拒绝
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Missing data info */}
        {!incident.alert && !incident.rca && !incident.heal && !incident.change && (
          <div className="glass-card p-5 lg:col-span-2">
            <div className="flex items-center justify-center gap-3 py-8 text-muted-foreground">
              <Info className="w-5 h-5" />
              <span className="text-sm">
                该故障尚未开始处理，等待Agent执行...
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Impact Chain Graph Placeholder */}
      {incident.rca?.impact_chain && (
        <div className="glass-card p-5">
          <div className="flex items-center gap-2 mb-4">
            <Network className="w-5 h-5 text-primary" />
            <h3 className="text-base font-semibold text-foreground">
              影响链可视化
            </h3>
          </div>
          <div className="flex items-center justify-center gap-3 py-6">
            <div className="px-4 py-3 bg-red-400/10 border border-red-400/30 rounded-lg text-center">
              <p className="text-xs text-muted-foreground mb-1">故障源</p>
              <p className="text-sm font-medium text-red-400">
                {incident.service}
              </p>
            </div>
            {incident.rca.impact_chain.map((node, idx) => (
              <div key={`${node}-${idx}`} className="flex items-center gap-3">
                <span className="text-muted-foreground">→</span>
                <div className="px-4 py-3 bg-amber-400/10 border border-amber-400/30 rounded-lg text-center">
                  <p className="text-xs text-muted-foreground mb-1">
                    影响 {idx + 1}
                  </p>
                  <p className="text-sm font-medium text-amber-400">{node}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
