'use client';

import { useState, useMemo } from 'react';
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Copy, Key, Plus, Trash, Eye, EyeSlash, CheckCircle } from '@phosphor-icons/react';
import { API_BASE_URL } from '@/lib/api';

interface APIKey {
    id: string;
    name: string;
    key: string;
    createdAt: Date;
    lastUsed?: Date;
}

interface APIAccessPanelProps {
    workflowId?: string;
    apiKeys?: APIKey[];
    onCreateKey?: (name: string) => void;
    onDeleteKey?: (id: string) => void;
}

// API keys should be fetched from backend - no hardcoded samples
const defaultApiKeys: APIKey[] = [];

// Get actual base URL for API access
function getAPIBaseUrl(): string {
    // Use the configured API_BASE_URL, but make it accessible externally
    // Remove any localhost references for production display
    if (typeof window !== 'undefined') {
        // In browser, use the current origin or the configured API URL
        const apiUrl = API_BASE_URL || 'http://localhost:8000';
        // If it's localhost, show a production-like URL for reference
        if (apiUrl.includes('localhost') || apiUrl.includes('127.0.0.1')) {
            return apiUrl; // Show actual development URL
        }
        return apiUrl;
    }
    return API_BASE_URL || 'http://localhost:8000';
}

function CodeBlock({ code, language = 'bash' }: { code: string; language?: string }) {
    const [copied, setCopied] = useState(false);

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(code);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch (err) {
            console.error('Failed to copy to clipboard:', err);
        }
    };

    return (
        <div className="relative group">
            <pre className="p-4 bg-gray-900 text-gray-100 rounded-lg text-xs overflow-auto">
                <code>{code}</code>
            </pre>
            <Button
                variant="ghost"
                size="icon"
                className="absolute top-2 right-2 h-7 w-7 opacity-0 group-hover:opacity-100 transition-opacity bg-gray-800 hover:bg-gray-700"
                onClick={handleCopy}
            >
                {copied ? (
                    <CheckCircle className="h-4 w-4 text-green-400" weight="fill" />
                ) : (
                    <Copy className="h-4 w-4 text-gray-400" />
                )}
            </Button>
        </div>
    );
}

export function APIAccessPanel({
    workflowId = 'workflow-123',
    apiKeys = defaultApiKeys,
    onCreateKey,
    onDeleteKey,
}: APIAccessPanelProps) {
    const [showKeyModal, setShowKeyModal] = useState(false);
    const [newKeyName, setNewKeyName] = useState('');
    const [visibleKeys, setVisibleKeys] = useState<Set<string>>(new Set());

    // Use actual API base URL
    const baseUrl = useMemo(() => getAPIBaseUrl(), []);

    // Workflow-specific endpoint - uses the saved workflow ID
    const workflowEndpoint = `/api/v1/workflows/${workflowId}/run`;
    const fullEndpointUrl = `${baseUrl}${workflowEndpoint}`;

    // Check if this is a local (unsaved) workflow
    const isLocalWorkflow = workflowId?.startsWith('local-');

    const curlExample = `curl -X POST '${fullEndpointUrl}' \\
  -H 'Authorization: Bearer {API_KEY}' \\
  -H 'Content-Type: application/json' \\
  -d '{
    "inputs": {
      "query": "Your input here"
    },
    "version": null,
    "response_mode": "blocking",
    "user": "user-123"
  }'`;

    const curlWithVersionExample = `# Run specific version
curl -X POST '${fullEndpointUrl}' \\
  -H 'Authorization: Bearer {API_KEY}' \\
  -H 'Content-Type: application/json' \\
  -d '{
    "inputs": { "query": "Input text" },
    "version": 1
  }'`;

    const responseExample = `{
  "status": "completed",
  "message": "Workflow executed: 3/3 nodes",
  "results": { ... },
  "executed_nodes": 3,
  "total_nodes": 3,
  "outputs": [
    {
      "node_id": "output-1",
      "node_name": "Response",
      "output_type": "text",
      "content": "Generated response...",
      "timestamp": "2024-01-22T12:00:00Z"
    }
  ]
}`;

    const toggleKeyVisibility = (id: string) => {
        setVisibleKeys((prev) => {
            const next = new Set(prev);
            if (next.has(id)) {
                next.delete(id);
            } else {
                next.add(id);
            }
            return next;
        });
    };

    const handleCreateKey = () => {
        if (newKeyName.trim()) {
            onCreateKey?.(newKeyName);
            setNewKeyName('');
            setShowKeyModal(false);
        }
    };

    return (
        <div className="h-full flex flex-col bg-white dark:bg-zinc-950">
            <div className="px-4 py-2 border-b border-zinc-200 dark:border-zinc-800">
                <div className="flex items-center justify-between">
                    <div>
                        <h3 className="text-base font-semibold">API Access</h3>
                        <p className="text-sm text-muted-foreground">
                            Run this workflow via API
                        </p>
                    </div>
                    <Button size="sm" onClick={() => setShowKeyModal(true)}>
                        <Key className="h-4 w-4 mr-2" />
                        API Keys
                    </Button>
                </div>
            </div>

            <ScrollArea className="flex-1">
                <div className="p-4 space-y-6">
                    {/* Warning for local workflows */}
                    {isLocalWorkflow && (
                        <div className="p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                            <p className="text-sm text-yellow-800 font-medium">Workflow Not Saved</p>
                            <p className="text-xs text-yellow-700 mt-1">
                                Save this workflow to the backend to enable API access. Local workflows cannot be executed via API.
                            </p>
                        </div>
                    )}

                    {/* Base URL */}
                    <div className="space-y-2">
                        <Label>Base URL</Label>
                        <div className="flex items-center gap-2">
                            <Input value={baseUrl} readOnly className="font-mono text-sm" />
                            <Button
                                variant="outline"
                                size="icon"
                                onClick={() => navigator.clipboard.writeText(baseUrl).catch(console.error)}
                            >
                                <Copy className="h-4 w-4" />
                            </Button>
                        </div>
                    </div>

                    {/* Endpoint */}
                    <div className="space-y-3">
                        <div>
                            <h3 className="font-medium">Execute Workflow</h3>
                            <p className="text-sm text-muted-foreground">Run this workflow with provided inputs</p>
                        </div>
                        <div className="flex items-center gap-2">
                            <span className="px-2 py-1 bg-green-100 text-green-700 text-xs font-medium rounded">POST</span>
                            <code className="text-sm font-mono">{workflowEndpoint}</code>
                        </div>
                        <div className="flex items-center gap-2">
                            <Input value={fullEndpointUrl} readOnly className="font-mono text-xs" />
                            <Button
                                variant="outline"
                                size="icon"
                                onClick={() => navigator.clipboard.writeText(fullEndpointUrl).catch(console.error)}
                            >
                                <Copy className="h-4 w-4" />
                            </Button>
                        </div>
                    </div>

                    {/* Authentication */}
                    <div className="space-y-2 p-3 bg-muted/50 rounded-lg">
                        <h4 className="text-sm font-medium">Authentication</h4>
                        <p className="text-xs text-muted-foreground">
                            Use Bearer token authentication with your API key in the Authorization header.
                        </p>
                        <code className="text-xs bg-background px-2 py-1 rounded">
                            Authorization: Bearer {'{'}<span className="text-primary">API_KEY</span>{'}'}
                        </code>
                    </div>

                    {/* cURL Example */}
                    <div className="space-y-2">
                        <Label>cURL Example</Label>
                        <CodeBlock code={curlExample} />
                    </div>

                    {/* Response Example */}
                    <div className="space-y-2">
                        <Label>Response</Label>
                        <CodeBlock code={responseExample} language="json" />
                    </div>

                    {/* Request Parameters */}
                    <div className="space-y-3">
                        <Label>Request Parameters</Label>
                        <div className="space-y-2">
                            {[
                                { name: 'inputs', type: 'object', required: false, desc: 'Key-value pairs matching workflow input variables' },
                                { name: 'version', type: 'integer', required: false, desc: 'Specific version number to run (defaults to active published version)' },
                                { name: 'response_mode', type: 'string', required: false, desc: '"blocking" (default) or "streaming"' },
                                { name: 'user', type: 'string', required: false, desc: 'User identifier for logging' },
                            ].map((param) => (
                                <div key={param.name} className="flex items-start gap-3 p-2 bg-muted/30 rounded text-sm">
                                    <code className="font-mono text-primary">{param.name}</code>
                                    <span className="text-muted-foreground">{param.type}</span>
                                    {param.required && (
                                        <span className="text-xs px-1.5 py-0.5 bg-red-100 text-red-600 rounded">required</span>
                                    )}
                                    <span className="text-xs text-muted-foreground flex-1">{param.desc}</span>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* Version-specific execution */}
                    <div className="space-y-2">
                        <Label>Run Specific Version</Label>
                        <p className="text-xs text-muted-foreground">
                            Specify a version number to run a specific published version instead of the active deployment.
                        </p>
                        <CodeBlock code={curlWithVersionExample} />
                    </div>
                </div>
            </ScrollArea>

            {/* API Key Dialog - Accessible Radix Dialog with proper focus management */}
            <Dialog open={showKeyModal} onOpenChange={setShowKeyModal}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>API Secret Keys</DialogTitle>
                        <DialogDescription>
                            Manage API keys for authenticating workflow API requests.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div className="flex gap-2">
                            <Input
                                placeholder="Key name..."
                                value={newKeyName}
                                onChange={(e) => setNewKeyName(e.target.value)}
                                aria-label="New API key name"
                            />
                            <Button onClick={handleCreateKey}>
                                <Plus className="h-4 w-4 mr-1" />
                                Create
                            </Button>
                        </div>

                        <div className="space-y-2 max-h-60 overflow-auto">
                            {apiKeys.length === 0 ? (
                                <p className="text-sm text-muted-foreground text-center py-4">
                                    No API keys yet. Create one to get started.
                                </p>
                            ) : (
                                apiKeys.map((key) => (
                                    <div key={key.id} className="flex items-center gap-2 p-2 bg-muted/50 rounded">
                                        <div className="flex-1 min-w-0">
                                            <div className="text-sm font-medium">{key.name}</div>
                                            <div className="text-xs font-mono text-muted-foreground truncate">
                                                {visibleKeys.has(key.id) ? key.key : '••••••••••••••••'}
                                            </div>
                                        </div>
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            className="h-7 w-7"
                                            onClick={() => toggleKeyVisibility(key.id)}
                                            aria-label={visibleKeys.has(key.id) ? 'Hide API key' : 'Show API key'}
                                        >
                                            {visibleKeys.has(key.id) ? (
                                                <EyeSlash className="h-4 w-4" />
                                            ) : (
                                                <Eye className="h-4 w-4" />
                                            )}
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            className="h-7 w-7"
                                            onClick={() => navigator.clipboard.writeText(key.key).catch(console.error)}
                                            aria-label="Copy API key"
                                        >
                                            <Copy className="h-4 w-4" />
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            className="h-7 w-7 text-red-500 hover:text-red-600"
                                            onClick={() => onDeleteKey?.(key.id)}
                                            aria-label="Delete API key"
                                        >
                                            <Trash className="h-4 w-4" />
                                        </Button>
                                    </div>
                                ))
                            )}
                        </div>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
