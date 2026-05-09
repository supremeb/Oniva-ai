'use client';

import { useState } from 'react';
import { X, Eye, EyeSlash, CircleNotch } from '@phosphor-icons/react';
import type { MarketplaceConnectorDetail } from '@/lib/api/marketplace';
import Image from 'next/image';

interface EnableConnectorDialogProps {
  connector: MarketplaceConnectorDetail;
  onClose: () => void;
  onEnable: (credentials: Record<string, string>) => void;
  isLoading: boolean;
}

export function EnableConnectorDialog({
  connector,
  onClose,
  onEnable,
  isLoading,
}: EnableConnectorDialogProps) {
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [showSecrets, setShowSecrets] = useState<Record<string, boolean>>({});
  const [imgError, setImgError] = useState(false);

  const logoPath = `/logos/connectors/${connector.id}.svg`;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onEnable(credentials);
  };

  const toggleShowSecret = (field: string) => {
    setShowSecrets((prev) => ({ ...prev, [field]: !prev[field] }));
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-card border border-border rounded-lg shadow-lg w-full max-w-md mx-4">
        <div className="flex items-center justify-between p-4 border-b border-border">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-lg bg-muted flex items-center justify-center overflow-hidden">
              {imgError ? (
                <span className="text-2xl">{connector.icon}</span>
              ) : (
                <Image
                  src={logoPath}
                  alt={connector.name}
                  width={32}
                  height={32}
                  className="object-contain"
                  onError={() => setImgError(true)}
                />
              )}
            </div>
            <div>
              <h2 className="font-semibold text-foreground">
                Enable {connector.name}
              </h2>
              <p className="text-xs text-muted-foreground">
                Enter your credentials to connect
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
          >
            <X size={20} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-4 space-y-4">
          {connector.required_credentials.map((field) => (
            <div key={field}>
              <label className="block text-sm font-medium text-foreground mb-1 capitalize">
                {field.replace(/_/g, ' ')}
              </label>
              <div className="relative">
                <input
                  type={showSecrets[field] ? 'text' : 'password'}
                  value={credentials[field] || ''}
                  onChange={(e) =>
                    setCredentials((prev) => ({
                      ...prev,
                      [field]: e.target.value,
                    }))
                  }
                  className="w-full px-3 py-2 pr-10 bg-background border border-border rounded text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50"
                  placeholder={`Enter ${field.replace(/_/g, ' ')}`}
                  required
                />
                <button
                  type="button"
                  onClick={() => toggleShowSecret(field)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  {showSecrets[field] ? (
                    <EyeSlash size={18} />
                  ) : (
                    <Eye size={18} />
                  )}
                </button>
              </div>
            </div>
          ))}

          {connector.docs_url && (
            <p className="text-xs text-muted-foreground">
              Need help?{' '}
              <a
                href={connector.docs_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                View documentation
              </a>
            </p>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm text-muted-foreground hover:text-foreground"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isLoading}
              className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded hover:bg-primary/90 disabled:opacity-50 flex items-center gap-2"
            >
              {isLoading && <CircleNotch size={16} className="animate-spin" />}
              {isLoading ? 'Enabling...' : 'Enable Connector'}
            </button>
          </div>
        </form>

        {connector.sample_tools.length > 0 && (
          <div className="border-t border-border p-4">
            <h3 className="text-sm font-medium text-foreground mb-2">
              Sample Operations
            </h3>
            <ul className="space-y-1">
              {connector.sample_tools.slice(0, 3).map((tool) => (
                <li
                  key={tool.name}
                  className="text-xs text-muted-foreground flex items-center gap-2"
                >
                  <span className="w-1 h-1 bg-muted-foreground rounded-full" />
                  {tool.description || tool.name}
                </li>
              ))}
              {connector.sample_tools.length > 3 && (
                <li className="text-xs text-muted-foreground">
                  +{connector.sample_tools.length - 3} more operations
                </li>
              )}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
