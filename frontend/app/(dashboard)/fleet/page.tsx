'use client';

import { useState } from 'react';
import { FleetRunList } from '@/components/fleet/FleetRunList';
import { FleetChat } from '@/components/fleet/FleetChat';
import { Broadcast } from '@phosphor-icons/react';

/**
 * Fleet page — split-panel inbox with chat-style run detail.
 * Left: filterable run list (Running / Needs Attention / Done / All)
 * Right: chat interface showing agent work + operator intervention
 */
export default function FleetPage() {
  const [selectedRun, setSelectedRun] = useState<{
    id: string;
    agent_name: string | null;
    status: string;
  } | null>(null);

  return (
    <div className="h-full flex">
      {/* Left panel — run list */}
      <div className="w-80 border-r border-border shrink-0 h-full overflow-hidden">
        <FleetRunList
          selectedId={selectedRun?.id ?? null}
          onSelect={(run) => setSelectedRun({
            id: run.id,
            agent_name: run.agent_name,
            status: run.status,
          })}
        />
      </div>

      {/* Right panel — chat interface */}
      <div className="flex-1 min-w-0 h-full overflow-hidden">
        {selectedRun ? (
          <FleetChat
            key={selectedRun.id}
            runId={selectedRun.id}
            agentName={selectedRun.agent_name || 'Unknown Agent'}
            status={selectedRun.status}
          />
        ) : (
          <div className="h-full flex items-center justify-center text-muted-foreground">
            <div className="text-center">
              <Broadcast className="h-10 w-10 mx-auto mb-3 opacity-30" />
              <p className="text-sm">Select a run to watch the agent work</p>
              <p className="text-xs mt-1 opacity-60">Running agents show live updates with intervention</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
