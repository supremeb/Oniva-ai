'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Gauge, CaretDown, Lightning, Robot, MagnifyingGlass } from '@phosphor-icons/react';
import { OperationsTab } from '@/components/observability/OperationsTab';
import { AgentHealthTab } from '@/components/observability/AgentHealthTab';
import { TracingTab } from '@/components/observability/TracingTab';
import { cn } from '@/lib/utils';

type ObservabilityTab = 'operations' | 'agents' | 'traces';

const WINDOW_OPTIONS = [
  { label: 'Last 15 minutes', minutes: 15 },
  { label: 'Last 1 hour', minutes: 60 },
  { label: 'Last 6 hours', minutes: 360 },
  { label: 'Last 24 hours', minutes: 1440 },
];

export default function ObservabilityPage() {
  const [selectedWindow, setSelectedWindow] = useState(WINDOW_OPTIONS[1]);
  const [activeTab, setActiveTab] = useState<ObservabilityTab>('operations');

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-page-title flex items-center gap-2">
            <Gauge className="h-6 w-6" />
            Observability
          </h1>
          <p className="text-page-description mt-1">
            Monitor LLM performance, agent health, and execution traces
          </p>
        </div>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="gap-2">
              {selectedWindow.label}
              <CaretDown className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {WINDOW_OPTIONS.map((opt) => (
              <DropdownMenuItem
                key={opt.minutes}
                onClick={() => setSelectedWindow(opt)}
                className={cn(
                  selectedWindow.minutes === opt.minutes && 'bg-accent'
                )}
              >
                {opt.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border">
        <button
          onClick={() => setActiveTab('operations')}
          className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
            activeTab === 'operations'
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          <Lightning size={18} />
          Operations
        </button>
        <button
          onClick={() => setActiveTab('agents')}
          className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
            activeTab === 'agents'
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          <Robot size={18} />
          Agent Health
        </button>
        <button
          onClick={() => setActiveTab('traces')}
          className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
            activeTab === 'traces'
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          <MagnifyingGlass size={18} />
          Traces
        </button>
      </div>

      {/* Tab Content */}
      {activeTab === 'operations' && (
        <OperationsTab windowMinutes={selectedWindow.minutes} />
      )}
      {activeTab === 'agents' && (
        <AgentHealthTab />
      )}
      {activeTab === 'traces' && (
        <TracingTab />
      )}
    </div>
  );
}
