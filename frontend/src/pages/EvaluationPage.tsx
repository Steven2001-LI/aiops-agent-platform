import { useState, useEffect, useCallback } from 'react';
import { useAppStore } from '@/store/useAppStore';
import { useEvaluations } from '@/hooks/useApi';
import { ScoreGauge } from '@/components/evaluation/ScoreGauge';
import {
  BarChart3,
  TrendingUp,
  Target,
  Cpu,
  Database,
  Sparkles,
  Play,
  CheckCircle2,
  Clock,
  ChevronRight,
} from 'lucide-react';
import {
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts';

const dimensionMeta: Record<
  string,
  { label: string; icon: React.ReactNode; color: string; description: string }
> = {
  end_to_end: {
    label: '端到端评估',
    icon: <Target className="w-5 h-5" />,
    color: '#3b82f6',
    description: '从故障发现到修复完成的整体流程评估',
  },
  reasoning: {
    label: '推理评估',
    icon: <Sparkles className="w-5 h-5" />,
    color: '#f59e0b',
    description: '根因分析推理能力和置信度校准评估',
  },
  tool_calling: {
    label: '工具调用',
    icon: <Cpu className="w-5 h-5" />,
    color: '#8b5cf6',
    description: '工具选择准确率和参数正确率评估',
  },
  rag: {
    label: 'RAG评估',
    icon: <Database className="w-5 h-5" />,
    color: '#22c55e',
    description: '检索精确率和生成回答质量评估',
  },
};

const historyData = [
  { date: '01-08', end_to_end: 82, reasoning: 88, tool_calling: 75, rag: 80 },
  { date: '01-09', end_to_end: 84, reasoning: 89, tool_calling: 76, rag: 81 },
  { date: '01-10', end_to_end: 83, reasoning: 90, tool_calling: 74, rag: 83 },
  { date: '01-11', end_to_end: 85, reasoning: 89, tool_calling: 77, rag: 82 },
  { date: '01-12', end_to_end: 86, reasoning: 91, tool_calling: 78, rag: 85 },
  { date: '01-13', end_to_end: 85, reasoning: 90, tool_calling: 79, rag: 84 },
  { date: '01-14', end_to_end: 86, reasoning: 91, tool_calling: 79, rag: 85 },
  { date: '01-15', end_to_end: 88, reasoning: 91, tool_calling: 79, rag: 85 },
];

const radarData = [
  { metric: '任务成功率', end_to_end: 92, reasoning: 0, tool_calling: 0, rag: 0 },
  { metric: 'MTTR', end_to_end: 85, reasoning: 0, tool_calling: 0, rag: 0 },
  { metric: '自动化率', end_to_end: 82, reasoning: 0, tool_calling: 0, rag: 0 },
  { metric: '根因准确率', end_to_end: 0, reasoning: 94, tool_calling: 0, rag: 0 },
  { metric: '置信度校准', end_to_end: 0, reasoning: 88, tool_calling: 0, rag: 0 },
  { metric: '工具选择', end_to_end: 0, reasoning: 0, tool_calling: 82, rag: 0 },
  { metric: '参数正确率', end_to_end: 0, reasoning: 0, tool_calling: 76, rag: 0 },
  { metric: '检索精确率', end_to_end: 0, reasoning: 0, tool_calling: 0, rag: 88 },
  { metric: '回答质量', end_to_end: 0, reasoning: 0, tool_calling: 0, rag: 82 },
];

export function EvaluationPage() {
  const { evaluations: storeEvals, setEvaluations } = useAppStore();
  const { listEvaluations, runEvaluation } = useEvaluations();
  const [selectedDimension, setSelectedDimension] = useState<string | null>(null);
  const [runningEval, setRunningEval] = useState(false);

  // Load evaluations from API
  const loadEvaluations = useCallback(async () => {
    try {
      const result = await listEvaluations();
      if (result?.items) {
        const mapped = result.items.map((item: Record<string, unknown>) => ({
          id: (item.id as string) || '',
          dimension: (item.dimension as string) || (item.eval_type as string) || '',
          overall_score: (item.overall_score as number) || 0,
          metrics: ((item.metrics as Array<{ name: string; score: number; weight: number }>) || []),
          timestamp: (item.timestamp as string) || new Date().toISOString(),
        }));
        setEvaluations(mapped);
      }
    } catch (_e) {
      console.debug('[EvaluationPage] Using store fallback');
    }
  }, [listEvaluations, setEvaluations]);

  useEffect(() => {
    loadEvaluations();
  }, [loadEvaluations]);

  const evaluations = storeEvals.length > 0 ? storeEvals : [];

  const overallScore = evaluations.length > 0
    ? evaluations.reduce((sum, e) => sum + e.overall_score, 0) / evaluations.length
    : 85.7;

  const handleRunEval = async () => {
    setRunningEval(true);
    try {
      // Call the real API
      await runEvaluation('end_to_end');
      // Reload evaluations after a brief delay
      setTimeout(async () => {
        await loadEvaluations();
        setRunningEval(false);
      }, 2000);
    } catch (_e) {
      // Fallback: simulate with timeout
      setTimeout(() => setRunningEval(false), 3000);
    }
  };

  const selectedEval = evaluations.find(
    (e) => e.dimension === selectedDimension
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">评估中心</h1>
          <p className="text-sm text-muted-foreground mt-1">
            多维度评估智能体系统性能
          </p>
        </div>
        <button
          onClick={handleRunEval}
          disabled={runningEval}
          className="inline-flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {runningEval ? (
            <>
              <div className="w-4 h-4 border-2 border-primary-foreground/30 border-t-primary-foreground rounded-full animate-spin" />
              评估中...
            </>
          ) : (
            <>
              <Play className="w-4 h-4" />
              运行评估
            </>
          )}
        </button>
      </div>

      {/* Overall Score */}
      <div className="glass-card p-6">
        <div className="flex flex-col md:flex-row items-center gap-8">
          <ScoreGauge score={overallScore} size={160} strokeWidth={10} />
          <div className="flex-1">
            <h3 className="text-lg font-semibold text-foreground mb-2">
              综合评分
            </h3>
            <p className="text-sm text-muted-foreground mb-4">
              基于端到端、推理、工具调用和RAG四个维度的加权综合评分
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              {evaluations.map((evalItem) => {
                const meta = dimensionMeta[evalItem.dimension];
                return (
                  <div
                    key={evalItem.id}
                    className={`p-3 rounded-lg border cursor-pointer transition-all ${
                      selectedDimension === evalItem.dimension
                        ? 'border-primary/50 bg-primary/5'
                        : 'border-border/50 bg-muted/20 hover:bg-muted/40'
                    }`}
                    onClick={() =>
                      setSelectedDimension(
                        evalItem.dimension === selectedDimension
                          ? null
                          : evalItem.dimension
                      )
                    }
                  >
                    <div
                      className="w-8 h-8 rounded-lg flex items-center justify-center mb-2"
                      style={{ color: meta?.color, backgroundColor: `${meta?.color}15` }}
                    >
                      {meta?.icon}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {meta?.label || evalItem.dimension}
                    </p>
                    <p
                      className="text-lg font-bold"
                      style={{ color: meta?.color }}
                    >
                      {evalItem.overall_score.toFixed(1)}
                    </p>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {/* Dimension Detail */}
      {selectedEval && (
        <div className="glass-card p-6 animate-slide-in">
          <div className="flex items-center gap-3 mb-4">
            <div
              className="w-10 h-10 rounded-lg flex items-center justify-center"
              style={{
                color: dimensionMeta[selectedEval.dimension]?.color,
                backgroundColor: `${dimensionMeta[selectedEval.dimension]?.color}15`,
              }}
            >
              {dimensionMeta[selectedEval.dimension]?.icon}
            </div>
            <div>
              <h3 className="text-lg font-semibold text-foreground">
                {dimensionMeta[selectedEval.dimension]?.label}
              </h3>
              <p className="text-xs text-muted-foreground">
                {dimensionMeta[selectedEval.dimension]?.description}
              </p>
            </div>
            <ScoreGauge
              score={selectedEval.overall_score}
              size={80}
              strokeWidth={6}
              showValue
              className="ml-auto"
            />
          </div>

          <div className="space-y-3">
            {selectedEval.metrics.map((metric) => (
              <div key={metric.name}>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm text-muted-foreground">
                    {metric.name}
                  </span>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">
                      权重 {metric.weight}
                    </span>
                    <span className="text-sm font-medium text-foreground">
                      {metric.score}
                    </span>
                  </div>
                </div>
                <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${metric.score}%`,
                      backgroundColor:
                        metric.score >= 85
                          ? '#22c55e'
                          : metric.score >= 60
                          ? '#f59e0b'
                          : '#ef4444',
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Radar Chart */}
        <div className="glass-card p-5">
          <div className="flex items-center gap-2 mb-4">
            <BarChart3 className="w-5 h-5 text-primary" />
            <h3 className="text-base font-semibold text-foreground">
              能力雷达图
            </h3>
          </div>
          <ResponsiveContainer width="100%" height={300}>
            <RadarChart data={radarData}>
              <PolarGrid stroke="hsl(var(--border))" />
              <PolarAngleAxis
                dataKey="metric"
                tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
              />
              <PolarRadiusAxis
                angle={90}
                domain={[0, 100]}
                tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 10 }}
              />
              <Radar
                name="端到端"
                dataKey="end_to_end"
                stroke="#3b82f6"
                fill="#3b82f6"
                fillOpacity={0.15}
                strokeWidth={2}
              />
              <Radar
                name="推理"
                dataKey="reasoning"
                stroke="#f59e0b"
                fill="#f59e0b"
                fillOpacity={0.15}
                strokeWidth={2}
              />
              <Radar
                name="工具调用"
                dataKey="tool_calling"
                stroke="#8b5cf6"
                fill="#8b5cf6"
                fillOpacity={0.15}
                strokeWidth={2}
              />
              <Radar
                name="RAG"
                dataKey="rag"
                stroke="#22c55e"
                fill="#22c55e"
                fillOpacity={0.15}
                strokeWidth={2}
              />
              <Legend />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'hsl(var(--card))',
                  border: '1px solid hsl(var(--border))',
                  borderRadius: '8px',
                }}
              />
            </RadarChart>
          </ResponsiveContainer>
        </div>

        {/* Trend Chart */}
        <div className="glass-card p-5">
          <div className="flex items-center gap-2 mb-4">
            <TrendingUp className="w-5 h-5 text-primary" />
            <h3 className="text-base font-semibold text-foreground">
              历史趋势
            </h3>
          </div>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={historyData}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
              <XAxis
                dataKey="date"
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
              <Legend />
              <Line
                type="monotone"
                dataKey="end_to_end"
                name="端到端"
                stroke="#3b82f6"
                strokeWidth={2}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="reasoning"
                name="推理"
                stroke="#f59e0b"
                strokeWidth={2}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="tool_calling"
                name="工具调用"
                stroke="#8b5cf6"
                strokeWidth={2}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="rag"
                name="RAG"
                stroke="#22c55e"
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Evaluations List */}
      <div className="glass-card p-5">
        <h3 className="text-base font-semibold text-foreground mb-4">
          评估报告列表
        </h3>
        <div className="space-y-2">
          {evaluations.map((evalItem) => {
            const meta = dimensionMeta[evalItem.dimension];
            return (
              <div
                key={evalItem.id}
                className="flex items-center gap-4 p-3 rounded-lg bg-muted/20 hover:bg-muted/40 transition-colors cursor-pointer"
                onClick={() => setSelectedDimension(evalItem.dimension)}
              >
                <div
                  className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0"
                  style={{
                    color: meta?.color,
                    backgroundColor: `${meta?.color}15`,
                  }}
                >
                  {meta?.icon}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-foreground">
                      {meta?.label || evalItem.dimension}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {evalItem.id}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 mt-0.5">
                    <span className="text-xs text-muted-foreground flex items-center gap-1">
                      <Clock className="w-3 h-3" />
                      {new Date(evalItem.timestamp).toLocaleString('zh-CN')}
                    </span>
                    <span className="text-xs text-muted-foreground flex items-center gap-1">
                      <CheckCircle2 className="w-3 h-3" />
                      {evalItem.metrics.length} 项指标
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <span
                    className="text-lg font-bold"
                    style={{ color: meta?.color }}
                  >
                    {evalItem.overall_score.toFixed(1)}
                  </span>
                  <ChevronRight className="w-4 h-4 text-muted-foreground" />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
