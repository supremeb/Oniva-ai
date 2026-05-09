'use client';

import React, { useEffect, useState } from 'react';
import { ConnectorMetadata, connectorsApi } from '@/lib/api/connectors';
import { ArrowLeft, CircleNotch, CheckCircle, XCircle } from '@phosphor-icons/react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { useParams, useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { Separator } from '@/components/ui/separator';
import Link from 'next/link';

export default function ConnectorDetailsPage() {
    const params = useParams();
    const router = useRouter();
    const id = params.id as string;

    const [connector, setConnector] = useState<ConnectorMetadata | null>(null);
    const [tools, setTools] = useState<any[]>([]);
    const [status, setStatus] = useState<{ status: string; message?: string } | null>(null);
    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
        const fetchDetails = async () => {
            setIsLoading(true);
            try {
                const [meta, toolList, stat] = await Promise.all([
                    connectorsApi.get(id),
                    connectorsApi.getTools(id),
                    connectorsApi.getStatus(id).catch(() => ({ status: 'unknown' }))
                ]);

                setConnector(meta);
                setTools(toolList);
                setStatus(stat);
            } catch (error) {
                console.error(error);
                toast.error('Error', { description: 'Failed to load connector details' });
            } finally {
                setIsLoading(false);
            }
        };

        if (id) {
            fetchDetails();
        }
    }, [id]);

    if (isLoading) {
        return (
            <div className="flex justify-center items-center h-screen">
                <CircleNotch className="h-8 w-8 animate-spin" />
            </div>
        );
    }

    if (!connector) {
        return <div className="p-6 lg:p-8 max-w-7xl mx-auto">Connector not found</div>;
    }

    const isConnected = status?.status === 'connected';

    return (
        <div className="p-6 lg:p-8 max-w-7xl mx-auto">
            {/* Header with back button */}
            <div className="mb-8">
                <div className="flex items-center gap-3 mb-2">
                    <Link href="/settings/integrations/connectors">
                        <Button variant="ghost" size="icon" className="h-8 w-8">
                            <ArrowLeft className="h-4 w-4" />
                        </Button>
                    </Link>
                    <div className="flex items-center gap-3">
                        <span className="text-4xl">{connector.icon}</span>
                        <h1 className="text-page-title">{connector.name}</h1>
                    </div>
                    <div className="ml-auto flex flex-col items-end gap-2">
                        <Badge variant={isConnected ? "secondary" : "destructive"} className={isConnected ? "bg-green-100 text-green-800" : ""}>
                            {isConnected ? (
                                <><CheckCircle className="w-4 h-4 mr-1" weight="fill" /> Connected</>
                            ) : (
                                <><XCircle className="w-4 h-4 mr-1" weight="fill" /> Disconnected</>
                            )}
                        </Badge>
                        <span className="text-xs text-muted-foreground">ID: {connector.id}</span>
                    </div>
                </div>
                <p className="text-page-description ml-11">{connector.description}</p>
            </div>

            <Separator className="my-6" />

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <div className="md:col-span-2">
                    <h2 className="text-overline mb-4">Available Tools</h2>
                    {tools.length === 0 ? (
                        <p className="text-muted-foreground">No tools available or connector not authenticated.</p>
                    ) : (
                        <div className="space-y-4">
                            {tools.map((tool) => (
                                <Card key={tool.name}>
                                    <CardHeader>
                                        <CardTitle className="text-base font-mono bg-muted p-1 inline-block rounded">
                                            {tool.name}
                                        </CardTitle>
                                        <CardDescription>{tool.description}</CardDescription>
                                    </CardHeader>
                                    <CardContent>
                                        <h4 className="text-label mb-2">Input Schema</h4>
                                        <pre className="bg-slate-50 p-2 rounded text-xs overflow-auto max-h-40 border">
                                            {JSON.stringify(tool.inputSchema, null, 2)}
                                        </pre>
                                    </CardContent>
                                </Card>
                            ))}
                        </div>
                    )}
                </div>

                <div>
                    <Card>
                        <CardHeader>
                            <CardTitle>Configuration</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="space-y-2 text-sm">
                                <div className="flex justify-between">
                                    <span className="text-muted-foreground">Version</span>
                                    <span>{connector.version}</span>
                                </div>
                                <div className="flex justify-between">
                                    <span className="text-muted-foreground">Auth Type</span>
                                    <span>{connector.auth_type}</span>
                                </div>
                                <div className="flex justify-between">
                                    <span className="text-muted-foreground">Category</span>
                                    <Badge variant="outline">{connector.category}</Badge>
                                </div>
                            </div>
                            <Button className="w-full mt-6" variant="outline" disabled>
                                Re-configure (Coming Soon)
                            </Button>
                        </CardContent>
                    </Card>
                </div>
            </div>
        </div>
    );
}
