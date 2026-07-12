import { cn } from '@/lib/utils';
import {
  Activity,
  BrainCircuit,
  Wrench,
  GitPullRequest,
  BookOpen,
  BarChart3,
} from 'lucide-react';
import type { Agent } from '@/types';

interface AgentStatusProps {
  agents: Agent[];
  className?: string;
}

const agentIcons: Record<string, React.ReactNode> = {
  'monitor-agent': <Activity className="w-5 h-5" />,
  'rca-agent': <BrainCircuit className="w-5 h-5" />,
  'heal-agent': <Wrench className="w-5 h-5" />,
  'change-agent': <GitPullRequest className="w-5 h-5" />,
  'knowledge-agent': <BookOpen className="w-5 h-5" />,
  'eval-agent': <BarChart3 className="w-5 h-5" />,
};

const statusColors: Record<string, { bg: string; dot: string; label: string }> = {
  running: {
    bg: 'bg-emerald-500/10 border-emerald-500/30',
    dot: 'bg-emerald-400',
    label: '运行中',
  },
  stopped: {
    bg: 'bg-slate-500/10 border-slate-500/30',
    dot: 'bg-slate-400',
    label: '已停止',
  },
  error: {
    bg: 'bg-red-500/10 border-red-500/30',
    dot: 'bg-red-400 animate-pulse',
    label: '错误',
  },
};

export function AgentStatusOverview({ agents, className }: AgentStatusProps) {
  return (
    <div className={cn('grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3', className)}>
      {agents.map((agent) => {
        const statusConfig = statusColors[agent.status] || statusColors.stopped;

        return (
          <div
            key={agent.id}
            className={cn(
              'flex flex-col items-center p-4 rounded-lg border transition-all duration-200',
              statusConfig.bg,
              'hover:shadow-lg hover:scale-[1.02]'
            )}
          >
            <div className="relative">
              <div className="w-10 h-10 rounded-full bg-background/50 flex items-center justify-center text-foreground/80">
                {agentIcons[agent.id] || <Activity className="w-5 h-5" />}
              </div>
              <span
                className={cn(
                  'absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-background',
                  statusConfig.dot
                )}
              />
            </div>
            <span className="mt-2 text-xs font-medium text-foreground text-center">
              {agent.name}
            </span>
            <span className="text-[10px] text-muted-foreground mt-0.5">
              {statusConfig.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
