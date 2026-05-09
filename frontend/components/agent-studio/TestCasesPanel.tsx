'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { ConfirmationDialog } from '@/components/ui/confirmation-dialog';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import {
  TestTube,
  Plus,
  Play,
  Trash,
  DotsThreeVertical,
  MagnifyingGlass,
  CheckCircle,
  XCircle,
  CircleNotch,
  Upload,
  Download,
  Pencil,
  FloppyDisk,
  X,
  Sparkle,
} from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import {
  listTestCases,
  createTestCase,
  updateTestCase,
  deleteTestCase,
  runSingleTest,
  importTestCases,
  exportTestCases,
  type TestCaseResponse,
  type CreateTestCaseRequest,
} from '@/lib/api/agent-studio';
import { toast } from 'sonner';
import { isBuiltinAgentId } from '@/lib/builtin-agents';
import { GenerateTestsModal } from './GenerateTestsModal';

interface TestCasesPanelProps {
  agentId: string;
  instructions?: string;
  refreshTrigger?: number;
  onRunEvaluation?: () => void;
  isBuiltinAgent?: boolean;
}

const SOURCE_LABELS: Record<string, string> = {
  chat_feedback: 'From Chat',
  manual: 'Manual',
  imported: 'Imported',
};

const EVALUATION_TYPES = [
  { value: 'llm_judge', label: 'LLM Judge' },
  { value: 'exact', label: 'Exact Match' },
  { value: 'contains', label: 'Contains' },
];

export function TestCasesPanel({
  agentId,
  instructions,
  refreshTrigger = 0,
  onRunEvaluation,
  isBuiltinAgent,
}: TestCasesPanelProps) {
  // Auto-detect built-in agent if not explicitly provided (accepts both slugs and UUIDs)
  const isBuiltin = isBuiltinAgent ?? isBuiltinAgentId(agentId);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [generateModalOpen, setGenerateModalOpen] = useState(false);

  // State
  const [testCases, setTestCases] = useState<TestCaseResponse[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [runningTest, setRunningTest] = useState<string | null>(null);

  // Dialog state
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [editingTestCase, setEditingTestCase] = useState<TestCaseResponse | null>(null);
  const [deleteTestCaseId, setDeleteTestCaseId] = useState<string | null>(null);

  // Form state
  const [formData, setFormData] = useState<CreateTestCaseRequest>({
    input: '',
    expected_output: '',
    name: '',
    category: '',
    evaluation_type: 'llm_judge',
    is_baseline: false,
  });

  // Load test cases (skip for built-in agents)
  const loadTestCases = useCallback(async () => {
    // Built-in agents don't support custom test cases
    if (isBuiltin) {
      setIsLoading(false);
      setTestCases([]);
      return;
    }

    try {
      setIsLoading(true);
      const result = await listTestCases(agentId, { limit: 100 });
      setTestCases(result.items);
    } catch (e) {
      const errorMessage = e instanceof Error ? e.message : 'Unknown error';
      // Agent may only exist in localStorage (not yet synced to backend) — silently show empty
      if (
        errorMessage.includes('not found') ||
        errorMessage.includes('Not Found') ||
        errorMessage.includes('Authentication required') ||
        errorMessage.includes('Not authenticated')
      ) {
        setTestCases([]);
      } else {
        console.error('Failed to load test cases:', errorMessage);
        toast.error('Error', { description: errorMessage || 'Failed to load test cases' });
      }
    } finally {
      setIsLoading(false);
    }
  }, [agentId, isBuiltin]);

  useEffect(() => {
    loadTestCases();
  }, [loadTestCases, refreshTrigger]);

  // Get unique categories
  const categories = Array.from(
    new Set(testCases.map((tc) => tc.category).filter(Boolean))
  ) as string[];

  // Filter test cases
  const filteredTestCases = testCases.filter((tc) => {
    const matchesSearch =
      !searchQuery ||
      tc.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      tc.input.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesCategory =
      selectedCategory === 'all' || tc.category === selectedCategory;
    return matchesSearch && matchesCategory;
  });

  // Handle create/update
  const handleSave = async () => {
    try {
      if (editingTestCase) {
        await updateTestCase(agentId, editingTestCase.id, formData);
        toast('Test case updated');
      } else {
        await createTestCase(agentId, formData);
        toast('Test case created');
      }
      setCreateDialogOpen(false);
      setEditingTestCase(null);
      setFormData({
        input: '',
        expected_output: '',
        name: '',
        category: '',
        evaluation_type: 'llm_judge',
        is_baseline: false,
      });
      loadTestCases();
    } catch (e) {
      toast.error('Error', { description: 'Failed to save test case' });
    }
  };

  // Handle delete
  const handleDelete = (id: string) => {
    setDeleteTestCaseId(id);
  };

  const confirmDeleteTestCase = async () => {
    if (!deleteTestCaseId) return;
    try {
      await deleteTestCase(agentId, deleteTestCaseId);
      toast('Test case deleted');
      loadTestCases();
    } catch (e) {
      toast.error('Error', { description: 'Failed to delete test case' });
    }
  };

  // Handle run single test
  const handleRunTest = async (testCase: TestCaseResponse) => {
    setRunningTest(testCase.id);

    try {
      const result = await runSingleTest(agentId, testCase.id);

      // Update local state with result
      setTestCases((prev) =>
        prev.map((tc) =>
          tc.id === testCase.id
            ? {
                ...tc,
                last_result: {
                  passed: result.passed,
                  actual_output: result.actual_output,
                  score: result.score,
                  evaluated_at: new Date().toISOString(),
                },
              }
            : tc
        )
      );

      if (result.passed) {
        toast('Test Passed', { description: 'Response matched expectations' });
      } else {
        toast.error('Test Failed', { description: 'Response did not match expectations' });
      }
    } catch (e) {
      toast.error('Error', { description: 'Failed to run test' });
    } finally {
      setRunningTest(null);
    }
  };

  // Handle import
  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      const result = await importTestCases(agentId, file);
      const skippedMsg = result.skipped > 0 ? `, ${result.skipped} skipped` : '';
      toast('Import Complete', { description: `Imported ${result.imported} test cases${skippedMsg}` });
      loadTestCases();
    } catch (e) {
      toast.error('Import Failed', { description: 'Failed to import test cases' });
    }

    // Reset input
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  // Handle export
  const handleExport = async () => {
    try {
      const blob = await exportTestCases(agentId, 'json');
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `test-cases-${agentId}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast.error('Export Failed', { description: 'Failed to export test cases' });
    }
  };

  // Edit test case
  const handleEdit = (testCase: TestCaseResponse) => {
    setEditingTestCase(testCase);
    setFormData({
      input: testCase.input,
      expected_output: testCase.expected_output || '',
      name: testCase.name || '',
      category: testCase.category || '',
      evaluation_type: testCase.evaluation_type || 'llm_judge',
      is_baseline: testCase.is_baseline,
    });
    setCreateDialogOpen(true);
  };

  // Stats
  const totalTests = testCases.length;
  const baselineTests = testCases.filter((tc) => tc.is_baseline).length;
  const passedTests = testCases.filter((tc) => tc.last_result?.passed).length;

  return (
    <div className="flex flex-col h-full bg-background">
      {/* Header */}
      <div className="flex flex-col border-b">
        <div className="flex items-center justify-between p-3">
          <div className="flex items-center gap-2">
            <TestTube className="h-5 w-5 text-muted-foreground" />
            <span className="font-medium">Test Cases</span>
            <Badge variant="secondary" className="text-xs">
              {totalTests}
            </Badge>
          </div>
          {!isBuiltin && (
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={() => setGenerateModalOpen(true)}
                title="Generate test cases with AI"
                disabled={!instructions?.trim()}
              >
                <Sparkle className="h-4 w-4" weight="fill" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={() => {
                  setEditingTestCase(null);
                  setFormData({
                    input: '',
                    expected_output: '',
                    name: '',
                    category: '',
                    evaluation_type: 'llm_judge',
                    is_baseline: false,
                  });
                  setCreateDialogOpen(true);
                }}
                title="Add test case"
              >
                <Plus className="h-4 w-4" />
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" className="h-7 w-7">
                    <DotsThreeVertical className="h-4 w-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => fileInputRef.current?.click()}>
                    <Upload className="h-4 w-4 mr-2" />
                    Import
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={handleExport} disabled={totalTests === 0}>
                    <Download className="h-4 w-4 mr-2" />
                    Export
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={onRunEvaluation} disabled={totalTests === 0}>
                    <Play className="h-4 w-4 mr-2" />
                    Run All Tests
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          )}
        </div>

        {/* Search & Filter - only show for custom agents */}
        {!isBuiltin && (
          <div className="px-3 pb-3 flex gap-2">
            <div className="relative flex-1">
              <MagnifyingGlass className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                placeholder="Search..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="h-8 pl-8 text-sm"
              />
            </div>
            {categories.length > 0 && (
              <Select value={selectedCategory} onValueChange={setSelectedCategory}>
                <SelectTrigger className="h-8 w-24 text-xs">
                  <SelectValue placeholder="Category" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All</SelectItem>
                  {categories.map((cat) => (
                    <SelectItem key={cat} value={cat}>
                      {cat}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
        )}

        {/* Stats - only show for custom agents with tests */}
        {!isBuiltin && totalTests > 0 && (
          <div className="px-3 pb-2 flex gap-3 text-xs text-muted-foreground">
            <span>{baselineTests} baseline</span>
            <span className="text-green-600">{passedTests} passed</span>
            <span className="text-red-600">{totalTests - passedTests} failed</span>
          </div>
        )}
      </div>

      {/* Test Cases List */}
      <ScrollArea className="flex-1">
        {isLoading ? (
          <div className="flex items-center justify-center h-32">
            <CircleNotch className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : filteredTestCases.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 text-center text-muted-foreground p-4">
            <TestTube className="h-8 w-8 mb-2 opacity-50" />
            {isBuiltin ? (
              <>
                <p className="text-sm font-medium">Built-in Agent</p>
                <p className="text-xs mt-1">
                  Built-in agents use predefined behavior and don&apos;t support custom test cases
                </p>
              </>
            ) : searchQuery ? (
              <p className="text-sm">No test cases match your search</p>
            ) : (
              <>
                <p className="text-sm font-medium">No test cases yet</p>
                <p className="text-xs mt-1">
                  Create test cases or rate chat responses
                </p>
              </>
            )}
          </div>
        ) : (
          <div className="p-2 space-y-2">
            {filteredTestCases.map((testCase) => (
              <div
                key={testCase.id}
                className={cn(
                  'p-3 rounded-lg border bg-card hover:bg-accent/50 transition-colors',
                  testCase.last_result?.passed === true && 'border-l-2 border-l-green-500',
                  testCase.last_result?.passed === false && 'border-l-2 border-l-red-500'
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-medium text-sm truncate">
                        {testCase.name || 'Untitled'}
                      </span>
                      {testCase.is_baseline && (
                        <Badge variant="outline" className="text-[10px] py-0 h-4">
                          Baseline
                        </Badge>
                      )}
                      {testCase.source && (
                        <Badge variant="secondary" className="text-[10px] py-0 h-4">
                          {SOURCE_LABELS[testCase.source] || testCase.source}
                        </Badge>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground line-clamp-2">
                      {testCase.input}
                    </p>
                    {testCase.last_result && (
                      <div className="flex items-center gap-1 mt-1.5">
                        {testCase.last_result.passed ? (
                          <CheckCircle className="h-3 w-3 text-green-600" weight="fill" />
                        ) : (
                          <XCircle className="h-3 w-3 text-red-600" weight="fill" />
                        )}
                        <span className="text-[10px] text-muted-foreground">
                          {testCase.last_result.passed ? 'Passed' : 'Failed'}
                          {testCase.last_result.score !== undefined &&
                            ` (${(testCase.last_result.score * 100).toFixed(0)}%)`}
                        </span>
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      onClick={() => handleRunTest(testCase)}
                      disabled={runningTest === testCase.id}
                      title="Run test"
                    >
                      {runningTest === testCase.id ? (
                        <CircleNotch className="h-3 w-3 animate-spin" />
                      ) : (
                        <Play className="h-3 w-3" />
                      )}
                    </Button>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon" className="h-6 w-6">
                          <DotsThreeVertical className="h-3 w-3" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => handleEdit(testCase)}>
                          <Pencil className="h-3 w-3 mr-2" />
                          Edit
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onClick={() => handleDelete(testCase.id)}
                          className="text-red-600"
                        >
                          <Trash className="h-3 w-3 mr-2" />
                          Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </ScrollArea>

      {/* Run All Button - only for custom agents with test cases */}
      {!isBuiltin && totalTests > 0 && (
        <div className="p-3 border-t">
          <Button
            className="w-full gap-2"
            size="sm"
            onClick={onRunEvaluation}
          >
            <Play className="h-4 w-4" />
            Run Evaluation ({totalTests} tests)
          </Button>
        </div>
      )}

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".json,.csv"
        className="hidden"
        onChange={handleImport}
      />

      {/* Create/Edit Dialog */}
      <Dialog open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {editingTestCase ? 'Edit Test Case' : 'Create Test Case'}
            </DialogTitle>
            <DialogDescription>
              {editingTestCase
                ? 'Update the test case details'
                : 'Add a new test case for your agent'}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="name">Name</Label>
              <Input
                id="name"
                placeholder="Test case name"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="input">Input *</Label>
              <Textarea
                id="input"
                placeholder="User message to test"
                value={formData.input}
                onChange={(e) => setFormData({ ...formData, input: e.target.value })}
                rows={3}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="expected">Expected Output</Label>
              <Textarea
                id="expected"
                placeholder="Expected response (for exact/contains matching)"
                value={formData.expected_output}
                onChange={(e) =>
                  setFormData({ ...formData, expected_output: e.target.value })
                }
                rows={3}
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="category">Category</Label>
                <Input
                  id="category"
                  placeholder="e.g., greetings"
                  value={formData.category}
                  onChange={(e) => setFormData({ ...formData, category: e.target.value })}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="eval-type">Evaluation Type</Label>
                <Select
                  value={formData.evaluation_type}
                  onValueChange={(v) =>
                    setFormData({ ...formData, evaluation_type: v as any })
                  }
                >
                  <SelectTrigger id="eval-type">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {EVALUATION_TYPES.map((type) => (
                      <SelectItem key={type.value} value={type.value}>
                        {type.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="flex items-center space-x-2">
              <Checkbox
                id="baseline"
                checked={formData.is_baseline}
                onCheckedChange={(checked) =>
                  setFormData({ ...formData, is_baseline: checked as boolean })
                }
              />
              <Label htmlFor="baseline" className="text-sm font-normal">
                Include in baseline evaluation
              </Label>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={!formData.input.trim()}>
              <FloppyDisk className="h-4 w-4 mr-2" />
              {editingTestCase ? 'Update' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmationDialog
        open={!!deleteTestCaseId}
        onOpenChange={(open) => { if (!open) setDeleteTestCaseId(null); }}
        title="Delete Test Case"
        description="This will permanently delete this test case. This cannot be undone."
        onConfirm={confirmDeleteTestCase}
      />

      <GenerateTestsModal
        open={generateModalOpen}
        onOpenChange={setGenerateModalOpen}
        agentId={agentId}
        instructions={instructions || ''}
        onImported={loadTestCases}
      />
    </div>
  );
}
