import { cn } from '@/lib/utils';
import { ArrowUpRight, ArrowDownRight } from 'lucide-react';
import type { ReactNode } from 'react';

interface StatCardProps {
  title: string;
  value: string | number;
  trend?: number;
  trendLabel?: string;
  icon: ReactNode;
  iconColor?: string;
  iconBgColor?: string;
  className?: string;
  onClick?: () => void;
}

export function StatCard({
  title,
  value,
  trend,
  trendLabel,
  icon,
  iconColor = 'text-blue-400',
  iconBgColor = 'bg-blue-400/10',
  className,
  onClick,
}: StatCardProps) {
  const isPositive = trend && trend > 0;
  const isNegative = trend && trend < 0;

  return (
    <div
      className={cn(
        'glass-card p-5 transition-all duration-200 hover:shadow-xl hover:border-border/80',
        onClick && 'cursor-pointer',
        className
      )}
      onClick={onClick}
    >
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-muted-foreground truncate">
            {title}
          </p>
          <p className="mt-2 text-2xl font-bold text-foreground tracking-tight">
            {value}
          </p>
          {trend !== undefined && (
            <div className="flex items-center mt-2 gap-1.5">
              <span
                className={cn(
                  'inline-flex items-center text-xs font-medium',
                  isPositive && 'text-emerald-400',
                  isNegative && 'text-red-400',
                  !isPositive && !isNegative && 'text-muted-foreground'
                )}
              >
                {isPositive && <ArrowUpRight className="w-3 h-3 mr-0.5" />}
                {isNegative && <ArrowDownRight className="w-3 h-3 mr-0.5" />}
                {trend > 0 ? '+' : ''}
                {trend}%
              </span>
              {trendLabel && (
                <span className="text-xs text-muted-foreground">
                  {trendLabel}
                </span>
              )}
            </div>
          )}
        </div>
        <div
          className={cn(
            'flex items-center justify-center w-10 h-10 rounded-lg shrink-0',
            iconBgColor
          )}
        >
          <span className={iconColor}>{icon}</span>
        </div>
      </div>
    </div>
  );
}
