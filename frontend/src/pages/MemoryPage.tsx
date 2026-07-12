import { useState } from 'react';
import { useAppStore } from '@/store/useAppStore';
import {
  Brain,
  Search,
  Plus,
  Trash2,
  Tag,
  Clock,
  Star,
  X,
  BookOpen,
  AlertTriangle,
  FileText,
  Cog,
  ChevronRight,
} from 'lucide-react';
import type { MemoryEntry } from '@/types';

type MemoryType = 'all' | 'incident' | 'playbook' | 'config' | 'knowledge';

const typeIcons: Record<string, React.ReactNode> = {
  incident: <AlertTriangle className="w-4 h-4" />,
  playbook: <BookOpen className="w-4 h-4" />,
  config: <Cog className="w-4 h-4" />,
  knowledge: <FileText className="w-4 h-4" />,
};

const typeLabels: Record<string, string> = {
  incident: '故障',
  playbook: '预案',
  config: '配置',
  knowledge: '知识',
};

const typeColors: Record<string, { bg: string; text: string }> = {
  incident: { bg: 'bg-red-400/10', text: 'text-red-400' },
  playbook: { bg: 'bg-blue-400/10', text: 'text-blue-400' },
  config: { bg: 'bg-purple-400/10', text: 'text-purple-400' },
  knowledge: { bg: 'bg-amber-400/10', text: 'text-amber-400' },
};

export function MemoryPage() {
  const { memories, setMemories } = useAppStore();
  const [searchQuery, setSearchQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState<MemoryType>('all');
  const [selectedMemory, setSelectedMemory] = useState<MemoryEntry | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [newMemory, setNewMemory] = useState({
    key: '',
    value: '',
    type: 'knowledge' as MemoryEntry['type'],
    importance: 0.8,
    tags: '',
  });

  const filteredMemories = memories.filter((m) => {
    const matchesSearch =
      searchQuery === '' ||
      m.key.toLowerCase().includes(searchQuery.toLowerCase()) ||
      m.value.toLowerCase().includes(searchQuery.toLowerCase()) ||
      m.tags?.some((t) => t.toLowerCase().includes(searchQuery.toLowerCase()));

    const matchesType = typeFilter === 'all' || m.type === typeFilter;

    return matchesSearch && matchesType;
  });

  const handleAddMemory = () => {
    const memory: MemoryEntry = {
      id: `MEM-${Date.now()}`,
      key: newMemory.key,
      value: newMemory.value,
      type: newMemory.type,
      created_at: new Date().toISOString(),
      importance: newMemory.importance,
      tags: newMemory.tags.split(',').map((t) => t.trim()).filter(Boolean),
    };
    setMemories([memory, ...memories]);
    setShowAddModal(false);
    setNewMemory({
      key: '',
      value: '',
      type: 'knowledge',
      importance: 0.8,
      tags: '',
    });
  };

  const handleDeleteMemory = (id: string) => {
    setMemories(memories.filter((m) => m.id !== id));
    if (selectedMemory?.id === id) setSelectedMemory(null);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">记忆管理</h1>
          <p className="text-sm text-muted-foreground mt-1">
            查看和管理智能体的记忆数据
          </p>
        </div>
        <button
          onClick={() => setShowAddModal(true)}
          className="inline-flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          <Plus className="w-4 h-4" />
          添加记忆
        </button>
      </div>

      {/* Search & Filter */}
      <div className="glass-card p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-[200px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <input
              type="text"
              placeholder="搜索记忆的key、value或标签..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-4 py-2 bg-background border border-input rounded-lg text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div className="flex items-center gap-1.5">
            {(['all', 'incident', 'playbook', 'config', 'knowledge'] as MemoryType[]).map(
              (type) => (
                <button
                  key={type}
                  onClick={() => setTypeFilter(type)}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                    typeFilter === type
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-muted text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {type === 'all' ? '全部' : typeLabels[type]}
                </button>
              )
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Memory List */}
        <div className="glass-card p-5">
          <h3 className="text-sm font-semibold text-foreground mb-4">
            记忆列表 ({filteredMemories.length})
          </h3>
          <div className="space-y-2 max-h-[600px] overflow-y-auto scrollbar-thin pr-1">
            {filteredMemories.length === 0 && (
              <div className="py-12 text-center text-muted-foreground">
                <Brain className="w-8 h-8 mx-auto mb-2 opacity-50" />
                暂无匹配的记忆数据
              </div>
            )}
            {filteredMemories.map((memory) => {
              const colors = typeColors[memory.type];
              return (
                <div
                  key={memory.id}
                  className={`group flex items-start gap-3 p-3 rounded-lg cursor-pointer transition-all ${
                    selectedMemory?.id === memory.id
                      ? 'bg-primary/10 border border-primary/20'
                      : 'bg-muted/20 hover:bg-muted/40 border border-transparent'
                  }`}
                  onClick={() => setSelectedMemory(memory)}
                >
                  <div
                    className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-0.5 ${colors.bg} ${colors.text}`}
                  >
                    {typeIcons[memory.type]}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-foreground truncate">
                        {memory.key}
                      </span>
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${colors.bg} ${colors.text}`}
                      >
                        {typeLabels[memory.type]}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                      {memory.value}
                    </p>
                    <div className="flex items-center gap-3 mt-2">
                      <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                        <Star className="w-3 h-3" />
                        {(memory.importance * 100).toFixed(0)}
                      </span>
                      <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {new Date(memory.created_at).toLocaleDateString('zh-CN')}
                      </span>
                      {memory.tags && memory.tags.length > 0 && (
                        <div className="flex items-center gap-1">
                          {memory.tags.slice(0, 2).map((tag) => (
                            <span
                              key={tag}
                              className="text-[10px] px-1.5 py-0.5 bg-muted rounded text-muted-foreground"
                            >
                              {tag}
                            </span>
                          ))}
                          {memory.tags.length > 2 && (
                            <span className="text-[10px] text-muted-foreground">
                              +{memory.tags.length - 2}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDeleteMemory(memory.id);
                    }}
                    className="p-1.5 rounded-md text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-red-400 hover:bg-red-400/10 transition-all"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                  <ChevronRight className="w-4 h-4 text-muted-foreground shrink-0 self-center" />
                </div>
              );
            })}
          </div>
        </div>

        {/* Memory Detail */}
        {selectedMemory ? (
          <div className="glass-card p-5 animate-slide-in">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <div
                  className={`w-10 h-10 rounded-lg flex items-center justify-center ${typeColors[selectedMemory.type].bg} ${typeColors[selectedMemory.type].text}`}
                >
                  {typeIcons[selectedMemory.type]}
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-foreground">
                    {selectedMemory.key}
                  </h3>
                  <span className="text-xs text-muted-foreground">
                    {selectedMemory.id}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span
                  className={`px-2 py-0.5 rounded text-xs font-medium ${typeColors[selectedMemory.type].bg} ${typeColors[selectedMemory.type].text}`}
                >
                  {typeLabels[selectedMemory.type]}
                </span>
                <button
                  onClick={() => handleDeleteMemory(selectedMemory.id)}
                  className="p-1.5 rounded-md text-muted-foreground hover:text-red-400 hover:bg-red-400/10 transition-colors"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>

            <div className="space-y-4">
              <div>
                <label className="text-xs text-muted-foreground">Value</label>
                <div className="mt-1 p-3 bg-muted/20 rounded-lg">
                  <p className="text-sm text-foreground whitespace-pre-wrap break-all font-mono">
                    {selectedMemory.value}
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs text-muted-foreground">
                    重要度
                  </label>
                  <div className="flex items-center gap-2 mt-1">
                    <Star className="w-4 h-4 text-amber-400" />
                    <span className="text-sm font-medium text-foreground">
                      {(selectedMemory.importance * 100).toFixed(0)} / 100
                    </span>
                  </div>
                  <div className="w-full h-1.5 bg-muted rounded-full overflow-hidden mt-1">
                    <div
                      className="h-full bg-amber-400 rounded-full"
                      style={{
                        width: `${selectedMemory.importance * 100}%`,
                      }}
                    />
                  </div>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">
                    创建时间
                  </label>
                  <div className="flex items-center gap-2 mt-1">
                    <Clock className="w-4 h-4 text-muted-foreground" />
                    <span className="text-sm text-foreground">
                      {new Date(selectedMemory.created_at).toLocaleString(
                        'zh-CN'
                      )}
                    </span>
                  </div>
                </div>
              </div>

              {selectedMemory.tags && selectedMemory.tags.length > 0 && (
                <div>
                  <label className="text-xs text-muted-foreground flex items-center gap-1 mb-2">
                    <Tag className="w-3 h-3" />
                    标签
                  </label>
                  <div className="flex flex-wrap gap-1.5">
                    {selectedMemory.tags.map((tag) => (
                      <span
                        key={tag}
                        className="px-2.5 py-1 bg-muted rounded-md text-xs text-foreground"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="glass-card p-5 flex items-center justify-center">
            <div className="text-center text-muted-foreground py-12">
              <Brain className="w-10 h-10 mx-auto mb-3 opacity-30" />
              <p className="text-sm">选择一条记忆查看详情</p>
            </div>
          </div>
        )}
      </div>

      {/* Add Memory Modal */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="bg-card border border-border rounded-xl shadow-xl w-full max-w-lg mx-4 p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-foreground">
                添加新记忆
              </h2>
              <button
                onClick={() => setShowAddModal(false)}
                className="text-muted-foreground hover:text-foreground"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-foreground mb-1.5">
                  Key
                </label>
                <input
                  type="text"
                  value={newMemory.key}
                  onChange={(e) =>
                    setNewMemory((m) => ({ ...m, key: e.target.value }))
                  }
                  placeholder="记忆的唯一标识"
                  className="w-full px-3 py-2 bg-background border border-input rounded-lg text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-foreground mb-1.5">
                  Value
                </label>
                <textarea
                  value={newMemory.value}
                  onChange={(e) =>
                    setNewMemory((m) => ({ ...m, value: e.target.value }))
                  }
                  placeholder="记忆的内容"
                  rows={4}
                  className="w-full px-3 py-2 bg-background border border-input rounded-lg text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring resize-none"
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-foreground mb-1.5">
                    类型
                  </label>
                  <select
                    value={newMemory.type}
                    onChange={(e) =>
                      setNewMemory((m) => ({
                        ...m,
                        type: e.target.value as MemoryEntry['type'],
                      }))
                    }
                    className="w-full px-3 py-2 bg-background border border-input rounded-lg text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  >
                    <option value="knowledge">知识</option>
                    <option value="incident">故障</option>
                    <option value="playbook">预案</option>
                    <option value="config">配置</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-foreground mb-1.5">
                    重要度 (0-1)
                  </label>
                  <input
                    type="number"
                    min="0"
                    max="1"
                    step="0.1"
                    value={newMemory.importance}
                    onChange={(e) =>
                      setNewMemory((m) => ({
                        ...m,
                        importance: parseFloat(e.target.value),
                      }))
                    }
                    className="w-full px-3 py-2 bg-background border border-input rounded-lg text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-foreground mb-1.5">
                  标签 (用逗号分隔)
                </label>
                <input
                  type="text"
                  value={newMemory.tags}
                  onChange={(e) =>
                    setNewMemory((m) => ({ ...m, tags: e.target.value }))
                  }
                  placeholder="database, latency, redis"
                  className="w-full px-3 py-2 bg-background border border-input rounded-lg text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>

              <div className="flex gap-3 pt-2">
                <button
                  onClick={() => setShowAddModal(false)}
                  className="flex-1 px-4 py-2 border border-input bg-background text-foreground rounded-lg text-sm font-medium hover:bg-accent transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={handleAddMemory}
                  disabled={!newMemory.key || !newMemory.value}
                  className="flex-1 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  添加
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
