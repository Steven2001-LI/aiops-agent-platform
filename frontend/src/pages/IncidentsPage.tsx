import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppStore } from '@/store/useAppStore';
import { useIncidents } from '@/hooks/useApi';
import { StatusBadge } from '@/components/common/StatusBadge';
import {
  Search,
  Filter,
  ChevronLeft,
  ChevronRight,
  AlertTriangle,
  Zap,
} from 'lucide-react';
import type { Severity, IncidentStatus, Incident } from '@/types';

type FilterSeverity = Severity | 'all';
type FilterStatus = IncidentStatus | 'all';

const severityOptions: { value: FilterSeverity; label: string }[] = [
  { value: 'all', label: '全部级别' },
  { value: 'critical', label: '严重' },
  { value: 'high', label: '高危' },
  { value: 'medium', label: '中危' },
  { value: 'low', label: '低危' },
];

const statusOptions: { value: FilterStatus; label: string }[] = [
  { value: 'all', label: '全部状态' },
  { value: 'pending', label: '待处理' },
  { value: 'detecting', label: '检测中' },
  { value: 'analyzing', label: '分析中' },
  { value: 'healing', label: '修复中' },
  { value: 'resolved', label: '已解决' },
  { value: 'failed', label: '失败' },
];

// 预配置的故障注入场景
const FAULT_SCENARIOS = [
  // ========== 基础设施层 ==========
  {
    id: 'fs_cpu_spike',
    label: '🔥 CPU 飙升 (order-service)',
    category: 'infrastructure',
    service: 'order-service',
    metric: 'cpu_usage_percent',
    value: 95.0,
    threshold: 80.0,
    severity: 'critical' as Severity,
    description: '部署后 CPU 从 50% 飙升至 95%，触发自适应阈值告警',
  },
  {
    id: 'fs_memory_leak',
    label: '💧 内存泄漏 (payment-service)',
    category: 'infrastructure',
    service: 'payment-service',
    metric: 'memory_usage_percent',
    value: 92.0,
    threshold: 85.0,
    severity: 'critical' as Severity,
    description: '内存持续增长至 92%，触发 OOM Kill 预警',
  },
  {
    id: 'fs_db_timeout',
    label: '⏱️ 数据库超时 (user-service)',
    category: 'infrastructure',
    service: 'user-service',
    metric: 'p99_latency_ms',
    value: 1500.0,
    threshold: 200.0,
    severity: 'high' as Severity,
    description: '连接池耗尽导致 P99 延迟飙升至 1500ms',
  },
  {
    id: 'fs_error_spike',
    label: '❌ 错误率飙升 (api-gateway)',
    category: 'infrastructure',
    service: 'api-gateway',
    metric: 'error_rate_percent',
    value: 25.0,
    threshold: 5.0,
    severity: 'critical' as Severity,
    description: '网关配置错误导致错误率飙升至 25%',
  },
  // ========== 业务层 ==========
  {
    id: 'fs_biz_payment',
    label: '💳 支付网关超时 (payment-service)',
    category: 'business',
    service: 'payment-service',
    metric: 'error_rate_percent',
    value: 35.0,
    threshold: 2.0,
    severity: 'critical' as Severity,
    description: '第三方支付网关超时 → 交易失败 → 订单积压 → 用户投诉。需要启用支付降级通道',
  },
  {
    id: 'fs_biz_order_stuck',
    label: '📦 订单处理卡死 (order-service)',
    category: 'business',
    service: 'order-service',
    metric: 'p99_latency_ms',
    value: 5000.0,
    threshold: 500.0,
    severity: 'critical' as Severity,
    description: '库存数据不一致 → 订单处理器死锁 → 订单队列积压 200+，影响全站购物',
  },
  {
    id: 'fs_biz_auth_failure',
    label: '🔐 登录失败风暴 (user-service)',
    category: 'business',
    service: 'user-service',
    metric: 'error_rate_percent',
    value: 40.0,
    threshold: 5.0,
    severity: 'critical' as Severity,
    description: 'SSO Token 缓存批量过期 → 大量用户登录失败 → 全站 40% 请求报 401',
  },
  {
    id: 'fs_biz_rate_limit',
    label: '🚦 限流误杀 (api-gateway)',
    category: 'business',
    service: 'api-gateway',
    metric: 'error_rate_percent',
    value: 18.0,
    threshold: 5.0,
    severity: 'high' as Severity,
    description: '促销活动期间限流配置过严 → 误杀正常用户 → 429 错误率 18%',
  },
  {
    id: 'fs_biz_mq_backlog',
    label: '📨 消息队列积压 (order-service)',
    category: 'business',
    service: 'order-service',
    metric: 'cpu_usage_percent',
    value: 88.0,
    threshold: 75.0,
    severity: 'high' as Severity,
    description: '消费者被损坏消息卡住 → MQ 积压 5000+ → 订单处理延迟 30min+',
  },
  {
    id: 'fs_biz_data_inconsistency',
    label: '🔄 数据不一致 (inventory-service)',
    category: 'business',
    service: 'inventory-service',
    metric: 'p99_latency_ms',
    value: 600.0,
    threshold: 100.0,
    severity: 'medium' as Severity,
    description: 'MySQL-ES 复制延迟 → 搜索结果与实际库存不符 → 超卖风险',
  },
];

export function IncidentsPage() {
  const navigate = useNavigate();
  const { incidents: storeIncidents, setIncidents } = useAppStore();
  const { listIncidents, triggerIncident, loading } = useIncidents();

  const [searchQuery, setSearchQuery] = useState('');
  const [severityFilter, setSeverityFilter] = useState<FilterSeverity>('all');
  const [statusFilter, setStatusFilter] = useState<FilterStatus>('all');
  const [currentPage, setCurrentPage] = useState(1);
  const [showTriggerModal, setShowTriggerModal] = useState(false);
  const [triggerLoading, setTriggerLoading] = useState(false);
  const [selectedScenario, setSelectedScenario] = useState<string>('');

  const itemsPerPage = 10;

  // 从 API 加载故障列表
  const loadIncidents = useCallback(async () => {
    try {
      const result = await listIncidents({
        severity: severityFilter === 'all' ? undefined : severityFilter,
        state: statusFilter === 'all' ? undefined : statusFilter,
        page: currentPage,
        pageSize: itemsPerPage,
      });
      if (result?.items) {
        // 转换后端数据格式到前端格式
        const mapped = result.items.map((item: Record<string, unknown>) => ({
          id: (item.incident_id as string) || (item.id as string) || '',
          service: (item.service as string) || '',
          metric: (item.metric as string) || (item.alert_event as Record<string, unknown>)?.metric as string || '',
          severity: (item.severity as Severity) || 'medium',
          status: (item.status as IncidentStatus) || (item.state as IncidentStatus) || 'pending',
          alert: (item.alert as Incident['alert']) || (item.alert_event
            ? { is_anomaly: true, confidence: 0.9, algorithms_voted: [] }
            : undefined),
          rca: item.rca as Incident['rca'],
          heal: item.heal as Incident['heal'],
          change: item.change as Incident['change'],
          created_at: (item.created_at as string) || new Date().toISOString(),
          updated_at: (item.updated_at as string) || new Date().toISOString(),
        }));
        setIncidents(mapped as Incident[]);
      }
    } catch (_err) {
      // API 调用失败时保留 store 中的种子数据
      console.warn('[IncidentsPage] API failed, using fallback data');
    }
  }, [listIncidents, severityFilter, statusFilter, currentPage, setIncidents]);

  useEffect(() => {
    loadIncidents();
  }, [loadIncidents]);

  // 处理故障触发
  const handleTriggerIncident = async () => {
    const scenario = FAULT_SCENARIOS.find((s) => s.id === selectedScenario);
    if (!scenario) return;

    setTriggerLoading(true);
    try {
      const result = await triggerIncident({
        service: scenario.service,
        metric: scenario.metric,
        severity: scenario.severity,
        value: scenario.value,
        threshold: scenario.threshold,
      });
      console.log('[IncidentsPage] Incident triggered:', result);
      // 重新加载列表
      await loadIncidents();
    } catch (err) {
      console.error('[IncidentsPage] Trigger failed:', err);
    } finally {
      setTriggerLoading(false);
      setShowTriggerModal(false);
      setSelectedScenario('');
    }
  };

  const incidents = storeIncidents.length > 0 ? storeIncidents : [];

  const filteredIncidents = incidents.filter((incident) => {
    const matchesSearch =
      searchQuery === '' ||
      incident.id.toLowerCase().includes(searchQuery.toLowerCase()) ||
      incident.service.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (incident.metric || '').toLowerCase().includes(searchQuery.toLowerCase());

    const matchesSeverity =
      severityFilter === 'all' || incident.severity === severityFilter;

    const matchesStatus =
      statusFilter === 'all' || incident.status === statusFilter;

    return matchesSearch && matchesSeverity && matchesStatus;
  });

  const totalPages = Math.ceil(filteredIncidents.length / itemsPerPage);
  const paginatedIncidents = filteredIncidents.slice(
    (currentPage - 1) * itemsPerPage,
    currentPage * itemsPerPage
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">故障管理</h1>
          <p className="text-sm text-muted-foreground mt-1">
            查看和管理所有故障事件 · 支持一键注入模拟故障
          </p>
        </div>
        <button
          onClick={() => setShowTriggerModal(true)}
          className="inline-flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          <Zap className="w-4 h-4" />
          注入故障
        </button>
      </div>

      {/* Filters */}
      <div className="glass-card p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-[200px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <input
              type="text"
              placeholder="搜索故障ID、服务或指标..."
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                setCurrentPage(1);
              }}
              className="w-full pl-9 pr-4 py-2 bg-background border border-input rounded-lg text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>

          <div className="flex items-center gap-2">
            <Filter className="w-4 h-4 text-muted-foreground" />
            <select
              value={severityFilter}
              onChange={(e) => {
                setSeverityFilter(e.target.value as FilterSeverity);
                setCurrentPage(1);
              }}
              className="px-3 py-2 bg-background border border-input rounded-lg text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {severityOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>

            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value as FilterStatus);
                setCurrentPage(1);
              }}
              className="px-3 py-2 bg-background border border-input rounded-lg text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {statusOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Table */}
      <div className="glass-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/50 bg-muted/30">
                <th className="text-left py-3 px-4 text-muted-foreground font-medium">
                  故障ID
                </th>
                <th className="text-left py-3 px-4 text-muted-foreground font-medium">
                  服务
                </th>
                <th className="text-left py-3 px-4 text-muted-foreground font-medium">
                  指标
                </th>
                <th className="text-left py-3 px-4 text-muted-foreground font-medium">
                  严重级别
                </th>
                <th className="text-left py-3 px-4 text-muted-foreground font-medium">
                  状态
                </th>
                <th className="text-left py-3 px-4 text-muted-foreground font-medium">
                  创建时间
                </th>
                <th className="text-left py-3 px-4 text-muted-foreground font-medium">
                  操作
                </th>
              </tr>
            </thead>
            <tbody>
              {paginatedIncidents.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-12 text-center text-muted-foreground">
                    <AlertTriangle className="w-8 h-8 mx-auto mb-2 opacity-50" />
                    {loading ? '加载中...' : '暂无匹配的故障数据'}
                  </td>
                </tr>
              )}
              {paginatedIncidents.map((incident) => (
                <tr
                  key={incident.id}
                  className="border-b border-border/30 hover:bg-accent/30 transition-colors"
                >
                  <td className="py-3 px-4 font-mono text-xs">{incident.id}</td>
                  <td className="py-3 px-4 font-medium">{incident.service}</td>
                  <td className="py-3 px-4 text-muted-foreground">
                    {incident.metric}
                  </td>
                  <td className="py-3 px-4">
                    <StatusBadge status={incident.severity} />
                  </td>
                  <td className="py-3 px-4">
                    <StatusBadge status={incident.status} />
                  </td>
                  <td className="py-3 px-4 text-muted-foreground text-xs">
                    {new Date(incident.created_at).toLocaleString('zh-CN')}
                  </td>
                  <td className="py-3 px-4">
                    <button
                      onClick={() => navigate(`/incidents/${incident.id}`)}
                      className="text-xs text-primary hover:underline"
                    >
                      详情
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-border/50">
            <span className="text-xs text-muted-foreground">
              共 {filteredIncidents.length} 条，第 {currentPage}/{totalPages} 页
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                disabled={currentPage === 1}
                className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              {Array.from({ length: totalPages }, (_, i) => i + 1).map((page) => (
                <button
                  key={page}
                  onClick={() => setCurrentPage(page)}
                  className={`min-w-[28px] px-2 py-1 rounded-md text-xs font-medium transition-colors ${
                    page === currentPage
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                  }`}
                >
                  {page}
                </button>
              ))}
              <button
                onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                disabled={currentPage === totalPages}
                className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Fault Injection Modal */}
      {showTriggerModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="bg-card border border-border rounded-xl shadow-xl w-full max-w-lg mx-4 p-6">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-lg font-semibold text-foreground flex items-center gap-2">
                  <Zap className="w-5 h-5 text-yellow-500" />
                  注入模拟故障
                </h2>
                <p className="text-xs text-muted-foreground mt-1">
                  选择一个预配置的故障场景来观察 Agent 分析过程
                </p>
              </div>
              <button
                onClick={() => {
                  setShowTriggerModal(false);
                  setSelectedScenario('');
                }}
                className="text-muted-foreground hover:text-foreground"
              >
                ✕
              </button>
            </div>

            <div className="space-y-4 max-h-80 overflow-y-auto mb-4">
              {/* 基础设施层 */}
              <div>
                <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2 px-1">
                  🖥️ 基础设施层
                </h3>
                <div className="space-y-2">
                  {FAULT_SCENARIOS.filter(s => (s as Record<string, unknown>).category === 'infrastructure').map((scenario) => (
                    <button
                      key={scenario.id}
                      onClick={() => setSelectedScenario(scenario.id)}
                      className={`w-full text-left p-3 rounded-lg border transition-all ${
                        selectedScenario === scenario.id
                          ? 'border-primary bg-primary/10 shadow-sm'
                          : 'border-border hover:border-primary/50 hover:bg-accent/50'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-sm text-foreground">
                          {scenario.label}
                        </span>
                        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                          scenario.severity === 'critical' ? 'bg-red-500/10 text-red-500' : 'bg-orange-500/10 text-orange-500'
                        }`}>
                          {scenario.severity.toUpperCase()}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground mt-1">{scenario.description}</p>
                      <div className="flex gap-2 mt-1.5">
                        <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{scenario.service}</code>
                        <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{scenario.metric}={scenario.value}</code>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
              {/* 业务层 */}
              <div>
                <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2 px-1">
                  💼 业务层
                </h3>
                <div className="space-y-2">
                  {FAULT_SCENARIOS.filter(s => (s as Record<string, unknown>).category === 'business').map((scenario) => (
                    <button
                      key={scenario.id}
                      onClick={() => setSelectedScenario(scenario.id)}
                      className={`w-full text-left p-3 rounded-lg border transition-all ${
                        selectedScenario === scenario.id
                          ? 'border-primary bg-primary/10 shadow-sm'
                          : 'border-border hover:border-primary/50 hover:bg-accent/50'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-sm text-foreground">
                          {scenario.label}
                        </span>
                        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                          scenario.severity === 'critical' ? 'bg-red-500/10 text-red-500' : 'bg-orange-500/10 text-orange-500'
                        }`}>
                          {scenario.severity.toUpperCase()}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground mt-1">{scenario.description}</p>
                      <div className="flex gap-2 mt-1.5">
                        <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{scenario.service}</code>
                        <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{scenario.metric}={scenario.value}</code>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="flex gap-3 pt-2 border-t border-border">
              <button
                onClick={() => {
                  setShowTriggerModal(false);
                  setSelectedScenario('');
                }}
                className="flex-1 px-4 py-2 border border-input bg-background text-foreground rounded-lg text-sm font-medium hover:bg-accent transition-colors"
              >
                取消
              </button>
              <button
                onClick={handleTriggerIncident}
                disabled={!selectedScenario || triggerLoading}
                className="flex-1 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
              >
                {triggerLoading ? (
                  <>
                    <span className="animate-spin">⏳</span>
                    注入中...
                  </>
                ) : (
                  <>
                    <Zap className="w-4 h-4" />
                    确认注入
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
