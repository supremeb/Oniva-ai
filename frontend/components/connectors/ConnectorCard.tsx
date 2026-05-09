import { ConnectorMetadata } from '@/lib/api/connectors';
import { Plugs, CheckCircle, GearSix } from '@phosphor-icons/react';

interface ConnectorCardProps {
    connector: ConnectorMetadata;
    isConnected: boolean;
    onConnectClick: () => void;
    onManageClick: () => void;
}

export function ConnectorCard({ connector, isConnected, onConnectClick, onManageClick }: ConnectorCardProps) {
    return (
        <div className="p-4 rounded-lg border border-border hover:border-foreground/20 transition-colors">
            <div className="flex items-start justify-between mb-3">
                <div className="w-8 h-8 rounded-lg bg-muted flex items-center justify-center">
                    <Plugs className="h-4 w-4 text-muted-foreground" weight="regular" />
                </div>
                {isConnected && (
                    <span className="flex items-center gap-1 text-xs text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full">
                        <CheckCircle className="h-3 w-3" weight="fill" />
                        Connected
                    </span>
                )}
            </div>

            <h3 className="text-card-title">{connector.name}</h3>
            <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                {connector.description}
            </p>

            <div className="flex items-center gap-2 mt-3">
                <span className="text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
                    {connector.category}
                </span>
                <span className="text-xs text-muted-foreground">
                    v{connector.version}
                </span>
                {connector.enterprise_only && (
                    <span className="text-xs text-amber-600 bg-amber-50 px-2 py-0.5 rounded-full">
                        Enterprise
                    </span>
                )}
            </div>

            <div className="mt-4">
                {isConnected ? (
                    <button
                        onClick={onManageClick}
                        className="flex items-center justify-center gap-2 w-full px-3 py-2 text-xs font-medium text-muted-foreground border border-border rounded-lg hover:border-foreground/20 hover:text-foreground transition-colors"
                    >
                        <GearSix className="h-3.5 w-3.5" weight="regular" />
                        Configure
                    </button>
                ) : (
                    <button
                        onClick={onConnectClick}
                        className="w-full px-3 py-2 text-xs font-medium bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors"
                    >
                        Connect
                    </button>
                )}
            </div>
        </div>
    );
}
