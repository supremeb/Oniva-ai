'use client';

import { useState, useCallback, useMemo } from 'react';
import {
  Brain,
  MagnifyingGlass,
  ThumbsUp,
  ThumbsDown,
  Archive,
  Trash,
  Tag,
  Eye,
  EyeSlash,
  Funnel,
  CaretDown,
  X,
  Clock,
  ChartBar,
  Sparkle,
  CheckCircle,
  WarningCircle,
  Buildings,
  User,
} from '@phosphor-icons/react';
import {
  useMemoryStats,
  useListMemories,
  useRecallMemories,
  useProvideFeedback,
  useUpdateMemory,
  useDeleteMemory,
  useArchiveMemories,
  useConsolidateMemories,
} from '@/hooks/useMemory';
import {
  EpisodicMemory,
  SearchMode,
  FeedbackType,
  VisibilityType,
  MemoryType,
  ActionType,
} from '@/lib/api/memory';
import { parseTimestamp } from '@/lib/utils';
import { ConfirmationDialog } from '@/components/ui/confirmation-dialog';

// Memory type colors
const memoryTypeColors: Record<MemoryType, { bg: string; text: string; border: string }> = {
  decision: { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-200' },
  fact: { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-200' },
  preference: { bg: 'bg-purple-50', text: 'text-purple-700', border: 'border-purple-200' },
  conversation: { bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-200' },
};

// Action type colors
const actionTypeColors: Record<ActionType, { bg: string; text: string }> = {
  analysis: { bg: 'bg-indigo-100', text: 'text-indigo-700' },
  compliance_check: { bg: 'bg-red-100', text: 'text-red-700' },
  recommendation: { bg: 'bg-green-100', text: 'text-green-700' },
  research: { bg: 'bg-cyan-100', text: 'text-cyan-700' },
  other: { bg: 'bg-gray-100', text: 'text-gray-700' },
};

// Memory Card Component
function MemoryCard({
  memory,
  onFeedback,
  onToggleArchive,
  onDelete,
  onUpdateVisibility,
}: {
  memory: EpisodicMemory;
  onFeedback: (feedback: FeedbackType) => void;
  onToggleArchive: () => void;
  onDelete: () => void;
  onUpdateVisibility: (visibility: VisibilityType) => void;
}) {
  const [showDetails, setShowDetails] = useState(false);
  const typeColors = memoryTypeColors[memory.memory_type as MemoryType] || memoryTypeColors.decision;
  const actionColors = actionTypeColors[memory.action_type as ActionType] || actionTypeColors.other;

  return (
    <div
      className={`
        bg-card border rounded-xl p-5 transition-all duration-200
        ${memory.archived ? 'opacity-60 border-border/40' : 'border-border/60 hover:border-border'}
      `}
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`px-2 py-0.5 text-xs font-medium rounded-md ${typeColors.bg} ${typeColors.text} ${typeColors.border} border`}>
            {memory.memory_type}
          </span>
          <span className={`px-2 py-0.5 text-xs font-medium rounded-md ${actionColors.bg} ${actionColors.text}`}>
            {memory.action_type.replace('_', ' ')}
          </span>
          {memory.archived && (
            <span className="px-2 py-0.5 text-xs font-medium rounded-md bg-gray-100 text-gray-600">
              Archived
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {memory.feedback === 'helpful' && (
            <ThumbsUp weight="fill" className="h-4 w-4 text-emerald-500" />
          )}
          {memory.feedback === 'not_helpful' && (
            <ThumbsDown weight="fill" className="h-4 w-4 text-red-500" />
          )}
        </div>
      </div>

      {/* Agent & Time */}
      <div className="flex items-center gap-2 text-xs text-muted-foreground mb-2">
        <span className="font-medium text-foreground">{memory.agent_name}</span>
        <span>&bull;</span>
        <Clock className="h-3.5 w-3.5" />
        <span>{parseTimestamp(memory.created_at).toLocaleDateString()}</span>
        {memory.similarity !== undefined && (
          <>
            <span>&bull;</span>
            <span className="text-primary font-medium">
              {(memory.similarity * 100).toFixed(0)}% match
            </span>
          </>
        )}
      </div>

      {/* Content */}
      <p className="text-sm text-foreground mb-3 line-clamp-3">
        {memory.context_decision}
      </p>

      {/* Rationale (if present) */}
      {memory.rationale && showDetails && (
        <div className="mb-3 p-3 bg-muted/50 rounded-lg">
          <p className="text-xs text-muted-foreground mb-1">Rationale</p>
          <p className="text-sm text-foreground">{memory.rationale}</p>
        </div>
      )}

      {/* Entities */}
      {memory.entities.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {memory.entities.slice(0, showDetails ? undefined : 5).map((entity, i) => (
            <span
              key={i}
              className="px-2 py-0.5 text-xs bg-muted text-muted-foreground rounded-md"
            >
              {entity}
            </span>
          ))}
          {!showDetails && memory.entities.length > 5 && (
            <span className="px-2 py-0.5 text-xs text-muted-foreground">
              +{memory.entities.length - 5} more
            </span>
          )}
        </div>
      )}

      {/* Tags */}
      {memory.tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {memory.tags.map((tag, i) => (
            <span
              key={i}
              className="px-2 py-0.5 text-xs bg-primary/10 text-primary rounded-md flex items-center gap-1"
            >
              <Tag className="h-3 w-3" />
              {tag}
            </span>
          ))}
        </div>
      )}

      {/* Importance & Access Stats */}
      <div className="flex items-center gap-4 text-xs text-muted-foreground mb-3">
        <span className="flex items-center gap-1">
          <Sparkle className="h-3.5 w-3.5" />
          Importance: {(memory.importance * 100).toFixed(0)}%
        </span>
        <span className="flex items-center gap-1">
          <Eye className="h-3.5 w-3.5" />
          Accessed {memory.access_count}x
        </span>
        <span className="flex items-center gap-1">
          {memory.visibility === 'private' && <EyeSlash className="h-3.5 w-3.5" />}
          {memory.visibility === 'shared' && <Buildings className="h-3.5 w-3.5" />}
          {memory.visibility}
        </span>
      </div>

      {/* Actions */}
      <div className="flex items-center justify-between pt-3 border-t border-border/40">
        <div className="flex items-center gap-2">
          <button
            onClick={() => onFeedback('helpful')}
            disabled={memory.feedback === 'helpful'}
            className={`
              p-1.5 rounded-md transition-colors
              ${memory.feedback === 'helpful'
                ? 'bg-emerald-100 text-emerald-600'
                : 'hover:bg-emerald-50 text-muted-foreground hover:text-emerald-600'
              }
            `}
            title="Mark as helpful"
          >
            <ThumbsUp className="h-4 w-4" weight={memory.feedback === 'helpful' ? 'fill' : 'regular'} />
          </button>
          <button
            onClick={() => onFeedback('not_helpful')}
            disabled={memory.feedback === 'not_helpful'}
            className={`
              p-1.5 rounded-md transition-colors
              ${memory.feedback === 'not_helpful'
                ? 'bg-red-100 text-red-600'
                : 'hover:bg-red-50 text-muted-foreground hover:text-red-600'
              }
            `}
            title="Mark as not helpful"
          >
            <ThumbsDown className="h-4 w-4" weight={memory.feedback === 'not_helpful' ? 'fill' : 'regular'} />
          </button>
          <button
            onClick={() => onUpdateVisibility(memory.visibility === 'shared' ? 'private' : 'shared')}
            className={`
              p-1.5 rounded-md transition-colors
              ${memory.visibility === 'shared'
                ? 'bg-blue-100 text-blue-600'
                : 'hover:bg-blue-50 text-muted-foreground hover:text-blue-600'
              }
            `}
            title={memory.visibility === 'shared' ? 'Make private' : 'Share with org'}
          >
            {memory.visibility === 'shared'
              ? <Buildings className="h-4 w-4" weight="fill" />
              : <Buildings className="h-4 w-4" />
            }
          </button>
          <button
            onClick={onToggleArchive}
            className="p-1.5 rounded-md hover:bg-amber-50 text-muted-foreground hover:text-amber-600 transition-colors"
            title={memory.archived ? 'Unarchive' : 'Archive'}
          >
            <Archive className="h-4 w-4" weight={memory.archived ? 'fill' : 'regular'} />
          </button>
          <button
            onClick={onDelete}
            className="p-1.5 rounded-md hover:bg-red-50 text-muted-foreground hover:text-red-600 transition-colors"
            title="Delete"
          >
            <Trash className="h-4 w-4" />
          </button>
        </div>
        <button
          onClick={() => setShowDetails(!showDetails)}
          className="text-xs text-primary hover:underline"
        >
          {showDetails ? 'Show less' : 'Show more'}
        </button>
      </div>
    </div>
  );
}

// Stats Card Component
function StatsCard({
  title,
  value,
  subtitle,
  icon: Icon,
  iconColor,
}: {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: React.ElementType;
  iconColor: string;
}) {
  return (
    <div className="bg-card border border-border/60 rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <Icon weight="duotone" className={`h-5 w-5 ${iconColor}`} />
      </div>
      <div className="space-y-1">
        <p className="text-stat">{value}</p>
        <p className="text-stat-label">
          {title}
        </p>
      </div>
      {subtitle && (
        <div className="mt-3 pt-3 border-t border-border/40">
          <p className="text-xs text-muted-foreground">{subtitle}</p>
        </div>
      )}
    </div>
  );
}

// Main Page Component
export default function MemoryPage() {
  // Tab state: "mine" or "shared"
  const [activeTab, setActiveTab] = useState<'mine' | 'shared'>('mine');

  // Search state
  const [searchQuery, setSearchQuery] = useState('');
  const [searchMode, setSearchMode] = useState<SearchMode>('hybrid');
  const [includeArchived, setIncludeArchived] = useState(false);
  const [memoryTypeFilter, setMemoryTypeFilter] = useState<MemoryType | 'all'>('all');
  const [isSearching, setIsSearching] = useState(false);
  const [deleteMemoryId, setDeleteMemoryId] = useState<string | null>(null);
  const [showConsolidateDialog, setShowConsolidateDialog] = useState(false);

  // Stats query
  const { data: stats, isLoading: statsLoading } = useMemoryStats();

  // List memories query (always runs on page load)
  const listRequest = useMemo(() => ({
    limit: 50,
    include_archived: includeArchived,
    memory_type: memoryTypeFilter !== 'all' ? memoryTypeFilter : undefined,
    visibility: activeTab === 'shared' ? 'shared' as const : undefined,
  }), [includeArchived, memoryTypeFilter, activeTab]);

  const {
    data: listResults,
    isLoading: listLoading,
  } = useListMemories(listRequest);

  // Search query - only run when explicitly searching
  const searchRequest = useMemo(() => {
    if (!isSearching || !searchQuery.trim()) return null;
    return {
      query: searchQuery,
      limit: 20,
      importance_weight: 0.3,
      recency_weight: 0.2,
    };
  }, [isSearching, searchQuery]);

  const {
    data: searchResults,
    isLoading: searchLoading,
    isFetching: searchFetching,
  } = useRecallMemories(searchRequest, { searchMode, includeArchived });

  // Mutations
  const feedbackMutation = useProvideFeedback();
  const updateMutation = useUpdateMemory();
  const deleteMutation = useDeleteMemory();
  const archiveMutation = useArchiveMemories();
  const consolidateMutation = useConsolidateMemories();

  // Handlers
  const handleSearch = useCallback(() => {
    if (searchQuery.trim()) {
      setIsSearching(true);
    }
  }, [searchQuery]);

  const handleFeedback = useCallback(
    (memoryId: string, feedback: FeedbackType) => {
      feedbackMutation.mutate({ memoryId, feedback: { feedback } });
    },
    [feedbackMutation]
  );

  const handleToggleArchive = useCallback(
    (memory: EpisodicMemory) => {
      updateMutation.mutate({
        memoryId: memory.id,
        updates: { archived: !memory.archived },
      });
    },
    [updateMutation]
  );

  const handleDelete = useCallback(
    (memoryId: string) => {
      setDeleteMemoryId(memoryId);
    },
    []
  );

  const confirmDeleteMemory = useCallback(async () => {
    if (!deleteMemoryId) return;
    deleteMutation.mutate(deleteMemoryId);
  }, [deleteMemoryId, deleteMutation]);

  const handleUpdateVisibility = useCallback(
    (memoryId: string, visibility: VisibilityType) => {
      updateMutation.mutate({ memoryId, updates: { visibility } });
    },
    [updateMutation]
  );

  const handleConsolidate = useCallback(() => {
    setShowConsolidateDialog(true);
  }, []);

  const confirmConsolidate = useCallback(async () => {
    consolidateMutation.mutate();
  }, [consolidateMutation]);

  // Filter memories by type
  const filteredMemories = useMemo(() => {
    if (!searchResults?.results) return [];
    if (memoryTypeFilter === 'all') return searchResults.results;
    return searchResults.results.filter((m) => m.memory_type === memoryTypeFilter);
  }, [searchResults, memoryTypeFilter]);

  return (
    <div className="p-6 lg:p-8 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-page-title">Memory Management</h1>
          <p className="text-page-description mt-1">
            Search, review, and manage agent memories and learning
          </p>
        </div>
      </div>

        {/* Tabs: My Memories / Shared */}
        <div className="flex gap-1 mb-6 border-b border-border">
          <button
            onClick={() => setActiveTab('mine')}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
              activeTab === 'mine'
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            <User size={18} />
            My Memories
          </button>
          <button
            onClick={() => setActiveTab('shared')}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
              activeTab === 'shared'
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            <Buildings size={18} />
            Shared
          </button>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <StatsCard
            title="Total Memories"
            value={statsLoading ? '---' : stats?.total_memories.toLocaleString() || 0}
            icon={Brain}
            iconColor="text-purple-600"
          />
          <StatsCard
            title="Avg Importance"
            value={statsLoading ? '---' : `${((stats?.avg_importance || 0) * 100).toFixed(0)}%`}
            icon={Sparkle}
            iconColor="text-amber-600"
          />
          <StatsCard
            title="Helpful"
            value={statsLoading ? '---' : stats?.by_feedback?.helpful || 0}
            subtitle={stats?.by_feedback?.not_helpful ? `${stats.by_feedback.not_helpful} marked not helpful` : undefined}
            icon={ThumbsUp}
            iconColor="text-emerald-600"
          />
          <StatsCard
            title="Archived"
            value={statsLoading ? '---' : stats?.archived_count || 0}
            icon={Archive}
            iconColor="text-gray-500"
          />
        </div>

        {/* Search & Filters */}
        <div className="bg-card border border-border/60 rounded-xl p-6 mb-6">
          <div className="flex flex-col lg:flex-row gap-4">
            {/* Search Input */}
            <div className="flex-1 relative">
              <MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-muted-foreground" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value);
                  setIsSearching(false);
                }}
                onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                placeholder="Search memories by content, entities, or context..."
                className="w-full pl-10 pr-4 py-2.5 bg-background border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
              />
            </div>

            {/* Search Mode */}
            <div className="flex items-center gap-2">
              <select
                value={searchMode}
                onChange={(e) => setSearchMode(e.target.value as SearchMode)}
                className="px-3 py-2.5 bg-background border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
              >
                <option value="hybrid">Hybrid Search</option>
                <option value="semantic">Semantic Only</option>
                <option value="keyword">Keyword Only</option>
              </select>

              {/* Memory Type Filter */}
              <select
                value={memoryTypeFilter}
                onChange={(e) => setMemoryTypeFilter(e.target.value as MemoryType | 'all')}
                className="px-3 py-2.5 bg-background border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
              >
                <option value="all">All Types</option>
                <option value="decision">Decisions</option>
                <option value="fact">Facts</option>
                <option value="preference">Preferences</option>
                <option value="conversation">Conversations</option>
              </select>

              {/* Include Archived */}
              <label className="flex items-center gap-2 px-3 py-2.5 bg-background border border-border rounded-lg text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={includeArchived}
                  onChange={(e) => setIncludeArchived(e.target.checked)}
                  className="rounded border-border"
                />
                <span className="text-muted-foreground">Archived</span>
              </label>

              {/* Search Button */}
              <button
                onClick={handleSearch}
                disabled={!searchQuery.trim() || searchFetching}
                className="px-4 py-2.5 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
              >
                <MagnifyingGlass className="h-4 w-4" />
                Search
              </button>
            </div>
          </div>

          {/* Quick Actions */}
          <div className="flex items-center gap-2 mt-4 pt-4 border-t border-border/40">
            <button
              onClick={handleConsolidate}
              disabled={consolidateMutation.isPending}
              className="px-3 py-1.5 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-700 hover:bg-amber-100 transition-colors flex items-center gap-1.5 disabled:opacity-50"
            >
              <Sparkle className="h-4 w-4" />
              Consolidate Memories
            </button>
            <button
              onClick={() => archiveMutation.mutate({ days_threshold: 90, min_access_count: 2 })}
              disabled={archiveMutation.isPending}
              className="px-3 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-sm text-gray-700 hover:bg-gray-100 transition-colors flex items-center gap-1.5 disabled:opacity-50"
            >
              <Archive className="h-4 w-4" />
              Archive Old Memories
            </button>
          </div>
        </div>

        {/* Search Results */}
        {isSearching && (
          <div className="space-y-4">
            {/* Results Header */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold text-foreground">
                  Search Results
                </h2>
                {searchResults && (
                  <span className="px-2 py-0.5 text-xs bg-muted text-muted-foreground rounded-md">
                    {filteredMemories.length} memories
                  </span>
                )}
                {searchResults?.search_mode && (
                  <span className="px-2 py-0.5 text-xs bg-primary/10 text-primary rounded-md">
                    {searchResults.search_mode} search
                  </span>
                )}
              </div>
              <button
                onClick={() => {
                  setIsSearching(false);
                  setSearchQuery('');
                }}
                className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
              >
                <X className="h-4 w-4" />
                Clear
              </button>
            </div>

            {/* Loading State */}
            {searchLoading && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {[...Array(4)].map((_, i) => (
                  <div key={i} className="animate-pulse bg-card border border-border/60 rounded-xl p-5">
                    <div className="h-4 bg-muted rounded w-1/3 mb-3" />
                    <div className="h-3 bg-muted rounded w-1/4 mb-3" />
                    <div className="space-y-2">
                      <div className="h-3 bg-muted rounded w-full" />
                      <div className="h-3 bg-muted rounded w-5/6" />
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Results Grid */}
            {!searchLoading && filteredMemories.length > 0 && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {filteredMemories.map((memory) => (
                  <MemoryCard
                    key={memory.id}
                    memory={memory}
                    onFeedback={(feedback) => handleFeedback(memory.id, feedback)}
                    onToggleArchive={() => handleToggleArchive(memory)}
                    onDelete={() => handleDelete(memory.id)}
                    onUpdateVisibility={(visibility) => handleUpdateVisibility(memory.id, visibility)}
                  />
                ))}
              </div>
            )}

            {/* Empty State */}
            {!searchLoading && filteredMemories.length === 0 && searchResults && (
              <div className="text-center py-12 text-muted-foreground">
                <Brain className="h-12 w-12 mx-auto mb-3 text-muted-foreground/50" />
                <p className="text-sm">No memories found matching your search.</p>
                <p className="text-xs mt-1">Try different keywords or adjust filters.</p>
              </div>
            )}
          </div>
        )}

        {/* Initial State - Show All Memories */}
        {!isSearching && (
          <div className="space-y-4">
            {/* Header */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold text-foreground">
                  {activeTab === 'shared' ? 'Shared Memories' : 'Recent Memories'}
                </h2>
                {listResults && (
                  <span className="px-2 py-0.5 text-xs bg-muted text-muted-foreground rounded-md">
                    {listResults.count} memories
                  </span>
                )}
              </div>
            </div>

            {/* Loading State */}
            {listLoading && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {[...Array(4)].map((_, i) => (
                  <div key={i} className="animate-pulse bg-card border border-border/60 rounded-xl p-5">
                    <div className="h-4 bg-muted rounded w-1/3 mb-3" />
                    <div className="h-3 bg-muted rounded w-1/4 mb-3" />
                    <div className="space-y-2">
                      <div className="h-3 bg-muted rounded w-full" />
                      <div className="h-3 bg-muted rounded w-5/6" />
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Memories Grid */}
            {!listLoading && listResults && listResults.results.length > 0 && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {listResults.results.map((memory) => (
                  <MemoryCard
                    key={memory.id}
                    memory={memory}
                    onFeedback={(feedback) => handleFeedback(memory.id, feedback)}
                    onToggleArchive={() => handleToggleArchive(memory)}
                    onDelete={() => handleDelete(memory.id)}
                    onUpdateVisibility={(visibility) => handleUpdateVisibility(memory.id, visibility)}
                  />
                ))}
              </div>
            )}

            {/* Empty State */}
            {!listLoading && (!listResults || listResults.results.length === 0) && (
              <div className="text-center py-16">
                <div className="inline-flex items-center justify-center w-16 h-16 bg-purple-100 rounded-full mb-4">
                  <Brain className="h-8 w-8 text-purple-600" />
                </div>
                <h2 className="text-xl font-semibold text-foreground mb-2">
                  {activeTab === 'shared' ? 'No Shared Memories' : 'No Memories Yet'}
                </h2>
                <p className="text-sm text-muted-foreground max-w-md mx-auto mb-6">
                  {activeTab === 'shared'
                    ? 'Shared memories are visible to all team members. Mark a memory as shared to add it here.'
                    : 'Memories are created when agents make decisions during workflows. Start using agents to build your knowledge base.'
                  }
                </p>
              </div>
            )}
          </div>
        )}

        {/* Memory Type Distribution */}
        {stats && !isSearching && (
          <div className="mt-8 bg-card border border-border/60 rounded-xl p-6">
            <h3 className="text-sm font-semibold text-foreground mb-4">Memory Distribution</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {Object.entries(stats.by_type).map(([type, count]) => {
                const colors = memoryTypeColors[type as MemoryType] || memoryTypeColors.decision;
                return (
                  <div
                    key={type}
                    className={`p-4 rounded-lg border ${colors.bg} ${colors.border}`}
                  >
                    <p className={`text-2xl font-bold ${colors.text}`}>{count}</p>
                    <p className={`text-xs ${colors.text} capitalize`}>{type}</p>
                  </div>
                );
              })}
            </div>
          </div>
        )}

      <ConfirmationDialog
        open={!!deleteMemoryId}
        onOpenChange={(open) => { if (!open) setDeleteMemoryId(null); }}
        title="Delete Memory"
        description="This will permanently delete this memory entry. This action cannot be undone."
        onConfirm={confirmDeleteMemory}
      />

      <ConfirmationDialog
        open={showConsolidateDialog}
        onOpenChange={setShowConsolidateDialog}
        title="Consolidate Memories"
        description="This will remove duplicate memories and archive old ones. Continue?"
        confirmLabel="Consolidate"
        destructive={false}
        onConfirm={confirmConsolidate}
      />
    </div>
  );
}
