'use client';

import { useState } from 'react';
import { Check, ArrowSquareOut } from '@phosphor-icons/react';
import type { MarketplaceConnector } from '@/lib/api/marketplace';
import Image from 'next/image';

interface ConnectorCardProps {
  connector: MarketplaceConnector;
  onEnable: (id: string) => void;
  onViewDetails: (id: string) => void;
}

export function ConnectorCard({
  connector,
  onEnable,
  onViewDetails,
}: ConnectorCardProps) {
  const [imgError, setImgError] = useState(false);

  const logoPath = `/logos/connectors/${connector.id}.svg`;

  return (
    <div
      className="bg-card border border-border rounded-lg p-4 hover:border-primary/50 transition-colors cursor-pointer"
      onClick={() => onViewDetails(connector.id)}
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-muted flex items-center justify-center overflow-hidden">
            {imgError ? (
              <span className="text-xl">{connector.icon}</span>
            ) : (
              <Image
                src={logoPath}
                alt={connector.name}
                width={24}
                height={24}
                className="object-contain"
                onError={() => setImgError(true)}
              />
            )}
          </div>
          <div>
            <h3 className="font-medium text-foreground">{connector.name}</h3>
            <p className="text-xs text-muted-foreground capitalize">
              {connector.category.replace('_', ' ')}
            </p>
          </div>
        </div>

        {connector.docs_url && (
          <a
            href={connector.docs_url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="text-muted-foreground hover:text-foreground"
          >
            <ArrowSquareOut size={16} />
          </a>
        )}
      </div>

      <p className="text-sm text-muted-foreground mt-3 line-clamp-2">
        {connector.description}
      </p>

      <div className="flex items-center justify-between mt-4">
        {connector.operation_count && (
          <span className="text-xs text-muted-foreground">
            {connector.operation_count} operations
          </span>
        )}

        {connector.is_enabled ? (
          <span className="inline-flex items-center gap-1 text-xs text-green-600 bg-green-100 dark:bg-green-900/30 px-2 py-1 rounded">
            <Check size={14} weight="bold" />
            Enabled
          </span>
        ) : (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onEnable(connector.id);
            }}
            className="text-xs bg-primary text-primary-foreground px-3 py-1 rounded hover:bg-primary/90 transition-colors"
          >
            Enable
          </button>
        )}
      </div>
    </div>
  );
}
