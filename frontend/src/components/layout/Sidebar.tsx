import { NavLink } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { useAppStore } from '@/store/useAppStore';
import {
  LayoutDashboard,
  AlertTriangle,
  Bot,
  BarChart3,
  GitBranch,
  Brain,
  ChevronLeft,
  ChevronRight,
  Cpu,
  Shield,
} from 'lucide-react';

const navItems = [
  {
    to: '/',
    icon: <LayoutDashboard className="w-5 h-5" />,
    label: '仪表盘',
  },
  {
    to: '/incidents',
    icon: <AlertTriangle className="w-5 h-5" />,
    label: '故障管理',
  },
  {
    to: '/agents',
    icon: <Bot className="w-5 h-5" />,
    label: 'Agent管理',
  },
  {
    to: '/evaluation',
    icon: <BarChart3 className="w-5 h-5" />,
    label: '评估中心',
  },
  {
    to: '/topology',
    icon: <GitBranch className="w-5 h-5" />,
    label: '服务拓扑',
  },
  {
    to: '/memory',
    icon: <Brain className="w-5 h-5" />,
    label: '记忆管理',
  },
];

export function Sidebar() {
  const { sidebarCollapsed, toggleSidebar, wsConnected } = useAppStore();

  return (
    <aside
      className={cn(
        'fixed left-0 top-0 h-screen bg-card/95 backdrop-blur-md border-r border-border/50 z-40 flex flex-col transition-all duration-300 ease-in-out',
        sidebarCollapsed ? 'w-[68px]' : 'w-[220px]'
      )}
    >
      {/* Logo */}
      <div
        className={cn(
          'flex items-center h-16 border-b border-border/50 shrink-0',
          sidebarCollapsed ? 'justify-center px-2' : 'px-4 gap-3'
        )}
      >
        <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-cyan-500 shrink-0">
          <Cpu className="w-4.5 h-4.5 text-white" />
        </div>
        {!sidebarCollapsed && (
          <div className="overflow-hidden">
            <h1 className="text-sm font-bold text-foreground whitespace-nowrap">
              AIOps智能运维
            </h1>
            <p className="text-[10px] text-muted-foreground whitespace-nowrap">
              多智能体平台
            </p>
          </div>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4 px-2 space-y-1 overflow-y-auto scrollbar-thin">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 group',
                isActive
                  ? 'bg-primary/10 text-primary border border-primary/20'
                  : 'text-muted-foreground hover:text-foreground hover:bg-accent',
                sidebarCollapsed && 'justify-center px-2'
              )
            }
            title={sidebarCollapsed ? item.label : undefined}
          >
            <span className="shrink-0">{item.icon}</span>
            {!sidebarCollapsed && <span className="truncate">{item.label}</span>}
          </NavLink>
        ))}
      </nav>

      {/* Bottom Section */}
      <div className="border-t border-border/50 p-2 space-y-2">
        {/* Connection Status */}
        <div
          className={cn(
            'flex items-center gap-2 px-3 py-2 rounded-lg',
            sidebarCollapsed && 'justify-center px-2'
          )}
          title={sidebarCollapsed ? (wsConnected ? 'WebSocket 已连接' : 'WebSocket 未连接') : undefined}
        >
          <Shield
            className={cn(
              'w-4 h-4 shrink-0',
              wsConnected ? 'text-emerald-400' : 'text-red-400'
            )}
          />
          {!sidebarCollapsed && (
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  'w-1.5 h-1.5 rounded-full',
                  wsConnected ? 'bg-emerald-400' : 'bg-red-400 animate-pulse'
                )}
              />
              <span className="text-xs text-muted-foreground">
                {wsConnected ? '实时连接' : '离线'}
              </span>
            </div>
          )}
        </div>

        {/* Toggle Button */}
        <button
          onClick={toggleSidebar}
          className={cn(
            'flex items-center gap-2 px-3 py-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition-colors w-full',
            sidebarCollapsed && 'justify-center px-2'
          )}
        >
          {sidebarCollapsed ? (
            <ChevronRight className="w-4 h-4" />
          ) : (
            <>
              <ChevronLeft className="w-4 h-4" />
              <span className="text-xs">收起侧边栏</span>
            </>
          )}
        </button>
      </div>
    </aside>
  );
}
