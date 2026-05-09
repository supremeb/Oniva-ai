'use client';

import { useConnectors, useDeleteConnector, useTestConnector } from '@/hooks/useConnectors';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Trash2, Plus, Plug, Loader2, Play } from 'lucide-react';
import { useState } from 'react';
import { ConnectorForm } from './ConnectorForm';
import { toast } from 'sonner';

export function ConnectorList() {
  const { data: connectors, isLoading, error } = useConnectors();
  const deleteConnector = useDeleteConnector();
  const testConnector = useTestConnector();
  const [showForm, setShowForm] = useState(false);

  const handleDelete = async (id: string) => {
    try {
      await deleteConnector.mutateAsync(id);
      toast('Connector deleted', { description: 'The connector has been successfully removed.' });
    } catch (err) {
      toast.error('Error', { description: 'Failed to delete connector.' });
    }
  };

  const handleTest = async (id: string) => {
      try {
          const result = await testConnector.mutateAsync(id);
          if (result.success) {
              toast("Connection Successful", { description: result.message || "Successfully connected to the data source." });
          } else {
              toast.error("Connection Failed", { description: result.message || "Could not connect to the data source." });
          }
      } catch (err) {
          toast.error("Error", { description: "Failed to test connection." });
      }
  }

  if (isLoading) {
    return (
      <div className="flex justify-center p-8">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error) {
    return <div className="text-destructive p-4">Error loading connectors.</div>;
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="text-lg font-semibold">Connectors</h2>
        <Button onClick={() => setShowForm(true)}>
          <Plus className="h-4 w-4 mr-2" />
          Add Connector
        </Button>
      </div>

      {connectors?.length === 0 && (
        <div className="text-center p-8 border border-dashed rounded-lg text-muted-foreground">
          No connectors found. Add one to connect data sources.
        </div>
      )}

      {connectors?.map((connector) => (
        <Card key={connector.id}>
          <CardContent className="flex items-center justify-between p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-muted rounded-full">
                <Plug className="h-4 w-4 text-muted-foreground" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <p className="font-medium">{connector.name}</p>
                  <Badge variant="outline" className="uppercase text-[10px]">
                    {connector.type}
                  </Badge>
                  {connector.is_active ? (
                    <Badge className="bg-green-500 hover:bg-green-600">Active</Badge>
                  ) : (
                    <Badge variant="secondary">Inactive</Badge>
                  )}
                </div>
                <p className="text-sm text-muted-foreground truncate max-w-md">
                   {/* Display a preview of config if needed, or just type */}
                   {Object.keys(connector.config).length} config keys
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button 
                variant="outline" 
                size="sm" 
                onClick={() => handleTest(connector.id)}
                disabled={testConnector.isPending}
              >
                  {testConnector.isPending && testConnector.variables === connector.id ? (
                      <Loader2 className="h-3 w-3 mr-2 animate-spin" />
                  ) : (
                      <Play className="h-3 w-3 mr-2" />
                  )}
                  Test
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => handleDelete(connector.id)}
                disabled={deleteConnector.isPending}
                className="text-muted-foreground hover:text-destructive"
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          </CardContent>
        </Card>
      ))}

      {showForm && <ConnectorForm onClose={() => setShowForm(false)} />}
    </div>
  );
}
