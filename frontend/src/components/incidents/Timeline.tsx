import { cn } from '@/lib/utils';
import {
  Activity,
  Search,
  BrainCircuit,
  Wrench,
  GitPullRequest,
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
} from 'lucide-react';
import type { IncidentStatus } from '@/types';

interface TimelineItem {
  agent: string;
  status: 'completed' | 'in_progress' | 'pending' | 'failed';
  time?: string;
  detail?: string;
}

interface TimelineProps {
  items: TimelineItem[];
  currentStatus: IncidentStatus;
  className?: string;
}

const agentIcons: Record<string, React.ReactNode> = {
  MonitorAgent: <Activity className="w-4 h-4" />,
  RCAAgent: <BrainCircuit className="w-4 h-4" />,
  HealAgent: <Wrench className="w-4 h-4" />,
  ChangeAgent: <GitPullRequest className="w-4 h-4" />,
};

const agentLabels: Record<string, string> = {
  MonitorAgent: '监控检测',
  RCAAgent: '根因分析',
  HealAgent: '故障恢复',
  ChangeAgent: '变更审批',
};

const statusIcons = {
  completed: <CheckCircle2 className="w-5 h-5 text-emerald-500" />,
  in_progress: <Loader2 className="w-5 h-5 text-blue-400 animate-spin" />,
  pending: <Clock className="w-5 h-5 text-slate-500" />,
  failed: <XCircle className="w-5 h-5 text-red-500" />,
};

const statusColors = {
  completed: 'border-emerald-500/50 bg-emerald-500/10',
  in_progress: 'border-blue-500/50 bg-blue-500/10',
  pending: 'border-slate-600/50 bg-slate-500/5',
  failed: 'border-red-500/50 bg-red-500/10',
};

const connectorColors = {
  completed: 'bg-emerald-500/40',
  in_progress: 'bg-gradient-to-b from-emerald-500/40 to-slate-600/30',
  pending: 'bg-slate-700/30',
  failed: 'bg-red-500/40',
};

export function Timeline({ items, className }: TimelineProps) {
  return (
    <div className={cn('relative', className)}>
      {/* Vertical line */}
      <div className="absolute left-[19px] top-6 bottom-6 w-0.5 bg-border/50" />

      <div className="space-y-0">
        {items.map((item, index) => (
          <div key={item.agent} className="relative flex gap-4">
            {/* Connector line segment */}
            {index < items.length - 1 && (
              <div
                className={cn(
                  'absolute left-[19px] top-10 w-0.5 h-[calc(100%-20px)]',
                  connectorColors[item.status]
                )}
              />
            )}

            {/* Icon circle */}
            <div
              className={cn(
                'relative z-10 flex items-center justify-center w-10 h-10 rounded-full border-2 shrink-0',
                statusColors[item.status]
              )}
            >
              {statusIcons[item.status]}
            </div>

            {/* Content */}
            <div className="flex-1 pb-6 pt-1">
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">
                  {agentIcons[item.agent] || <Search className="w-4 h-4" />}
                </span>
                <span className="font-medium text-sm">
                  {agentLabels[item.agent] || item.agent}
                </span>
                {item.time && (
                  <span className="text-xs text-muted-foreground ml-auto">
                    {new Date(item.time).toLocaleTimeString('zh-CN')}
                  </span>
                )}
              </div>
              {item.detail && (
                <p className="mt-1.5 text-sm text-muted-foreground pl-6">
                  {item.detail}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
