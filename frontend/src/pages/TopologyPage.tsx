import { useCallback, useEffect, useRef, useState } from 'react';
import { StatusBadge } from '@/components/common/StatusBadge';
import { useTopology } from '@/hooks/useApi';
import {
  Server,
  Database,
  Globe,
  Box,
  Activity,
  ArrowRight,
  Wifi,
  AlertTriangle,
  Search,
} from 'lucide-react';

interface ServiceNode {
  id: string;
  name: string;
  type: 'service' | 'database' | 'gateway' | 'cache' | 'mq';
  status: 'healthy' | 'warning' | 'critical' | 'unknown';
  x: number;
  y: number;
  metrics: {
    cpu: number;
    memory: number;
    latency: number;
    error_rate: number;
  };
}

interface ServiceEdge {
  source: string;
  target: string;
  type: 'http' | 'rpc' | 'db' | 'cache' | 'mq';
  status: 'healthy' | 'warning' | 'critical';
}

const layoutPositions = [
  [400, 60], [180, 165], [400, 165], [620, 165],
  [110, 300], [300, 300], [500, 300], [690, 300],
  [180, 430], [400, 430], [620, 430],
] as const;

function parseMetric(value: unknown): number {
  if (typeof value === 'number') return value;
  if (typeof value !== 'string') return 0;
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function inferNodeType(id: string): ServiceNode['type'] {
  if (id.includes('gateway')) return 'gateway';
  if (id.includes('mysql') || id.includes('postgres') || id.includes('elastic')) return 'database';
  if (id.includes('redis') || id.includes('cache')) return 'cache';
  if (id.includes('kafka') || id.includes('mq')) return 'mq';
  return 'service';
}

const nodeIcons: Record<string, React.ReactNode> = {
  gateway: <Globe className="w-5 h-5" />,
  service: <Box className="w-5 h-5" />,
  database: <Database className="w-5 h-5" />,
  cache: <Activity className="w-5 h-5" />,
  mq: <Server className="w-5 h-5" />,
};

const statusColors: Record<string, { bg: string; border: string; text: string }> = {
  healthy: { bg: 'bg-emerald-500/10', border: 'border-emerald-500/40', text: 'text-emerald-400' },
  warning: { bg: 'bg-amber-500/10', border: 'border-amber-500/40', text: 'text-amber-400' },
  critical: { bg: 'bg-red-500/10', border: 'border-red-500/40', text: 'text-red-400' },
  unknown: { bg: 'bg-slate-500/10', border: 'border-slate-500/40', text: 'text-slate-400' },
};

const edgeColors: Record<string, string> = {
  healthy: '#22c55e',
  warning: '#f59e0b',
  critical: '#ef4444',
};

export function TopologyPage() {
  const svgRef = useRef<SVGSVGElement>(null);
  const { getTopology, loading, error } = useTopology();
  const [nodes, setNodes] = useState<ServiceNode[]>([]);
  const [edges, setEdges] = useState<ServiceEdge[]>([]);
  const [selectedNode, setSelectedNode] = useState<ServiceNode | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  const loadTopology = useCallback(async () => {
    try {
      const result = await getTopology();
      const mappedNodes: ServiceNode[] = result.nodes.map((raw, index) => {
        const id = String(raw.id || raw.service || `service-${index}`);
        const metrics = (raw.metrics || {}) as Record<string, unknown>;
        const [x, y] = layoutPositions[index % layoutPositions.length];
        return {
          id,
          name: String(raw.name || id),
          type: inferNodeType(id),
          status: (raw.status as ServiceNode['status']) || 'unknown',
          x,
          y,
          metrics: {
            cpu: parseMetric(metrics.cpu),
            memory: parseMetric(metrics.memory),
            latency: parseMetric(metrics.latency),
            error_rate: parseMetric(metrics.error_rate),
          },
        };
      });
      const statusById = new Map(mappedNodes.map((node) => [node.id, node.status]));
      const mappedEdges: ServiceEdge[] = result.edges.map((raw) => {
        const source = String(raw.source || '');
        const target = String(raw.target || '');
        const endpointStatuses = [statusById.get(source), statusById.get(target)];
        const status: ServiceEdge['status'] = endpointStatuses.includes('critical')
          ? 'critical'
          : endpointStatuses.includes('warning')
          ? 'warning'
          : 'healthy';
        return {
          source,
          target,
          type: (raw.type as ServiceEdge['type']) || 'http',
          status,
        };
      });
      setNodes(mappedNodes);
      setEdges(mappedEdges);
    } catch {
      setNodes([]);
      setEdges([]);
    }
  }, [getTopology]);

  useEffect(() => {
    void loadTopology();
  }, [loadTopology]);

  const filteredNodes = nodes.filter(
    (n) =>
      searchQuery === '' ||
      n.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const getConnectedEdges = (nodeId: string) =>
    edges.filter((e) => e.source === nodeId || e.target === nodeId);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">服务拓扑</h1>
          <p className="text-sm text-muted-foreground mt-1">
            微服务架构依赖关系和实时状态
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Wifi className={`w-4 h-4 ${error ? 'text-red-400' : 'text-emerald-400'}`} />
          <span>{loading ? '加载中' : error ? '数据不可用' : '已连接后端数据'}</span>
        </div>
      </div>

      {/* Topology Graph */}
      <div className="glass-card p-4">
        <div className="flex items-center gap-3 mb-4">
          <div className="relative flex-1 max-w-xs">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <input
              type="text"
              placeholder="搜索服务..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-4 py-2 bg-background border border-input rounded-lg text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div className="flex items-center gap-4 text-xs text-muted-foreground">
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-emerald-400" />
              正常
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-amber-400" />
              警告
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-red-400" />
              严重
            </div>
          </div>
        </div>

        <div className="relative overflow-auto bg-muted/10 rounded-lg border border-border/50" style={{ minHeight: 520 }}>
          <svg
            ref={svgRef}
            viewBox="0 0 800 520"
            className="w-full h-full"
            style={{ minHeight: 520 }}
          >
            {/* Edges */}
            {edges.map((edge, idx) => {
              const source = nodes.find((n) => n.id === edge.source);
              const target = nodes.find((n) => n.id === edge.target);
              if (!source || !target) return null;

              const isHighlighted =
                selectedNode &&
                (edge.source === selectedNode.id || edge.target === selectedNode.id);

              return (
                <g key={`edge-${idx}`}>
                  <line
                    x1={source.x}
                    y1={source.y}
                    x2={target.x}
                    y2={target.y}
                    stroke={edgeColors[edge.status]}
                    strokeWidth={isHighlighted ? 2.5 : 1.5}
                    opacity={selectedNode && !isHighlighted ? 0.2 : 0.8}
                    strokeDasharray={edge.type === 'rpc' ? '6,4' : undefined}
                  />
                  {/* Arrow */}
                  <polygon
                    points={`${target.x - 8},${target.y - 8} ${target.x + 8},${target.y} ${target.x - 8},${target.y + 8}`}
                    fill={edgeColors[edge.status]}
                    opacity={0}
                    transform={`rotate(${Math.atan2(target.y - source.y, target.x - source.x) * 180 / Math.PI} ${(source.x + target.x) / 2} ${(source.y + target.y) / 2}) translate(${(source.x + target.x) / 2 - target.x} ${(source.y + target.y) / 2 - target.y})`}
                  />
                </g>
              );
            })}

            {/* Nodes */}
            {filteredNodes.map((node) => {
              const colors = statusColors[node.status];
              const isSelected = selectedNode?.id === node.id;

              return (
                <g
                  key={node.id}
                  transform={`translate(${node.x}, ${node.y})`}
                  className="cursor-pointer"
                  onClick={() => setSelectedNode(isSelected ? null : node)}
                >
                  {/* Glow effect */}
                  {isSelected && (
                    <circle
                      r="38"
                      fill="none"
                      stroke="hsl(var(--primary))"
                      strokeWidth="2"
                      opacity="0.5"
                      className="animate-pulse-slow"
                    />
                  )}
                  {/* Node circle */}
                  <circle
                    r="32"
                    className={`${colors.bg} ${colors.border}`}
                    strokeWidth="1.5"
                  />
                  <circle
                    r="30"
                    fill="none"
                    className={colors.border.replace('border-', 'stroke-')}
                    strokeWidth="1"
                    opacity="0.5"
                  />
                  {/* Icon */}
                  <foreignObject x="-10" y="-14" width="20" height="20">
                    <div className={`flex items-center justify-center ${colors.text}`}>
                      {nodeIcons[node.type]}
                    </div>
                  </foreignObject>
                  {/* Label */}
                  <text
                    y="48"
                    textAnchor="middle"
                    className="text-xs fill-foreground font-medium"
                  >
                    {node.name.length > 12
                      ? node.name.substring(0, 10) + '...'
                      : node.name}
                  </text>
                  {/* Status dot */}
                  <circle
                    cx="22"
                    cy="-22"
                    r="5"
                    className={
                      node.status === 'healthy'
                        ? 'fill-emerald-400'
                        : node.status === 'warning'
                        ? 'fill-amber-400'
                        : 'fill-red-400'
                    }
                  />
                </g>
              );
            })}
          </svg>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Selected Node Detail */}
        {selectedNode && (
          <div className="glass-card p-5 animate-slide-in">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <div
                  className={`w-10 h-10 rounded-lg flex items-center justify-center ${statusColors[selectedNode.status].bg} ${statusColors[selectedNode.status].text}`}
                >
                  {nodeIcons[selectedNode.type]}
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-foreground">
                    {selectedNode.name}
                  </h3>
                  <span className="text-xs text-muted-foreground">
                    {selectedNode.type.toUpperCase()}
                  </span>
                </div>
              </div>
              <StatusBadge status={selectedNode.status} />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="p-3 bg-muted/20 rounded-lg">
                <p className="text-xs text-muted-foreground">CPU</p>
                <div className="flex items-center gap-2 mt-1">
                  <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full ${
                        selectedNode.metrics.cpu > 80
                          ? 'bg-red-400'
                          : selectedNode.metrics.cpu > 60
                          ? 'bg-amber-400'
                          : 'bg-emerald-400'
                      }`}
                      style={{ width: `${selectedNode.metrics.cpu}%` }}
                    />
                  </div>
                  <span className="text-sm font-medium">
                    {selectedNode.metrics.cpu}%
                  </span>
                </div>
              </div>
              <div className="p-3 bg-muted/20 rounded-lg">
                <p className="text-xs text-muted-foreground">内存</p>
                <div className="flex items-center gap-2 mt-1">
                  <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full ${
                        selectedNode.metrics.memory > 80
                          ? 'bg-red-400'
                          : selectedNode.metrics.memory > 60
                          ? 'bg-amber-400'
                          : 'bg-emerald-400'
                      }`}
                      style={{ width: `${selectedNode.metrics.memory}%` }}
                    />
                  </div>
                  <span className="text-sm font-medium">
                    {selectedNode.metrics.memory}%
                  </span>
                </div>
              </div>
              <div className="p-3 bg-muted/20 rounded-lg">
                <p className="text-xs text-muted-foreground">延迟</p>
                <p className="text-lg font-semibold text-foreground">
                  {selectedNode.metrics.latency}ms
                </p>
              </div>
              <div className="p-3 bg-muted/20 rounded-lg">
                <p className="text-xs text-muted-foreground">错误率</p>
                <p
                  className={`text-lg font-semibold ${
                    selectedNode.metrics.error_rate > 1
                      ? 'text-red-400'
                      : 'text-emerald-400'
                  }`}
                >
                  {selectedNode.metrics.error_rate}%
                </p>
              </div>
            </div>

            {/* Connected services */}
            <div className="mt-4">
              <p className="text-xs text-muted-foreground mb-2">依赖关系</p>
              <div className="space-y-1.5">
                {getConnectedEdges(selectedNode.id).map((edge, idx) => {
                  const otherId =
                    edge.source === selectedNode.id ? edge.target : edge.source;
                  const other = nodes.find((n) => n.id === otherId);
                  if (!other) return null;
                  return (
                    <div
                      key={idx}
                      className="flex items-center gap-2 text-sm text-muted-foreground"
                    >
                      {edge.source === selectedNode.id ? (
                        <>
                          <ArrowRight className="w-3.5 h-3.5" />
                          <span>调用</span>
                        </>
                      ) : (
                        <>
                          <ArrowRight className="w-3.5 h-3.5 rotate-180" />
                          <span>被调用</span>
                        </>
                      )}
                      <span className="font-medium text-foreground">
                        {other.name}
                      </span>
                      <span className="text-xs">({edge.type})</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}

        {/* Service List */}
        <div className="glass-card p-5">
          <h3 className="text-base font-semibold text-foreground mb-4">
            服务列表
          </h3>
          <div className="space-y-2 max-h-[400px] overflow-y-auto scrollbar-thin pr-1">
            {nodes.map((node) => (
              <div
                key={node.id}
                className={`flex items-center gap-3 p-3 rounded-lg cursor-pointer transition-colors ${
                  selectedNode?.id === node.id
                    ? 'bg-primary/10 border border-primary/20'
                    : 'bg-muted/20 hover:bg-muted/40'
                }`}
                onClick={() =>
                  setSelectedNode(
                    selectedNode?.id === node.id ? null : node
                  )
                }
              >
                <div
                  className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${statusColors[node.status].bg} ${statusColors[node.status].text}`}
                >
                  {nodeIcons[node.type]}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-foreground truncate">
                      {node.name}
                    </span>
                    <StatusBadge status={node.status} />
                  </div>
                  <div className="flex items-center gap-3 mt-0.5">
                    <span className="text-[10px] text-muted-foreground">
                      CPU {node.metrics.cpu}%
                    </span>
                    <span className="text-[10px] text-muted-foreground">
                      MEM {node.metrics.memory}%
                    </span>
                    <span className="text-[10px] text-muted-foreground">
                      {node.metrics.latency}ms
                    </span>
                  </div>
                </div>
                {node.status === 'critical' && (
                  <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
