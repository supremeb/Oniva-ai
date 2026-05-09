'use client';

import { useState, useRef, useCallback, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Input } from '@/components/ui/input';
import {
  ArrowLeft,
  PaperPlaneTilt,
  CircleNotch,
  Sparkle,
  ArrowRight,
  ChatText,
} from '@phosphor-icons/react';
import { toast } from 'sonner';
import {
  generateAgent,
  clarifyAgent,
  type ClarifyQuestion,
  type SuggestedSkill,
  type SuggestedConnector,
} from '@/lib/api/agent-generation';
import { createNewAgentAsync } from '@/lib/api/agents';
import { useQueryClient } from '@tanstack/react-query';
import { cn } from '@/lib/utils';

type Phase = 'input' | 'clarifying' | 'generating';

export default function CreateAgentPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const abortRef = useRef<(() => void) | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // beforeunload handler (stable reference so we can remove it)
  const beforeUnloadHandler = useCallback((e: BeforeUnloadEvent) => {
    e.preventDefault();
  }, []);

  // Cleanup timeout + beforeunload guard
  const cleanupGeneration = useCallback(() => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    window.removeEventListener('beforeunload', beforeUnloadHandler);
  }, [beforeUnloadHandler]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      cleanupGeneration();
      abortRef.current?.();
    };
  }, [cleanupGeneration]);

  // Phase
  const [phase, setPhase] = useState<Phase>('input');

  // Input
  const [description, setDescription] = useState('');

  // Clarifying questions state
  const [questions, setQuestions] = useState<ClarifyQuestion[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [isClarifying, setIsClarifying] = useState(false);

  // Generation progress
  const [generationStatus, setGenerationStatus] = useState('');
  const [error, setError] = useState<string | null>(null);

  // Accumulated config (used for auto-save)
  const configRef = useRef<{
    name: string;
    description: string;
    instructions: string;
    model: string;
    temperature: number;
    maxTokens: number;
    skills: string[];
    connectors: string[];
    icon: string;
  }>({
    name: '',
    description: '',
    instructions: '',
    model: 'openai/gpt-4o',
    temperature: 0.7,
    maxTokens: 2000,
    skills: [],
    connectors: [],
    icon: 'Robot',
  });
  const instructionsRef = useRef('');

  // Build clarification context from answers
  const buildClarificationContext = useCallback((): Array<{ question: string; answer: string }> => {
    return questions
      .filter((q) => answers[q.id])
      .map((q) => ({ question: q.text, answer: answers[q.id] }));
  }, [questions, answers]);

  // Auto-save and redirect
  const autoSaveAndRedirect = useCallback(async () => {
    const cfg = configRef.current;
    setGenerationStatus('Saving agent...');
    try {
      const agent = await createNewAgentAsync(cfg.name, cfg.description, {
        icon: cfg.icon,
        systemPrompt: cfg.instructions,
        model: cfg.model,
        temperature: cfg.temperature,
        maxOutputTokens: cfg.maxTokens,
        skills: cfg.skills,
        connectors: cfg.connectors,
      });
      queryClient.invalidateQueries({ queryKey: ['customAgents'] });
      toast.success('Agent created — opening studio');
      router.push(`/agents/${agent.id}`);
    } catch (err) {
      toast.error(`Failed to save: ${err instanceof Error ? err.message : 'Unknown error'}`);
      setPhase('input');
    }
  }, [queryClient, router]);

  // Step 1: Generate → auto-save → redirect
  const handleGenerate = useCallback((clarificationContext?: Array<{ question: string; answer: string }>) => {
    setPhase('generating');
    setError(null);
    setGenerationStatus('Generating agent name...');
    instructionsRef.current = '';

    // Guard: warn user before closing tab during generation
    window.addEventListener('beforeunload', beforeUnloadHandler);

    // Timeout: abort generation after 120 seconds
    timeoutRef.current = setTimeout(() => {
      abortRef.current?.();
      cleanupGeneration();
      setError('Generation timed out. Try again or create manually.');
      setPhase('input');
      toast.error('Generation timed out. Try again or create manually.');
    }, 120_000);

    const abort = generateAgent(description, {
      onAgentName: (n) => {
        configRef.current.name = n;
        setGenerationStatus('Writing instructions...');
      },
      onAgentDescription: (d) => {
        configRef.current.description = d;
      },
      onInstructionChunk: (delta) => {
        instructionsRef.current += delta;
        configRef.current.instructions = instructionsRef.current;
      },
      onInstructionComplete: () => {
        setGenerationStatus('Selecting skills and model...');
      },
      onSkillsSuggested: (s) => {
        configRef.current.skills = s.map((sk) => sk.id);
      },
      onConnectorsSuggested: (c) => {
        configRef.current.connectors = c.map((cn) => cn.id);
      },
      onModelRecommended: (data) => {
        configRef.current.model = data.model;
        configRef.current.temperature = data.temperature;
        configRef.current.maxTokens = data.max_tokens;
      },
      onGenerationComplete: (config) => {
        cleanupGeneration();
        // Use the complete config as the source of truth
        // (individual SSE events may have been missed)
        configRef.current = {
          name: config.name || configRef.current.name,
          description: config.description || configRef.current.description,
          instructions: config.system_prompt || configRef.current.instructions,
          model: config.model || configRef.current.model,
          temperature: config.temperature ?? configRef.current.temperature,
          maxTokens: config.max_tokens ?? configRef.current.maxTokens,
          skills: config.skills?.length ? config.skills : configRef.current.skills,
          connectors: config.connectors?.length ? config.connectors : configRef.current.connectors,
          icon: config.icon || configRef.current.icon || 'Robot',
        };
        autoSaveAndRedirect();
      },
      onError: (msg) => {
        cleanupGeneration();
        setError(msg);
        setPhase('input');
        toast.error(`Generation failed: ${msg}`);
      },
    }, clarificationContext);

    abortRef.current = abort;
  }, [description, autoSaveAndRedirect, beforeUnloadHandler, cleanupGeneration]);

  // Static fallback questions when LLM clarify is unavailable
  const STATIC_QUESTIONS: ClarifyQuestion[] = [
    { id: 'audience', text: 'Who will primarily interact with this agent?', type: 'multiple_choice', options: ['Internal team members', 'External customers', 'Both internal and external'] },
    { id: 'tone', text: 'What communication style should the agent use?', type: 'multiple_choice', options: ['Professional & formal', 'Friendly & conversational', 'Technical & precise'] },
    { id: 'output', text: 'What kind of output should the agent produce?', type: 'multiple_choice', options: ['Short answers & summaries', 'Detailed reports & analysis', 'Structured data & tables'] },
  ];

  // Step 2: Ask for clarifying questions (static fallback if LLM unavailable)
  const handleSubmitDescription = useCallback(async () => {
    if (description.trim().length < 20) {
      toast.error('Please provide at least 20 characters describing your agent.');
      return;
    }

    setIsClarifying(true);
    setError(null);

    try {
      const result = await clarifyAgent(description);

      if (result.should_generate || result.questions.length === 0) {
        // LLM says description is detailed enough — still show static questions
        setQuestions(STATIC_QUESTIONS);
        setAnswers({});
        setPhase('clarifying');
        return;
      }

      setQuestions(result.questions);
      setAnswers({});
      setPhase('clarifying');
    } catch {
      // LLM unavailable — show static fallback questions
      setQuestions(STATIC_QUESTIONS);
      setAnswers({});
      setPhase('clarifying');
    } finally {
      setIsClarifying(false);
    }
  }, [description]);

  // Generate with answers
  const handleGenerateWithAnswers = useCallback(() => {
    const context = buildClarificationContext();
    handleGenerate(context.length > 0 ? context : undefined);
  }, [buildClarificationContext, handleGenerate]);

  const isInputValid = description.trim().length >= 20;
  const hasAnyAnswers = Object.values(answers).some((a) => a.trim());

  // ── Input Phase ──────────────────────────────────────────────────────────
  if (phase === 'input') {
    return (
      <div className="flex flex-col items-center justify-center min-h-[calc(100vh-56px)] px-4">
        <div className="w-full max-w-2xl space-y-6">
          <button
            onClick={() => router.push('/agents')}
            className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to Agents
          </button>

          <div className="text-center space-y-2">
            <div className="w-12 h-12 rounded-xl bg-primary/10 flex items-center justify-center mx-auto">
              <Sparkle className="h-6 w-6 text-primary" weight="fill" />
            </div>
            <h1 className="text-2xl font-semibold tracking-tight">Create an Agent</h1>
            <p className="text-sm text-muted-foreground">
              Describe what you want this agent to do, and AI will build it for you.
            </p>
          </div>

          <div className="space-y-2">
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Describe what this agent should do..."
              rows={6}
              className="resize-none text-base"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && isInputValid && !isClarifying) {
                  e.preventDefault();
                  handleSubmitDescription();
                }
              }}
              autoFocus
            />
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">
                {description.length}/5,000 characters (min 20)
              </span>
              <Button
                onClick={handleSubmitDescription}
                disabled={!isInputValid || isClarifying}
                size="sm"
              >
                {isClarifying ? (
                  <CircleNotch className="h-4 w-4 mr-1.5 animate-spin" />
                ) : (
                  <PaperPlaneTilt className="h-4 w-4 mr-1.5" weight="fill" />
                )}
                {isClarifying ? 'Analyzing...' : 'Generate Agent'}
              </Button>
            </div>
          </div>

          <p className="text-center text-xs text-muted-foreground">
            or{' '}
            <button
              onClick={() => router.push('/agents')}
              className="underline underline-offset-2 hover:text-foreground transition-colors"
            >
              create manually
            </button>
          </p>

          {error && (
            <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
              {error}
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── Clarifying Phase ─────────────────────────────────────────────────────
  if (phase === 'clarifying') {
    return (
      <div className="flex flex-col items-center justify-center min-h-[calc(100vh-56px)] px-4">
        <div className="w-full max-w-2xl space-y-6">
          <button
            onClick={() => setPhase('input')}
            className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to description
          </button>

          <div className="text-center space-y-2">
            <div className="w-12 h-12 rounded-xl bg-primary/10 flex items-center justify-center mx-auto">
              <ChatText className="h-6 w-6 text-primary" weight="fill" />
            </div>
            <h1 className="text-2xl font-semibold tracking-tight">A few quick questions</h1>
            <p className="text-sm text-muted-foreground">
              Help us build a better agent by answering these questions.
            </p>
          </div>

          <div className="rounded-lg bg-muted/50 px-4 py-3">
            <p className="text-sm text-muted-foreground line-clamp-2">{description}</p>
          </div>

          <div className="space-y-4">
            {questions.map((q) => (
              <div key={q.id} className="rounded-lg border bg-card p-4 space-y-3">
                <p className="text-sm font-medium">{q.text}</p>

                {q.type === 'multiple_choice' && q.options ? (
                  <div className="flex flex-wrap gap-2">
                    {q.options.map((option) => (
                      <button
                        key={option}
                        onClick={() =>
                          setAnswers((prev) => ({ ...prev, [q.id]: option }))
                        }
                        className={cn(
                          'px-3 py-1.5 rounded-full text-sm border transition-colors',
                          answers[q.id] === option
                            ? 'bg-primary text-primary-foreground border-primary'
                            : 'bg-background hover:bg-accent border-border'
                        )}
                      >
                        {option}
                      </button>
                    ))}
                  </div>
                ) : (
                  <Input
                    value={answers[q.id] || ''}
                    onChange={(e) =>
                      setAnswers((prev) => ({ ...prev, [q.id]: e.target.value }))
                    }
                    placeholder="Type your answer..."
                    className="text-sm"
                  />
                )}
              </div>
            ))}
          </div>

          <div className="flex items-center justify-between">
            <button
              onClick={() => handleGenerate()}
              className="text-sm text-muted-foreground hover:text-foreground underline underline-offset-2 transition-colors"
            >
              Skip, just generate
            </button>
            <Button
              onClick={handleGenerateWithAnswers}
              size="sm"
            >
              <ArrowRight className="h-4 w-4 mr-1.5" />
              {hasAnyAnswers ? 'Generate Agent' : 'Skip & Generate'}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  // ── Generating Phase (progress → auto-save → redirect) ──────────────────
  return (
    <div className="flex flex-col items-center justify-center min-h-[calc(100vh-56px)] px-4">
      <div className="w-full max-w-md text-center space-y-6">
        <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center mx-auto">
          <CircleNotch className="h-8 w-8 animate-spin text-primary" />
        </div>
        <div className="space-y-2">
          <h1 className="text-xl font-semibold tracking-tight">Building your agent</h1>
          <p className="text-sm text-muted-foreground">{generationStatus}</p>
        </div>
        <button
          onClick={() => {
            abortRef.current?.();
            cleanupGeneration();
            setPhase('input');
          }}
          className="text-sm text-muted-foreground hover:text-foreground underline underline-offset-2 transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
