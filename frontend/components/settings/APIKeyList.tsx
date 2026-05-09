'use client';

import { useAPIKeys, useDeleteAPIKey } from '@/hooks/useAPIKeys';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Trash2, Key, Loader2 } from 'lucide-react';
import { toast } from 'sonner';

export function APIKeyList() {
  const { data: keysData, isLoading, error } = useAPIKeys();
  const deleteKey = useDeleteAPIKey();

  // Ensure keys is always an array (defensive coding)
  const keys = Array.isArray(keysData) ? keysData : [];

  const handleDelete = async (id: string) => {
      try {
          await deleteKey.mutateAsync(id);
          toast("API Key deleted", { description: "The API key has been successfully removed." });
      } catch (err) {
          toast.error("Error", { description: "Failed to delete API key." });
      }
  };

  if (isLoading) {
      return <div className="flex justify-center p-8"><Loader2 className="h-8 w-8 animate-spin text-muted-foreground" /></div>;
  }

  if (error) {
      return <div className="text-destructive p-4">Error loading API keys.</div>;
  }

  return (
    <div className="space-y-4">
      {keys.length === 0 && (
          <div className="text-center p-8 border border-dashed rounded-lg text-muted-foreground">
              No API keys found. Add one to get started.
          </div>
      )}

      {keys.map((key) => (
        <Card key={key.id}>
          <CardContent className="flex items-center justify-between p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-muted rounded-full">
                <Key className="h-4 w-4 text-muted-foreground" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                    <p className="font-medium">{key.name}</p>
                    {key.is_default && <Badge variant="secondary" className="text-xs">Default</Badge>}
                </div>
                <p className="text-body-muted">
                  {key.provider} • {key.masked_key}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="icon"
                onClick={() => handleDelete(key.id)}
                disabled={deleteKey.isPending}
                className="text-muted-foreground hover:text-destructive"
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          </CardContent>
        </Card>
      ))}

    </div>
  );
}
