import { useNavigate } from 'react-router-dom';
import { useAppStore } from '@/store/useAppStore';
import { StatCard } from '@/components/dashboard/StatCard';
import { AgentStatusOverview } from '@/components/dashboard/AgentStatus';
import { StatusBadge } from '@/components/common/StatusBadge';
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Clock,
  Activity,
  Bot,
  TrendingUp,
  PieChart,
} from 'lucide-react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart as RePieChart,
  Pie,
  Cell,
  Legend,
} from 'recharts';

const trendData = [
  { time: '00:00', score: 82 },
  { time: '04:00', score: 85 },
  { time: '08:00', score: 78 },
  { time: '12:00', score: 88 },
  { time: '16:00', score: 92 },
  { time: '20:00', score: 87 },
  { time: '23:59', score: 91 },
];

const severityData = [
  { name: '严重', value: 12, color: '#ef4444' },
  { name: '高危', value: 28, color: '#f97316' },
  { name: '中危', value: 56, color: '#eab308' },
  { name: '低危', value: 60, color: '#3b82f6' },
];

const healthScore = 87;
const healthColor =
  healthScore >= 85 ? '#22c55e' : healthScore >= 60 ? '#f59e0b' : '#ef4444';

export function DashboardPage() {
  const navigate = useNavigate();
  const { incidentStats, agents, incidents } = useAppStore();

  const recentIncidents = incidents.slice(0, 5);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">仪表盘</h1>
          <p className="text-sm text-muted-foreground mt-1">
            实时监控多智能体运维平台状态
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-emerald-400" />
          <span className="text-sm text-muted-foreground">
            系统运行正常
          </span>
        </div>
      </div>

      {/* Health Score Card */}
      <div className="glass-card p-6">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-lg font-semibold text-foreground">
              系统健康度
            </h3>
            <p className="text-sm text-muted-foreground mt-1">
              综合所有Agent和故障处理指标
            </p>
            <div className="mt-4 flex items-center gap-6">
              <div>
                <span className="text-3xl font-bold" style={{ color: healthColor }}>
                  {healthScore}
                </span>
                <span className="text-sm text-muted-foreground">/ 100</span>
              </div>
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <TrendingUp className="w-4 h-4 text-emerald-400" />
                <span>较昨日 +2.3%</span>
              </div>
            </div>
          </div>
          {/* Circular Progress */}
          <div className="relative w-28 h-28">
            <svg className="w-full h-full transform -rotate-90" viewBox="0 0 100 100">
              <circle
                cx="50" cy="50" r="42"
                fill="none"
                stroke="currentColor"
                strokeWidth="8"
                className="text-muted/20"
              />
              <circle
                cx="50" cy="50" r="42"
                fill="none"
                stroke={healthColor}
                strokeWidth="8"
                strokeLinecap="round"
                strokeDasharray={`${(healthScore / 100) * 264} 264`}
                className="transition-all duration-1000"
              />
            </svg>
            <div className="absolute inset-0 flex items-center justify-center">
              <Activity className="w-8 h-8" style={{ color: healthColor }} />
            </div>
          </div>
        </div>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        <StatCard
          title="故障总数"
          value={incidentStats.total}
          trend={-5.2}
          trendLabel="较上周"
          icon={<AlertTriangle className="w-5 h-5" />}
          iconColor="text-red-400"
          iconBgColor="bg-red-400/10"
          onClick={() => navigate('/incidents')}
        />
        <StatCard
          title="已解决"
          value={incidentStats.resolved}
          trend={8.1}
          trendLabel="较上周"
          icon={<CheckCircle2 className="w-5 h-5" />}
          iconColor="text-emerald-400"
          iconBgColor="bg-emerald-400/10"
        />
        <StatCard
          title="处理中"
          value={incidentStats.processing}
          trend={-12.3}
          trendLabel="较上周"
          icon={<Loader2 className="w-5 h-5" />}
          iconColor="text-blue-400"
          iconBgColor="bg-blue-400/10"
        />
        <StatCard
          title="平均MTTR"
          value={`${incidentStats.avg_mttr} min`}
          trend={-15.6}
          trendLabel="效率提升"
          icon={<Clock className="w-5 h-5" />}
          iconColor="text-purple-400"
          iconBgColor="bg-purple-400/10"
        />
      </div>

      {/* Agent Status */}
      <div className="glass-card p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Bot className="w-5 h-5 text-primary" />
            <h3 className="text-base font-semibold text-foreground">
              Agent状态概览
            </h3>
          </div>
          <button
            onClick={() => navigate('/agents')}
            className="text-xs text-primary hover:underline"
          >
            查看全部
          </button>
        </div>
        <AgentStatusOverview agents={agents} />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Trend Chart */}
        <div className="glass-card p-5">
          <div className="flex items-center gap-2 mb-4">
            <TrendingUp className="w-5 h-5 text-primary" />
            <h3 className="text-base font-semibold text-foreground">
              评估分数趋势
            </h3>
          </div>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={trendData}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
              <XAxis
                dataKey="time"
                stroke="hsl(var(--muted-foreground))"
                fontSize={12}
              />
              <YAxis
                domain={[60, 100]}
                stroke="hsl(var(--muted-foreground))"
                fontSize={12}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'hsl(var(--card))',
                  border: '1px solid hsl(var(--border))',
                  borderRadius: '8px',
                }}
              />
              <Line
                type="monotone"
                dataKey="score"
                stroke="#3b82f6"
                strokeWidth={2}
                dot={{ fill: '#3b82f6', r: 4 }}
                activeDot={{ r: 6, fill: '#3b82f6' }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Pie Chart */}
        <div className="glass-card p-5">
          <div className="flex items-center gap-2 mb-4">
            <PieChart className="w-5 h-5 text-primary" />
            <h3 className="text-base font-semibold text-foreground">
              故障类型分布
            </h3>
          </div>
          <ResponsiveContainer width="100%" height={250}>
            <RePieChart>
              <Pie
                data={severityData}
                cx="50%"
                cy="50%"
                innerRadius={60}
                outerRadius={90}
                paddingAngle={4}
                dataKey="value"
              >
                {severityData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={entry.color} />
                ))}
              </Pie>
              <Legend />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'hsl(var(--card))',
                  border: '1px solid hsl(var(--border))',
                  borderRadius: '8px',
                }}
              />
            </RePieChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Recent Incidents */}
      <div className="glass-card p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-foreground">最近故障</h3>
          <button
            onClick={() => navigate('/incidents')}
            className="text-xs text-primary hover:underline"
          >
            查看全部
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/50">
                <th className="text-left py-2.5 px-3 text-muted-foreground font-medium">
                  ID
                </th>
                <th className="text-left py-2.5 px-3 text-muted-foreground font-medium">
                  服务
                </th>
                <th className="text-left py-2.5 px-3 text-muted-foreground font-medium">
                  级别
                </th>
                <th className="text-left py-2.5 px-3 text-muted-foreground font-medium">
                  状态
                </th>
                <th className="text-left py-2.5 px-3 text-muted-foreground font-medium">
                  时间
                </th>
              </tr>
            </thead>
            <tbody>
              {recentIncidents.map((incident) => (
                <tr
                  key={incident.id}
                  className="border-b border-border/30 hover:bg-accent/30 transition-colors cursor-pointer"
                  onClick={() => navigate(`/incidents/${incident.id}`)}
                >
                  <td className="py-2.5 px-3 font-mono text-xs">
                    {incident.id}
                  </td>
                  <td className="py-2.5 px-3">{incident.service}</td>
                  <td className="py-2.5 px-3">
                    <StatusBadge status={incident.severity} />
                  </td>
                  <td className="py-2.5 px-3">
                    <StatusBadge status={incident.status} />
                  </td>
                  <td className="py-2.5 px-3 text-muted-foreground text-xs">
                    {new Date(incident.created_at).toLocaleString('zh-CN')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
