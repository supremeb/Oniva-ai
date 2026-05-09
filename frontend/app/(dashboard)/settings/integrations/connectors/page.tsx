'use client';

import { useState } from 'react';
import {
  MagnifyingGlass,
  Funnel,
  Plus,
  CircleNotch,
  Check,
  Plugs,
  Storefront,
  Upload,
} from '@phosphor-icons/react';
import { ConnectorCard } from '@/components/marketplace/ConnectorCard';
import { EnableConnectorDialog } from '@/components/marketplace/EnableConnectorDialog';
import { ConnectorForm } from '@/components/settings/ConnectorForm';
import {
  useCategories,
  useConnectors,
  useConnectorDetail,
  useEnableConnector,
  useEnabledConnectors,
  useDisableConnector,
} from '@/hooks/useMarketplace';
import { toast } from 'sonner';
import { ConfirmationDialog } from '@/components/ui/confirmation-dialog';
import Image from 'next/image';

type Tab = 'my-connectors' | 'marketplace';

export default function ConnectorsSettingsPage() {
  const [activeTab, setActiveTab] = useState<Tab>('marketplace');
  const [search, setSearch] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string | undefined>();
  const [selectedConnector, setSelectedConnector] = useState<string | null>(null);
  const [showImportForm, setShowImportForm] = useState(false);
  const [disableTarget, setDisableTarget] = useState<{ uuid: string; name: string } | null>(null);

  // Marketplace queries
  const { data: categories, isLoading: categoriesLoading } = useCategories();
  const { data: connectors, isLoading: connectorsLoading } = useConnectors({
    category: selectedCategory,
    search: search || undefined,
  });
  const { data: connectorDetail, isLoading: detailLoading } = useConnectorDetail(
    selectedConnector ?? undefined
  );

  // Enabled connectors query
  const { data: enabledData, isLoading: enabledLoading } = useEnabledConnectors();

  // Mutations
  const enableMutation = useEnableConnector();
  const disableMutation = useDisableConnector();

  const handleEnable = async (credentials: Record<string, string>) => {
    if (!selectedConnector) return;

    try {
      const result = await enableMutation.mutateAsync({
        connectorId: selectedConnector,
        request: { credentials },
      });
      toast.success(result.message);
      setSelectedConnector(null);
    } catch (error: any) {
      toast.error(error.message || 'Failed to enable connector');
    }
  };

  const handleDisable = (connectorUuid: string, name: string) => {
    setDisableTarget({ uuid: connectorUuid, name });
  };

  const confirmDisable = async () => {
    if (!disableTarget) return;
    try {
      await disableMutation.mutateAsync(disableTarget.uuid);
      toast.success(`${disableTarget.name} has been disabled`);
    } catch (error: any) {
      toast.error(error.message || 'Failed to disable connector');
    }
  };

  const isLoading = categoriesLoading || connectorsLoading;

  const getLogoPath = (connectorId: string) => {
    return `/logos/connectors/${connectorId}.svg`;
  };

  return (
    <div className="p-6 lg:p-8 max-w-5xl">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-page-title">Connectors</h1>
        <p className="text-page-description mt-1">
          Manage your integrations and browse 100+ pre-built connectors
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 border-b border-border">
        <button
          onClick={() => setActiveTab('my-connectors')}
          className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
            activeTab === 'my-connectors'
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          <Plugs size={18} />
          My Connectors
          {enabledData?.total_count ? (
            <span className="ml-1 px-1.5 py-0.5 text-xs rounded-full bg-primary/10 text-primary">
              {enabledData.total_count}
            </span>
          ) : null}
        </button>
        <button
          onClick={() => setActiveTab('marketplace')}
          className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
            activeTab === 'marketplace'
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          <Storefront size={18} />
          Marketplace
        </button>
      </div>

      {/* My Connectors Tab */}
      {activeTab === 'my-connectors' && (
        <div>
          {enabledLoading ? (
            <div className="flex items-center justify-center py-16">
              <CircleNotch size={32} className="animate-spin text-primary" />
            </div>
          ) : !enabledData?.connectors?.length ? (
            <div className="text-center py-16">
              <Plugs size={48} className="mx-auto text-muted-foreground mb-4" />
              <p className="text-muted-foreground mb-4">No connectors enabled yet</p>
              <button
                onClick={() => setActiveTab('marketplace')}
                className="text-primary hover:underline text-sm"
              >
                Browse marketplace to add connectors
              </button>
            </div>
          ) : (
            <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
              {enabledData.connectors.map((connector) => (
                <div
                  key={connector.id}
                  className="bg-card border border-border rounded-lg p-4"
                >
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-lg bg-muted flex items-center justify-center overflow-hidden">
                        <Image
                          src={getLogoPath(connector.connector_id)}
                          alt={connector.name}
                          width={24}
                          height={24}
                          className="object-contain"
                          onError={(e) => {
                            (e.target as HTMLImageElement).style.display = 'none';
                            (e.target as HTMLImageElement).parentElement!.innerHTML = connector.icon || '🔌';
                          }}
                        />
                      </div>
                      <div>
                        <h3 className="font-medium text-foreground">{connector.name}</h3>
                        <p className="text-xs text-muted-foreground capitalize">
                          {connector.category.replace('_', ' ')}
                        </p>
                      </div>
                    </div>
                    <span className="inline-flex items-center gap-1 text-xs text-green-600 bg-green-100 dark:bg-green-900/30 px-2 py-1 rounded">
                      <Check size={12} weight="bold" />
                      Active
                    </span>
                  </div>

                  {connector.description && (
                    <p className="text-sm text-muted-foreground mt-3 line-clamp-2">
                      {connector.description}
                    </p>
                  )}

                  <div className="flex items-center justify-between mt-4 pt-3 border-t border-border">
                    <span className="text-xs text-muted-foreground">
                      {connector.operation_count} operations
                    </span>
                    <button
                      onClick={() => handleDisable(connector.id, connector.name)}
                      disabled={disableMutation.isPending}
                      className="text-xs text-red-500 hover:text-red-600 transition-colors"
                    >
                      Disable
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Marketplace Tab */}
      {activeTab === 'marketplace' && (
        <div>
          {/* Search and Filters */}
          <div className="flex flex-col sm:flex-row gap-4 mb-6 max-w-2xl">
            <div className="relative flex-1">
              <MagnifyingGlass
                size={18}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
              />
              <input
                type="text"
                placeholder="Search connectors..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full pl-10 pr-4 py-2 bg-background border border-border rounded-lg text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50"
              />
            </div>

            <button
              onClick={() => setShowImportForm(true)}
              className="flex items-center gap-2 px-4 py-2 border border-border rounded-lg text-foreground hover:bg-muted/50 transition-colors"
            >
              <Plus size={18} />
              Import Custom
            </button>
          </div>

          {/* Category Filter */}
          <div className="flex flex-wrap gap-2 mb-6">
            <button
              onClick={() => setSelectedCategory(undefined)}
              className={`px-3 py-1.5 rounded-full text-sm transition-colors ${
                !selectedCategory
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-muted text-muted-foreground hover:bg-muted/80'
              }`}
            >
              All
            </button>
            {categories?.map((cat) => (
              <button
                key={cat.id}
                onClick={() => setSelectedCategory(cat.id)}
                className={`px-3 py-1.5 rounded-full text-sm transition-colors flex items-center gap-1.5 ${
                  selectedCategory === cat.id
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted text-muted-foreground hover:bg-muted/80'
                }`}
              >
                <span>{cat.icon}</span>
                {cat.name}
                <span className="text-xs opacity-70">({cat.connector_count})</span>
              </button>
            ))}
          </div>

          {/* Connector Grid */}
          {isLoading ? (
            <div className="flex items-center justify-center py-16">
              <CircleNotch size={32} className="animate-spin text-primary" />
            </div>
          ) : connectors?.length === 0 ? (
            <div className="text-center py-16">
              <Funnel size={48} className="mx-auto text-muted-foreground mb-4" />
              <p className="text-muted-foreground">No connectors found</p>
              <div className="flex flex-col items-center gap-2 mt-4">
                {search && (
                  <button
                    onClick={() => setSearch('')}
                    className="text-primary hover:underline text-sm"
                  >
                    Clear search
                  </button>
                )}
                <p className="text-sm text-muted-foreground">or</p>
                <button
                  onClick={() => setShowImportForm(true)}
                  className="flex items-center gap-2 text-primary hover:underline text-sm"
                >
                  <Upload size={16} />
                  Import a custom connector
                </button>
              </div>
            </div>
          ) : (
            <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
              {connectors?.map((connector) => (
                <ConnectorCard
                  key={connector.id}
                  connector={connector}
                  onEnable={setSelectedConnector}
                  onViewDetails={setSelectedConnector}
                />
              ))}
            </div>
          )}

          {/* Stats */}
          {connectors && connectors.length > 0 && (
            <div className="mt-8 text-center text-sm text-muted-foreground">
              Showing {connectors.length} connectors
              {selectedCategory && ` in ${selectedCategory.replace('_', ' ')}`}
            </div>
          )}
        </div>
      )}

      {/* Enable Dialog */}
      {selectedConnector && connectorDetail && !detailLoading && (
        <EnableConnectorDialog
          connector={connectorDetail}
          onClose={() => setSelectedConnector(null)}
          onEnable={handleEnable}
          isLoading={enableMutation.isPending}
        />
      )}

      {/* Import Custom Connector Dialog */}
      {showImportForm && (
        <ConnectorForm onClose={() => setShowImportForm(false)} />
      )}

      <ConfirmationDialog
        open={!!disableTarget}
        onOpenChange={(open) => { if (!open) setDisableTarget(null); }}
        title="Disable Connector"
        description={`This will disconnect ${disableTarget?.name ?? 'this connector'}. You can re-enable it later.`}
        confirmLabel="Disable"
        destructive={false}
        onConfirm={confirmDisable}
      />
    </div>
  );
}
