'use client';

import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { cn } from '@/lib/utils';
import { API_BASE_URL } from '@/lib/api';
import {
  ChainOfThought,
  ChatEmptyState,
  ChatLoadingIndicator,
  ChatErrorBoundary,
} from '@/components/chat/shared';
import { ArtifactCreationSkeleton } from '@/components/chat/shared/ArtifactCreationSkeleton';
import { ArtifactModal } from '@/components/chat/shared/ArtifactModal';
import type { ArtifactModalData } from '@/components/chat/shared/data/FilePreview';
import type { MessagePart, SourceItem } from '@/lib/models';
import type { ToolCallMeta } from '@/hooks/useStreamingParts';
import {
  Play,
  ArrowClockwise,
  List,
  Lightning,
  Code,
  Table as TableIcon,
  ChartBar,
  Database,
  GitDiff,
  Clock,
  Gauge,
  Warning,
  FileText,
  Link as LinkIcon,
  BookOpen,
  TextAa,
  Question,
  ShieldCheck,
  ListChecks,
  Wrench,
  Brain,
  Sparkle,
  FileMagnifyingGlass,
  Article,
} from '@phosphor-icons/react';

interface TestCommand {
  id: string;
  label: string;
  command: string;
  icon: React.ComponentType<{ className?: string; weight?: 'regular' | 'bold' | 'fill' }>;
  description: string;
}

const TEST_COMMANDS: TestCommand[] = [
  { id: 'showcase', label: 'Full Showcase', command: 'showcase', icon: Lightning, description: 'Complete component demonstration' },
  { id: 'thinking', label: 'Thinking', command: 'show thinking', icon: Brain, description: 'Thinking accordion + tool calls' },
  { id: 'text', label: 'Text Streaming', command: 'show text', icon: TextAa, description: 'Markdown with streaming' },
  { id: 'code', label: 'Code Block', command: 'show code', icon: Code, description: 'Syntax highlighted code' },
  { id: 'tool', label: 'Tool Call', command: 'show tool', icon: Wrench, description: 'Tool execution progress' },
  { id: 'question', label: 'Question', command: 'show question', icon: Question, description: 'Agent question options' },
  { id: 'approval', label: 'Approval', command: 'show approval', icon: ShieldCheck, description: 'Human approval card' },
  { id: 'table', label: 'Data Table', command: 'show table', icon: TableIcon, description: 'Sortable data table' },
  { id: 'chart', label: 'Chart', command: 'show chart', icon: ChartBar, description: 'Chart visualization' },
  { id: 'sql', label: 'SQL Query', command: 'show sql', icon: Database, description: 'SQL preview & results' },
  { id: 'json', label: 'JSON Viewer', command: 'show json', icon: Code, description: 'Collapsible JSON tree' },
  { id: 'diff', label: 'Diff View', command: 'show diff', icon: GitDiff, description: 'Git-style diff' },
  { id: 'timeline', label: 'Timeline', command: 'show timeline', icon: Clock, description: 'Event timeline' },
  { id: 'metrics', label: 'Metrics', command: 'show metrics', icon: Gauge, description: 'KPI metric cards' },
  { id: 'status', label: 'Status Messages', command: 'show status', icon: Warning, description: 'Alert types' },
  { id: 'file', label: 'File Preview', command: 'show file', icon: FileText, description: 'File download cards' },
  { id: 'link', label: 'Link Preview', command: 'show link', icon: LinkIcon, description: 'URL preview cards' },
  { id: 'source', label: 'Sources', command: 'show source', icon: BookOpen, description: 'Citation cards' },
  { id: 'steps', label: 'Steps', command: 'show steps', icon: ListChecks, description: 'Progress steps' },
  { id: 'accordion', label: 'Tool Accordion', command: 'show accordion', icon: Wrench, description: 'Expandable tool calls with sources' },
  { id: 'skeleton', label: 'Artifact Skeleton', command: 'show skeleton', icon: Sparkle, description: 'Shimmer loading animation' },
  { id: 'artifact', label: 'Artifact Card + Modal', command: 'show artifact', icon: Article, description: 'File preview → full modal' },
];

export default function UITestPage() {
  const [parts, setParts] = useState<MessagePart[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeCommand, setActiveCommand] = useState<string | null>(null);
  const [isEnabled, setIsEnabled] = useState<boolean | null>(null);
  const [toolCallMeta, setToolCallMeta] = useState<Map<string, ToolCallMeta>>(new Map());
  const [sources, setSources] = useState<SourceItem[]>([]);
  const [artifactModal, setArtifactModal] = useState<(ArtifactModalData & { open: boolean }) | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Check if UI testing is enabled
  useEffect(() => {
    const checkStatus = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/v1/chat/ui-test/status`, {
          credentials: 'include',
        });
        if (response.ok) {
          const data = await response.json();
          setIsEnabled(data.enabled);
        } else {
          setIsEnabled(false);
        }
      } catch {
        setIsEnabled(false);
      }
    };
    checkStatus();
  }, []);

  // Client-side simulation for thinking demo (no backend needed)
  // Showcases unified chain-of-thought: thinking + multiple tool types + response
  const runThinkingDemo = useCallback(async () => {
    setParts([]);
    setError(null);
    setActiveCommand('show thinking');
    setIsStreaming(true);

    const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

    // Helper to update parts immutably
    const addPart = (part: MessagePart) => setParts(prev => [...prev, part]);
    const updateToolStatus = (id: string, status: string, extras?: Partial<MessagePart>) =>
      setParts(prev => prev.map(p =>
        p.type === 'tool_call' && (p as any).id === id
          ? { ...p, status, ...extras } as MessagePart
          : p
      ));

    // Phase 1: Thinking text streams in
    const thinkingChunks = [
      'The user wants to understand their ESG compliance landscape. ',
      'I should check their knowledge base for existing compliance documents, ',
      'search the web for latest regulatory updates, ',
      'and check their workspace for any recent reports.',
    ];
    let thinkingContent = '';
    for (const chunk of thinkingChunks) {
      thinkingContent += chunk;
      setParts(prev => {
        const others = prev.filter(p => p.type !== 'thinking');
        return [{
          type: 'thinking',
          variant: 'accordion',
          thinkingContent,
          isThinking: true,
          showElapsed: true,
        } as MessagePart, ...others];
      });
      await sleep(350);
    }

    // Phase 2: Tool calls arrive one by one (search_knowledge → running)
    addPart({ type: 'tool_call', id: 'tc-1', toolName: 'search_knowledge', status: 'running' } as MessagePart);
    await sleep(1200);
    updateToolStatus('tc-1', 'success', { durationMs: 1120 } as any);

    // list_workspace → running
    addPart({ type: 'tool_call', id: 'tc-2', toolName: 'list_workspace', status: 'running' } as MessagePart);
    await sleep(800);
    updateToolStatus('tc-2', 'success', { durationMs: 780 } as any);

    // search_knowledge again
    addPart({ type: 'tool_call', id: 'tc-3', toolName: 'search_knowledge', status: 'running' } as MessagePart);
    await sleep(900);
    updateToolStatus('tc-3', 'success', { durationMs: 870 } as any);

    // web_search
    addPart({ type: 'tool_call', id: 'tc-4', toolName: 'web_search', status: 'running' } as MessagePart);
    await sleep(1500);
    updateToolStatus('tc-4', 'success', { durationMs: 1430 } as any);

    // web_search again
    addPart({ type: 'tool_call', id: 'tc-5', toolName: 'web_search', status: 'running' } as MessagePart);
    await sleep(1100);
    updateToolStatus('tc-5', 'success', { durationMs: 1050 } as any);

    // fetch_from_connector
    addPart({ type: 'tool_call', id: 'tc-6', toolName: 'fetch_from_connector', status: 'running' } as MessagePart);
    await sleep(600);
    updateToolStatus('tc-6', 'success', { durationMs: 540 } as any);

    await sleep(200);

    // Phase 3: Mark thinking as done
    setParts(prev => prev.map(p =>
      p.type === 'thinking' ? { ...p, isThinking: false } as MessagePart : p
    ));
    await sleep(300);

    // Phase 4: Stream response text (chain collapses automatically)
    const responseChunks = [
      'Based on my research, here\'s the current **ESG compliance landscape** for your organization:\n\n',
      '## Current ESG Compliance Frameworks\n\n',
      '### European Union (SFDR)\n',
      'The Sustainable Finance Disclosure Regulation requires fund managers to classify products as Article 6, 8, or 9 based on sustainability characteristics.\n\n',
      '### SEC Climate Disclosure\n',
      'New SEC rules require public companies to report Scope 1 and 2 greenhouse gas emissions in annual filings.\n\n',
      '### Your Workspace Findings\n',
      'I found **3 compliance reports** in your workspace and **12 relevant knowledge base documents** covering SFDR, TCFD, and SEC requirements.\n\n',
      '> Your organization appears to be **Article 8 compliant** but may need updates for the latest SEC climate disclosure rules effective Q3 2026.',
    ];
    let responseText = '';
    for (const chunk of responseChunks) {
      responseText += chunk;
      setParts(prev => {
        const filtered = prev.filter(p => p.type !== 'text');
        return [...filtered, { type: 'text', content: responseText, isStreaming: true }];
      });
      await sleep(120);
    }

    // Finalize
    setParts(prev => prev.map(p => {
      if (p.type === 'text') return { ...p, isStreaming: false };
      return p;
    }));
    setIsStreaming(false);
  }, []);

  // ── Tool Accordion demo: shows enriched tool calls with labels, args, results, sources
  const runAccordionDemo = useCallback(async () => {
    setParts([]);
    setError(null);
    setActiveCommand('show accordion');
    setIsStreaming(true);

    const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

    // Build tool call meta with rich data
    const meta = new Map<string, ToolCallMeta>();

    // Tool 1: search with sources
    meta.set('tc-acc-1', {
      id: 'tc-acc-1',
      toolName: 'web_search',
      label: "Searching for 'ESG compliance frameworks 2026'",
      status: 'running',
      args: { query: 'ESG compliance frameworks 2026', max_results: 5 },
      sources: [],
    });
    setToolCallMeta(new Map(meta));
    setParts([
      { type: 'thinking', variant: 'accordion', thinkingContent: 'Let me search for the latest ESG compliance information...', isThinking: true } as MessagePart,
      { type: 'tool_call', id: 'tc-acc-1', toolName: 'web_search', status: 'running' } as MessagePart,
    ]);
    await sleep(1500);

    // Complete tool 1 with sources
    meta.set('tc-acc-1', {
      ...meta.get('tc-acc-1')!,
      status: 'success',
      durationMs: 1420,
      result: { results: ['SFDR Article 8 guidelines...', 'SEC Climate Disclosure Rule...'] },
      sources: [
        { title: 'EU SFDR Regulation', url: 'https://ec.europa.eu/sfdr', snippet: 'Sustainable Finance Disclosure Regulation framework...' },
        { title: 'SEC Climate Rules 2026', url: 'https://sec.gov/climate-disclosure', snippet: 'Final rule on climate-related disclosures...' },
        { title: 'TCFD Recommendations', url: 'https://fsb-tcfd.org', snippet: 'Task Force on Climate-related Financial Disclosures...' },
      ],
    });
    setToolCallMeta(new Map(meta));
    setParts(prev => prev.map(p =>
      p.type === 'tool_call' && (p as any).id === 'tc-acc-1'
        ? { ...p, status: 'success', durationMs: 1420 } as MessagePart
        : p
    ));
    await sleep(500);

    // Tool 2: knowledge base search
    meta.set('tc-acc-2', {
      id: 'tc-acc-2',
      toolName: 'search_documents',
      label: "Searching documents for 'carbon emissions report'",
      status: 'running',
      args: { query: 'carbon emissions report', knowledge_base_id: 'kb-001' },
      sources: [],
    });
    setToolCallMeta(new Map(meta));
    setParts(prev => [...prev, { type: 'tool_call', id: 'tc-acc-2', toolName: 'search_documents', status: 'running' } as MessagePart]);
    await sleep(1000);

    meta.set('tc-acc-2', {
      ...meta.get('tc-acc-2')!,
      status: 'success',
      durationMs: 920,
      result: { documents: [{ title: 'Q4 Emissions Report', score: 0.94 }] },
      sources: [
        { title: 'Q4 Emissions Report (relevance: 94%)', url: '/knowledge-bases/kb-001/documents/doc-1', snippet: 'Total Scope 1 emissions decreased 12% YoY...' },
        { title: 'Annual Sustainability Review (relevance: 87%)', url: '/knowledge-bases/kb-001/documents/doc-2', snippet: 'The board approved new sustainability targets...' },
      ],
    });
    setToolCallMeta(new Map(meta));
    setParts(prev => prev.map(p =>
      p.type === 'tool_call' && (p as any).id === 'tc-acc-2'
        ? { ...p, status: 'success', durationMs: 920 } as MessagePart
        : p
    ));
    await sleep(500);

    // Tool 3: failed tool
    meta.set('tc-acc-3', {
      id: 'tc-acc-3',
      toolName: 'fetch_from_connector',
      label: 'Fetching from Bloomberg Terminal',
      status: 'running',
      args: { connector_name: 'bloomberg', endpoint: '/esg-scores' },
      sources: [],
    });
    setToolCallMeta(new Map(meta));
    setParts(prev => [...prev, { type: 'tool_call', id: 'tc-acc-3', toolName: 'fetch_from_connector', status: 'running' } as MessagePart]);
    await sleep(800);

    meta.set('tc-acc-3', {
      ...meta.get('tc-acc-3')!,
      status: 'error',
      durationMs: 750,
      error: 'Connection timeout: Bloomberg API did not respond within 5s',
    });
    setToolCallMeta(new Map(meta));
    setParts(prev => prev.map(p =>
      p.type === 'tool_call' && (p as any).id === 'tc-acc-3'
        ? { ...p, status: 'error', durationMs: 750, error: 'Connection timeout: Bloomberg API did not respond within 5s' } as MessagePart
        : p
    ));

    // Aggregate sources
    const allSources: SourceItem[] = [
      { title: 'EU SFDR Regulation', url: 'https://ec.europa.eu/sfdr', snippet: 'Sustainable Finance Disclosure Regulation framework...' },
      { title: 'SEC Climate Rules 2026', url: 'https://sec.gov/climate-disclosure', snippet: 'Final rule on climate-related disclosures...' },
      { title: 'TCFD Recommendations', url: 'https://fsb-tcfd.org', snippet: 'Task Force on Climate-related Financial Disclosures...' },
      { title: 'Q4 Emissions Report', url: '/knowledge-bases/kb-001/documents/doc-1', snippet: 'Total Scope 1 emissions decreased 12% YoY...' },
    ];
    setSources(allSources);

    await sleep(300);

    // Mark thinking done and add response
    setParts(prev => {
      const updated = prev.map(p =>
        p.type === 'thinking' ? { ...p, isThinking: false } as MessagePart : p
      );
      return [...updated, {
        type: 'text',
        content: '## ESG Compliance Summary\n\nBased on web research and your knowledge base, here are the key findings:\n\n1. **SFDR compliance** — Your fund products are currently classified as Article 8\n2. **SEC disclosure** — New climate rules require Scope 1+2 reporting by Q3 2026\n3. **TCFD alignment** — Your sustainability reports follow TCFD recommendations\n\n> Note: Bloomberg connector timed out. Retry for ESG scores.',
        isStreaming: false,
      } as MessagePart];
    });

    setIsStreaming(false);
  }, []);

  // ── Skeleton demo: shows shimmer loading animation
  const runSkeletonDemo = useCallback(() => {
    setParts([]);
    setError(null);
    setActiveCommand('show skeleton');
    setToolCallMeta(new Map());
    setSources([]);
    // Not streaming — just show static skeletons
    setIsStreaming(false);
  }, []);

  // ── Artifact demo: shows enhanced FilePreview cards with content + click-to-open-modal
  const runArtifactDemo = useCallback(() => {
    setParts([]);
    setError(null);
    setActiveCommand('show artifact');
    setToolCallMeta(new Map());
    setSources([]);
    setIsStreaming(false);

    const sampleParts: MessagePart[] = [
      {
        type: 'text',
        content: 'I\'ve generated the analysis report and a Python script for you:',
        isStreaming: false,
      } as MessagePart,
      {
        type: 'file',
        filename: 'ESG_Analysis_Report.md',
        fileType: 'MD',
        size: 4200,
        content: '# ESG Analysis Report\n\n## Executive Summary\n\nThe analysis reveals significant growth potential in sustainable investment strategies across European markets.\n\n## Key Findings\n\n### 1. Environmental Metrics\n- Carbon intensity reduced by 23% YoY\n- Renewable energy usage increased to 67%\n- Water consumption decreased by 15%\n\n### 2. Social Governance\n- Board diversity improved to 42% female representation\n- Employee satisfaction score: 8.4/10\n- Zero workplace safety incidents in Q4\n\n### 3. Governance Standards\n- Independent directors: 7 of 9 board members\n- Executive compensation aligned with ESG targets\n- Annual sustainability audit passed\n\n## Recommendations\n\n1. **Increase Scope 3 reporting** — Currently only Scope 1 & 2 are tracked\n2. **Set Science-Based Targets** — Align with SBTi for 1.5°C pathway\n3. **Enhance supply chain due diligence** — Map Tier 2+ suppliers\n\n> Overall ESG Score: **A-** (up from B+ last quarter)',
      } as MessagePart,
      {
        type: 'file',
        filename: 'esg_metrics_calculator.py',
        fileType: 'PY',
        size: 1800,
        content: 'import pandas as pd\nimport numpy as np\nfrom dataclasses import dataclass\n\n@dataclass\nclass ESGMetrics:\n    """Calculate ESG scores from raw data."""\n    carbon_intensity: float\n    renewable_pct: float\n    diversity_score: float\n    governance_score: float\n\n    @property\n    def environmental_score(self) -> float:\n        return (1 - self.carbon_intensity / 100) * 0.6 + self.renewable_pct * 0.4\n\n    @property\n    def social_score(self) -> float:\n        return self.diversity_score\n\n    @property\n    def overall_score(self) -> float:\n        return (\n            self.environmental_score * 0.4\n            + self.social_score * 0.3\n            + self.governance_score * 0.3\n        )\n\n    def grade(self) -> str:\n        score = self.overall_score\n        if score >= 0.9: return "A+"\n        if score >= 0.8: return "A"\n        if score >= 0.7: return "A-"\n        if score >= 0.6: return "B+"\n        return "B"\n\n# Example usage\nmetrics = ESGMetrics(\n    carbon_intensity=23.5,\n    renewable_pct=0.67,\n    diversity_score=0.84,\n    governance_score=0.91,\n)\nprint(f"ESG Grade: {metrics.grade()}")\nprint(f"Overall Score: {metrics.overall_score:.2%}")',
      } as MessagePart,
      {
        type: 'file',
        filename: 'portfolio_data.csv',
        fileType: 'CSV',
        size: 320,
        content: 'company,sector,esg_score,carbon_intensity,revenue_m\nApple Inc,Technology,A,12.3,394500\nMicrosoft,Technology,A+,8.1,211915\nNestle,Consumer,B+,45.2,94424\nShell PLC,Energy,C,89.7,386201\nUnilever,Consumer,A-,22.8,60073',
      } as MessagePart,
    ];

    setParts(sampleParts);
  }, []);

  const handleOpenModal = useCallback((data: ArtifactModalData) => {
    setArtifactModal({ ...data, open: true });
  }, []);

  const runCommand = useCallback(async (command: string) => {
    if (isStreaming) {
      abortControllerRef.current?.abort();
      return;
    }

    // Handle client-side demos
    if (command === 'show thinking') {
      runThinkingDemo();
      return;
    }
    if (command === 'show accordion') {
      runAccordionDemo();
      return;
    }
    if (command === 'show skeleton') {
      runSkeletonDemo();
      return;
    }
    if (command === 'show artifact') {
      runArtifactDemo();
      return;
    }

    setParts([]);
    setError(null);
    setActiveCommand(command);
    setIsStreaming(true);
    abortControllerRef.current = new AbortController();

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
      };

      // Add CSRF token for cookie-based auth
      const csrfMatch = document.cookie.match(/(?:^|; )csrf_token=([^;]*)/);
      if (csrfMatch) {
        headers['X-CSRF-Token'] = decodeURIComponent(csrfMatch[1]);
      }

      const response = await fetch(`${API_BASE_URL}/api/v1/chat/ui-test`, {
        method: 'POST',
        headers,
        credentials: 'include',
        body: JSON.stringify({ command }),
        signal: abortControllerRef.current.signal,
      });

      if (!response.ok) {
        const errorBody = await response.json().catch(() => ({}));
        throw new Error(errorBody.detail || `HTTP ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let buffer = '';
      let currentEventType: string | null = null;
      let accumulatedText = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || trimmed.startsWith(':')) continue;

          if (trimmed.startsWith('event:')) {
            currentEventType = trimmed.slice(6).trim();
            continue;
          }

          if (trimmed.startsWith('data:')) {
            const data = trimmed.slice(5).trim();
            if (!data) continue;

            try {
              const parsed = JSON.parse(data);

              switch (currentEventType) {
                case 'text_delta':
                  accumulatedText += parsed.delta || '';
                  setParts(prev => {
                    const lastPart = prev[prev.length - 1];
                    if (lastPart?.type === 'text') {
                      return [
                        ...prev.slice(0, -1),
                        { ...lastPart, content: accumulatedText, isStreaming: true }
                      ];
                    }
                    return [...prev, { type: 'text', content: accumulatedText, isStreaming: true }];
                  });
                  break;

                case 'part':
                  // If we have accumulated text, finalize it first
                  if (accumulatedText) {
                    setParts(prev => {
                      const lastPart = prev[prev.length - 1];
                      if (lastPart?.type === 'text') {
                        return [
                          ...prev.slice(0, -1),
                          { ...lastPart, content: accumulatedText, isStreaming: false },
                          parsed.part
                        ];
                      }
                      return [...prev, parsed.part];
                    });
                    accumulatedText = '';
                  } else if (parsed.part?.type === 'thinking') {
                    // Replace existing thinking part (singleton per message)
                    setParts(prev => {
                      const hasThinking = prev.some(p => p.type === 'thinking');
                      if (hasThinking) {
                        return prev.map(p => p.type === 'thinking' ? parsed.part : p);
                      }
                      return [...prev, parsed.part];
                    });
                  } else {
                    setParts(prev => [...prev, parsed.part]);
                  }
                  break;

                case 'tool_call_start':
                  setParts(prev => [...prev, {
                    type: 'tool_call',
                    id: parsed.toolCallId,
                    toolName: parsed.toolName,
                    status: 'running',
                    args: parsed.args,
                  }]);
                  break;

                case 'tool_call_progress':
                  setParts(prev => prev.map(p =>
                    p.type === 'tool_call' && (p as { id: string }).id === parsed.toolCallId
                      ? { ...p, steps: [...((p as { steps?: unknown[] }).steps || []), parsed.step] }
                      : p
                  ));
                  break;

                case 'tool_call_complete':
                  setParts(prev => prev.map(p =>
                    p.type === 'tool_call' && (p as { id: string }).id === parsed.toolCallId
                      ? {
                          ...p,
                          status: parsed.status,
                          result: parsed.result,
                          error: parsed.error,
                          durationMs: parsed.durationMs,
                        }
                      : p
                  ));
                  break;

                case 'question':
                  setParts(prev => [...prev, { ...parsed.question, type: 'question' }]);
                  break;

                case 'approval':
                  setParts(prev => [...prev, { ...parsed.approval, type: 'approval' }]);
                  break;

                case 'error':
                  setError(parsed.message);
                  break;

                case 'done':
                  // Finalize any remaining text
                  if (accumulatedText) {
                    setParts(prev => {
                      const lastPart = prev[prev.length - 1];
                      if (lastPart?.type === 'text') {
                        return [
                          ...prev.slice(0, -1),
                          { ...lastPart, content: accumulatedText, isStreaming: false }
                        ];
                      }
                      return prev;
                    });
                  }
                  break;
              }
            } catch {
              // Ignore parse errors
            }
          }
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') return;
      setError(err instanceof Error ? err.message : 'Request failed');
    } finally {
      setIsStreaming(false);
    }
  }, [isStreaming, runThinkingDemo, runAccordionDemo, runSkeletonDemo, runArtifactDemo]);

  const reset = useCallback(() => {
    abortControllerRef.current?.abort();
    setParts([]);
    setError(null);
    setActiveCommand(null);
    setIsStreaming(false);
    setToolCallMeta(new Map());
    setSources([]);
    setArtifactModal(null);
  }, []);

  // Auto-scroll to bottom
  useEffect(() => {
    if (containerRef.current && isStreaming) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [parts, isStreaming]);

  if (isEnabled === null) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <ChatLoadingIndicator message="Checking UI test status..." />
      </div>
    );
  }

  if (!isEnabled) {
    return (
      <div className="flex items-center justify-center min-h-screen p-8">
        <div className="text-center max-w-md">
          <Warning className="h-16 w-16 text-amber-500 mx-auto mb-4" weight="fill" />
          <h1 className="text-2xl font-semibold mb-2">UI Testing Disabled</h1>
          <p className="text-muted-foreground mb-4">
            The UI testing agent is not enabled. Set <code className="bg-muted px-1 rounded">DEBUG=true</code> or{' '}
            <code className="bg-muted px-1 rounded">ENABLE_UI_TESTING_AGENT=true</code> in your backend configuration.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-[calc(100vh-4rem)]">
      {/* Sidebar with commands */}
      <div className="w-72 border-r bg-muted/30 flex flex-col">
        <div className="p-4 border-b">
          <h1 className="text-lg font-semibold flex items-center gap-2">
            <List className="h-5 w-5" />
            UI Component Tester
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Test and showcase AI chat components
          </p>
        </div>

        <div className="flex-1 overflow-auto p-2">
          <div className="space-y-1">
            {TEST_COMMANDS.map((cmd) => (
              <button
                key={cmd.id}
                onClick={() => runCommand(cmd.command)}
                disabled={isStreaming && activeCommand !== cmd.command}
                className={cn(
                  'w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left text-sm',
                  'transition-colors duration-150',
                  activeCommand === cmd.command
                    ? 'bg-primary text-primary-foreground'
                    : 'hover:bg-muted',
                  isStreaming && activeCommand !== cmd.command && 'opacity-50 cursor-not-allowed'
                )}
              >
                <cmd.icon
                  className={cn(
                    'h-4 w-4 shrink-0',
                    activeCommand === cmd.command && isStreaming && 'animate-pulse'
                  )}
                  weight={activeCommand === cmd.command ? 'fill' : 'regular'}
                />
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{cmd.label}</div>
                  <div className={cn(
                    'text-xs truncate',
                    activeCommand === cmd.command
                      ? 'text-primary-foreground/80'
                      : 'text-muted-foreground'
                  )}>
                    {cmd.description}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="p-3 border-t space-y-2">
          <button
            onClick={reset}
            className={cn(
              'w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg',
              'bg-muted hover:bg-muted/80 transition-colors text-sm font-medium'
            )}
          >
            <ArrowClockwise className="h-4 w-4" />
            Reset
          </button>
        </div>
      </div>

      {/* Main content area */}
      <div className="flex-1 flex flex-col">
        <div className="border-b px-6 py-3 flex items-center justify-between bg-background/50">
          <div>
            {activeCommand ? (
              <div className="flex items-center gap-2">
                <code className="text-sm bg-muted px-2 py-0.5 rounded">{activeCommand}</code>
                {isStreaming && (
                  <span className="text-xs text-muted-foreground animate-pulse">
                    Streaming...
                  </span>
                )}
              </div>
            ) : (
              <span className="text-muted-foreground text-sm">
                Select a command to test
              </span>
            )}
          </div>
          {parts.length > 0 && (
            <div className="text-xs text-muted-foreground">
              {parts.length} part{parts.length !== 1 ? 's' : ''} rendered
            </div>
          )}
        </div>

        <div
          ref={containerRef}
          className="flex-1 overflow-auto p-6"
        >
          <ChatErrorBoundary>
            {error && (
              <div className="mb-4 p-4 rounded-lg bg-destructive/10 border border-destructive/30 text-destructive">
                <div className="font-medium">Error</div>
                <div className="text-sm mt-1">{error}</div>
              </div>
            )}

            {parts.length === 0 && !isStreaming && !error && activeCommand !== 'show skeleton' && (
              <ChatEmptyState
                title="UI Component Tester"
                description="Select a command from the sidebar to test a component"
              />
            )}

            {/* Skeleton demo: static display of shimmer components */}
            {activeCommand === 'show skeleton' && parts.length === 0 && (
              <div className="space-y-6 max-w-lg">
                <h3 className="text-sm font-medium text-muted-foreground mb-2">ArtifactCreationSkeleton — with filename</h3>
                <ArtifactCreationSkeleton
                  filename="ESG_Analysis_Report.md"
                  toolName="save_analysis_report"
                />
                <h3 className="text-sm font-medium text-muted-foreground mb-2 mt-6">ArtifactCreationSkeleton — without filename</h3>
                <ArtifactCreationSkeleton
                  toolName="execute_python"
                />
                <h3 className="text-sm font-medium text-muted-foreground mb-2 mt-6">ArtifactCreationSkeleton — minimal</h3>
                <ArtifactCreationSkeleton />
              </div>
            )}

            {parts.length > 0 && (
              <ChainOfThought
                parts={parts}
                compact={false}
                animated={isStreaming}
                toolCallMeta={toolCallMeta.size > 0 ? toolCallMeta : undefined}
                sources={sources.length > 0 ? sources : undefined}
                handlers={{
                  onQuestionAnswer: (id, answer) => {
                    console.log('Question answered:', id, answer);
                  },
                  onApprove: (id) => {
                    console.log('Approved:', id);
                    setParts(prev => prev.map(p =>
                      p.type === 'approval' && (p as { id: string }).id === id
                        ? { ...p, status: 'approved' }
                        : p
                    ));
                  },
                  onReject: (id) => {
                    console.log('Rejected:', id);
                    setParts(prev => prev.map(p =>
                      p.type === 'approval' && (p as { id: string }).id === id
                        ? { ...p, status: 'rejected' }
                        : p
                    ));
                  },
                  onOpenModal: handleOpenModal,
                }}
              />
            )}

            {isStreaming && (
              <div className="mt-4">
                <ChatLoadingIndicator message="Loading..." compact />
              </div>
            )}
          </ChatErrorBoundary>
        </div>
      </div>

      {/* Artifact modal overlay */}
      {artifactModal?.open && (
        <ArtifactModal
          open={artifactModal.open}
          onOpenChange={(open) => setArtifactModal(open ? artifactModal : null)}
          filename={artifactModal.filename}
          content={artifactModal.content}
          fileType={artifactModal.fileType}
          downloadUrl={artifactModal.downloadUrl}
        />
      )}
    </div>
  );
}
