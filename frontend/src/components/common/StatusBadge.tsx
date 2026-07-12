import { cn } from '@/lib/utils';
import type { IncidentStatus, Severity, AgentStatus } from '@/types';

interface StatusBadgeProps {
  status: IncidentStatus | Severity | AgentStatus | string;
  type?: 'status' | 'severity' | 'agent';
  className?: string;
  showDot?: boolean;
}

const statusConfig: Record<
  string,
  { bg: string; text: string; dot: string; label: string }
> = {
  // Incident status
  pending: {
    bg: 'bg-slate-500/15',
    text: 'text-slate-400',
    dot: 'bg-slate-400',
    label: '待处理',
  },
  detecting: {
    bg: 'bg-blue-500/15',
    text: 'text-blue-400',
    dot: 'bg-blue-400 animate-pulse',
    label: '检测中',
  },
  analyzing: {
    bg: 'bg-amber-500/15',
    text: 'text-amber-400',
    dot: 'bg-amber-400 animate-pulse',
    label: '分析中',
  },
  healing: {
    bg: 'bg-purple-500/15',
    text: 'text-purple-400',
    dot: 'bg-purple-400 animate-pulse',
    label: '修复中',
  },
  resolved: {
    bg: 'bg-emerald-500/15',
    text: 'text-emerald-400',
    dot: 'bg-emerald-400',
    label: '已解决',
  },
  failed: {
    bg: 'bg-red-500/15',
    text: 'text-red-400',
    dot: 'bg-red-400',
    label: '失败',
  },
  // Severity
  critical: {
    bg: 'bg-red-500/15',
    text: 'text-red-400',
    dot: 'bg-red-500',
    label: '严重',
  },
  high: {
    bg: 'bg-orange-500/15',
    text: 'text-orange-400',
    dot: 'bg-orange-400',
    label: '高危',
  },
  medium: {
    bg: 'bg-yellow-500/15',
    text: 'text-yellow-400',
    dot: 'bg-yellow-400',
    label: '中危',
  },
  low: {
    bg: 'bg-blue-500/15',
    text: 'text-blue-400',
    dot: 'bg-blue-400',
    label: '低危',
  },
  // Agent status
  running: {
    bg: 'bg-emerald-500/15',
    text: 'text-emerald-400',
    dot: 'bg-emerald-400',
    label: '运行中',
  },
  stopped: {
    bg: 'bg-slate-500/15',
    text: 'text-slate-400',
    dot: 'bg-slate-400',
    label: '已停止',
  },
  error: {
    bg: 'bg-red-500/15',
    text: 'text-red-400',
    dot: 'bg-red-400',
    label: '错误',
  },
};

export function StatusBadge({
  status,
  className,
  showDot = true,
}: StatusBadgeProps) {
  const config = statusConfig[status] || {
    bg: 'bg-gray-500/15',
    text: 'text-gray-400',
    dot: 'bg-gray-400',
    label: status,
  };

  return (
    <span
      className={cn(
        'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium',
        config.bg,
        config.text,
        className
      )}
    >
      {showDot && <span className={cn('w-1.5 h-1.5 rounded-full mr-1.5', config.dot)} />}
      {config.label}
    </span>
  );
}
