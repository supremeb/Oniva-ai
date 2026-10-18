'use client';

import { Suspense, useCallback, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { FleetRunList } from '@/components/fleet/FleetRunList';
import { FleetChat } from '@/components/fleet/FleetChat';
import { useFleetRun } from '@/hooks/useFleetRuns';
import type { FleetRunItem } from '@/lib/models';
import { Broadcast } from '@phosphor-icons/react';

interface SelectedRun {
  id: string;
  agent_name: string | null;
  status: string;
  conversation_id: string | null;
}

function toSelected(run: FleetRunItem): SelectedRun {
  return {
    id: run.id,
    agent_name: run.agent_name,
    status: run.status,
    // An engine job carries its conversation; a legacy job has none (trace path).
    conversation_id: run.engine === false ? null : (run.conversation_id ?? null),
  };
}

/**
 * Fleet page — split-panel inbox with chat-style run detail.
 * Left: filterable run list (Running / Needs Attention / Done / All)
 * Right: chat interface showing agent work + operator intervention
 *
 * The selected run lives in the URL (`/fleet?run=<jobId>`) so a reload rejoins it.
 */
function FleetPageInner() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const runParam = searchParams.get('run');

  // Run picked from the list this session: shown at once; the live row below takes over for the conversation id
  // (a job picked while pending has none yet) and for runs restored from the URL.
  const [picked, setPicked] = useState<SelectedRun | null>(null);
  const knownPicked = picked && picked.id === runParam ? picked : null;

  const { data: liveRun, isLoading } = useFleetRun(runParam);
  const selectedRun: SelectedRun | null = liveRun ? toSelected(liveRun) : knownPicked;
  const restoring = !!runParam && !selectedRun && isLoading;
  const notFound = !!runParam && !selectedRun && !isLoading;

  const handleSelect = useCallback(
    (run: FleetRunItem) => {
      setPicked(toSelected(run));
      router.replace(`${pathname}?run=${encodeURIComponent(run.id)}`, { scroll: false });
    },
    [router, pathname],
  );

  return (
    <div className="h-full flex">
      {/* Left panel — run list */}
      <div className="w-80 border-r border-border shrink-0 h-full overflow-hidden">
        <FleetRunList selectedId={runParam} onSelect={handleSelect} />
      </div>

      {/* Right panel — chat interface */}
      <div className="flex-1 min-w-0 h-full overflow-hidden">
        {selectedRun ? (
          <FleetChat
            key={`${selectedRun.id}:${selectedRun.conversation_id ?? ''}`}
            runId={selectedRun.id}
            agentName={selectedRun.agent_name || 'Unknown Agent'}
            status={selectedRun.status}
            conversationId={selectedRun.conversation_id}
          />
        ) : restoring ? (
          <div className="h-full flex items-center justify-center text-muted-foreground text-sm">Loading...</div>
        ) : notFound ? (
          <div className="h-full flex items-center justify-center text-muted-foreground">
            <div className="text-center">
              <Broadcast className="h-10 w-10 mx-auto mb-3 opacity-30" />
              <p className="text-sm">Run not found</p>
              <p className="text-xs mt-1 opacity-60">It may have been removed, or it is not one of your team&apos;s recent runs</p>
            </div>
          </div>
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

export default function FleetPage() {
  return (
    <Suspense fallback={null}>
      <FleetPageInner />
    </Suspense>
  );
}
