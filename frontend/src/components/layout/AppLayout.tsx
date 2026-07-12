import { Outlet } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { useAppStore } from '@/store/useAppStore';
import { Sidebar } from './Sidebar';
import { Bell, Settings, User } from 'lucide-react';
import { useWebSocket } from '@/hooks/useWebSocket';

export function AppLayout() {
  const { sidebarCollapsed } = useAppStore();
  // WebSocket 在 hook 内部自动连接，这里只需调用 hook
  useWebSocket();

  return (
    <div className="min-h-screen bg-background">
      <Sidebar />

      {/* Main Content */}
      <div
        className={cn(
          'transition-all duration-300 ease-in-out min-h-screen flex flex-col',
          sidebarCollapsed ? 'ml-[68px]' : 'ml-[220px]'
        )}
      >
        {/* Top Bar */}
        <header className="sticky top-0 z-30 h-16 border-b border-border/50 bg-background/80 backdrop-blur-md">
          <div className="flex items-center justify-between h-full px-6">
            <div>
              <h2 className="text-lg font-semibold text-foreground">
                多智能体AIOps运维平台
              </h2>
            </div>
            <div className="flex items-center gap-3">
              <button className="relative p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition-colors">
                <Bell className="w-5 h-5" />
                <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-red-500" />
              </button>
              <button className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition-colors">
                <Settings className="w-5 h-5" />
              </button>
              <div className="flex items-center gap-2 pl-3 border-l border-border/50">
                <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center">
                  <User className="w-4 h-4 text-primary" />
                </div>
                <span className="text-sm font-medium text-foreground hidden lg:block">
                  运维管理员
                </span>
              </div>
            </div>
          </div>
        </header>

        {/* Page Content */}
        <main className="flex-1 p-6 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
