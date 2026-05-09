'use client';

import { useState, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { CircleNotch, Sparkle, TestTube, Plus } from '@phosphor-icons/react';
import { toast } from 'sonner';
import {
  generateTestCases,
  createTestCase,
  type GeneratedTestCase,
} from '@/lib/api/agent-studio';

interface GenerateTestsModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agentId: string;
  instructions: string;
  onImported?: () => void;
}

export function GenerateTestsModal({
  open,
  onOpenChange,
  agentId,
  instructions,
  onImported,
}: GenerateTestsModalProps) {
  const [phase, setPhase] = useState<'idle' | 'generating' | 'review' | 'importing'>('idle');
  const [testCases, setTestCases] = useState<GeneratedTestCase[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const handleGenerate = useCallback(async () => {
    setPhase('generating');
    setError(null);
    try {
      const result = await generateTestCases(agentId, instructions, 5);
      setTestCases(result.test_cases);
      setSelected(new Set(result.test_cases.map((_, i) => i)));
      setPhase('review');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to generate test cases');
      setPhase('idle');
    }
  }, [agentId, instructions]);

  const handleImport = useCallback(async () => {
    const toImport = testCases.filter((_, i) => selected.has(i));
    if (toImport.length === 0) return;

    setPhase('importing');
    let imported = 0;
    for (const tc of toImport) {
      try {
        await createTestCase(agentId, {
          input: tc.input,
          expected_output: tc.expected_output,
          category: tc.category,
          evaluation_type: tc.evaluation_type,
          is_baseline: false,
        });
        imported++;
      } catch {
        // Skip failed imports
      }
    }
    toast.success(`Imported ${imported} test cases`);
    onImported?.();
    onOpenChange(false);
    // Reset state
    setPhase('idle');
    setTestCases([]);
    setSelected(new Set());
  }, [testCases, selected, agentId, onImported, onOpenChange]);

  const toggleSelect = (index: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === testCases.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(testCases.map((_, i) => i)));
    }
  };

  // Reset on close
  const handleOpenChange = (open: boolean) => {
    if (!open) {
      setPhase('idle');
      setTestCases([]);
      setSelected(new Set());
      setError(null);
    }
    onOpenChange(open);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkle className="h-4 w-4 text-primary" weight="fill" />
            Generate Test Cases
          </DialogTitle>
          <DialogDescription>
            AI will analyze your agent&apos;s instructions and generate diverse test scenarios.
          </DialogDescription>
        </DialogHeader>

        {/* Idle / Error state */}
        {phase === 'idle' && (
          <div className="py-6 text-center space-y-3">
            <TestTube className="h-10 w-10 mx-auto text-muted-foreground opacity-50" />
            <p className="text-sm text-muted-foreground">
              Generate test cases based on your agent&apos;s instructions to measure accuracy.
            </p>
            {error && (
              <p className="text-sm text-destructive">{error}</p>
            )}
          </div>
        )}

        {/* Generating */}
        {phase === 'generating' && (
          <div className="py-8 flex flex-col items-center gap-3">
            <CircleNotch className="h-8 w-8 animate-spin text-primary" />
            <p className="text-sm text-muted-foreground">Analyzing instructions and generating test cases...</p>
          </div>
        )}

        {/* Review */}
        {phase === 'review' && testCases.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <button
                onClick={toggleAll}
                className="text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                {selected.size === testCases.length ? 'Deselect all' : 'Select all'}
              </button>
              <Badge variant="secondary" className="text-xs">
                {selected.size}/{testCases.length} selected
              </Badge>
            </div>
            <ScrollArea className="max-h-[360px]">
              <div className="space-y-2 pr-2">
                {testCases.map((tc, i) => (
                  <button
                    key={i}
                    onClick={() => toggleSelect(i)}
                    className="w-full text-left p-3 rounded-lg border bg-card hover:bg-accent/50 transition-colors flex items-start gap-3"
                  >
                    <Checkbox
                      checked={selected.has(i)}
                      onCheckedChange={() => toggleSelect(i)}
                      className="mt-0.5"
                    />
                    <div className="flex-1 min-w-0 space-y-1">
                      <div className="flex items-center gap-2">
                        <Badge variant="outline" className="text-[10px] py-0 h-4">
                          {tc.category}
                        </Badge>
                        <Badge variant="secondary" className="text-[10px] py-0 h-4">
                          {tc.evaluation_type}
                        </Badge>
                      </div>
                      <p className="text-sm font-medium">{tc.input}</p>
                      <p className="text-xs text-muted-foreground line-clamp-2">
                        Expected: {tc.expected_output}
                      </p>
                    </div>
                  </button>
                ))}
              </div>
            </ScrollArea>
          </div>
        )}

        {/* Review with no results */}
        {phase === 'review' && testCases.length === 0 && (
          <div className="py-6 text-center">
            <p className="text-sm text-muted-foreground">No test cases were generated. Try again with more detailed instructions.</p>
          </div>
        )}

        {/* Importing */}
        {phase === 'importing' && (
          <div className="py-8 flex flex-col items-center gap-3">
            <CircleNotch className="h-8 w-8 animate-spin text-primary" />
            <p className="text-sm text-muted-foreground">Importing {selected.size} test cases...</p>
          </div>
        )}

        <DialogFooter>
          {phase === 'idle' && (
            <Button onClick={handleGenerate} disabled={!instructions.trim()}>
              <Sparkle className="h-4 w-4 mr-1.5" weight="fill" />
              Generate
            </Button>
          )}
          {phase === 'review' && (
            <>
              <Button variant="outline" onClick={() => handleOpenChange(false)}>
                Cancel
              </Button>
              <Button onClick={handleImport} disabled={selected.size === 0}>
                <Plus className="h-4 w-4 mr-1.5" />
                Import {selected.size} Test{selected.size !== 1 ? 's' : ''}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
