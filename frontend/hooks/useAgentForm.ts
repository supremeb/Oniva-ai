'use client';

import { useReducer, useCallback, useEffect } from 'react';
import type { CustomAgent, DeepWorkModels } from '@/lib/api/agents';

/**
 * Agent form state
 */
export interface AgentFormState {
  name: string;
  description: string;
  systemPrompt: string;
  instructionFormat: 'plain_text' | 'tiptap_json';
  model: string;
  temperature: number;
  maxTokens: number;
  selectedSkills: string[];
  selectedConnectors: string[];
  deepWorkModels: DeepWorkModels;
  knowledgeBaseIds: string[];
  workflowIds: string[];
  // Orchestration fields
  isSupervisor: boolean;
  childAgentConfig: Array<{ agent_id: string; task: string; order: number }>;
  executionMode: 'sequential' | 'parallel';
  resourceConstraints: { max_concurrent?: number; token_budget?: number; timeout_seconds?: number; max_depth?: number };
}

/**
 * Form action types
 */
type FormAction =
  | { type: 'SET_FIELD'; field: keyof AgentFormState; value: AgentFormState[keyof AgentFormState] }
  | { type: 'TOGGLE_SKILL'; skill: string }
  | { type: 'TOGGLE_CONNECTOR'; connector: string }
  | { type: 'TOGGLE_KNOWLEDGE_BASE'; kbId: string }
  | { type: 'TOGGLE_WORKFLOW'; workflowId: string }
  | { type: 'SET_DEEP_WORK_MODELS'; models: DeepWorkModels }
  | { type: 'RESET'; initial: AgentFormState }
  | { type: 'LOAD_AGENT'; agent: CustomAgent };

/**
 * Default form state
 */
const DEFAULT_STATE: AgentFormState = {
  name: '',
  description: '',
  systemPrompt: '',
  instructionFormat: 'plain_text',
  model: 'gpt-4o',
  temperature: 0.7,
  maxTokens: 2000,
  selectedSkills: [],
  selectedConnectors: [],
  deepWorkModels: {},
  knowledgeBaseIds: [],
  workflowIds: [],
  isSupervisor: false,
  childAgentConfig: [],
  executionMode: 'sequential',
  resourceConstraints: {},
};

/**
 * Form reducer
 */
function formReducer(state: AgentFormState, action: FormAction): AgentFormState {
  switch (action.type) {
    case 'SET_FIELD':
      return { ...state, [action.field]: action.value };

    case 'TOGGLE_SKILL':
      return {
        ...state,
        selectedSkills: state.selectedSkills.includes(action.skill)
          ? state.selectedSkills.filter((s) => s !== action.skill)
          : [...state.selectedSkills, action.skill],
      };

    case 'TOGGLE_CONNECTOR':
      return {
        ...state,
        selectedConnectors: state.selectedConnectors.includes(action.connector)
          ? state.selectedConnectors.filter((c) => c !== action.connector)
          : [...state.selectedConnectors, action.connector],
      };

    case 'TOGGLE_KNOWLEDGE_BASE':
      return {
        ...state,
        knowledgeBaseIds: state.knowledgeBaseIds.includes(action.kbId)
          ? state.knowledgeBaseIds.filter((id) => id !== action.kbId)
          : [...state.knowledgeBaseIds, action.kbId],
      };

    case 'TOGGLE_WORKFLOW':
      return {
        ...state,
        workflowIds: state.workflowIds.includes(action.workflowId)
          ? state.workflowIds.filter((id) => id !== action.workflowId)
          : [...state.workflowIds, action.workflowId],
      };

    case 'SET_DEEP_WORK_MODELS':
      return { ...state, deepWorkModels: action.models };

    case 'RESET':
      return action.initial;

    case 'LOAD_AGENT':
      return {
        name: action.agent.name,
        description: action.agent.description || '',
        systemPrompt: action.agent.systemPrompt || '',
        instructionFormat: (action.agent as CustomAgent & { instructionFormat?: string }).instructionFormat === 'tiptap_json' ? 'tiptap_json' : 'plain_text',
        model: action.agent.model || 'gpt-4o',
        temperature: action.agent.temperature ?? 0.7,
        maxTokens: action.agent.maxOutputTokens ?? 2000,
        selectedSkills: action.agent.skills || [],
        selectedConnectors: action.agent.connectors || [],
        deepWorkModels: action.agent.deepWorkModels || {},
        knowledgeBaseIds: action.agent.knowledgeBaseIds ?? [],
        workflowIds: action.agent.workflowIds ?? [],
        isSupervisor: action.agent.isSupervisor || false,
        childAgentConfig: action.agent.childAgentConfig || [],
        executionMode: action.agent.executionMode || 'sequential',
        resourceConstraints: action.agent.resourceConstraints || {},
      };

    default:
      return state;
  }
}

/**
 * Set-based array equality (order-independent)
 */
function arraysEqual(a: string[], b: string[]): boolean {
  const setA = new Set(a);
  const setB = new Set(b);
  return setA.size === setB.size && [...setA].every(x => setB.has(x));
}

/**
 * Compare form state with original agent to detect changes
 */
function hasFormChanges(state: AgentFormState, agent: CustomAgent | null): boolean {
  if (!agent) return false;

  return (
    state.name !== agent.name ||
    state.description !== (agent.description || '') ||
    state.systemPrompt !== (agent.systemPrompt || '') ||
    state.model !== (agent.model || 'gpt-4o') ||
    state.temperature !== (agent.temperature ?? 0.7) ||
    state.maxTokens !== (agent.maxOutputTokens ?? 2000) ||
    !arraysEqual(state.selectedSkills, agent.skills || []) ||
    !arraysEqual(state.selectedConnectors, agent.connectors || []) ||
    !arraysEqual(state.knowledgeBaseIds, agent.knowledgeBaseIds ?? []) ||
    !arraysEqual(state.workflowIds, agent.workflowIds ?? []) ||
    JSON.stringify(state.deepWorkModels) !== JSON.stringify(agent.deepWorkModels || {}) ||
    state.isSupervisor !== (agent.isSupervisor || false) ||
    state.executionMode !== (agent.executionMode || 'sequential') ||
    JSON.stringify(state.childAgentConfig) !== JSON.stringify(agent.childAgentConfig || []) ||
    JSON.stringify(state.resourceConstraints) !== JSON.stringify(agent.resourceConstraints || {})
  );
}

/**
 * Hook for managing agent form state with useReducer
 */
export function useAgentForm(agent: CustomAgent | null) {
  const [state, dispatch] = useReducer(formReducer, DEFAULT_STATE);

  // Load agent data when it changes
  useEffect(() => {
    if (agent) {
      dispatch({ type: 'LOAD_AGENT', agent });
    }
  }, [agent]);

  // Field setters
  const setName = useCallback((value: string) => {
    dispatch({ type: 'SET_FIELD', field: 'name', value });
  }, []);

  const setDescription = useCallback((value: string) => {
    dispatch({ type: 'SET_FIELD', field: 'description', value });
  }, []);

  const setSystemPrompt = useCallback((value: string) => {
    dispatch({ type: 'SET_FIELD', field: 'systemPrompt', value });
  }, []);

  const setInstructionFormat = useCallback((value: 'plain_text' | 'tiptap_json') => {
    dispatch({ type: 'SET_FIELD', field: 'instructionFormat', value });
  }, []);

  const setModel = useCallback((value: string) => {
    dispatch({ type: 'SET_FIELD', field: 'model', value });
  }, []);

  const setTemperature = useCallback((value: number) => {
    dispatch({ type: 'SET_FIELD', field: 'temperature', value });
  }, []);

  const setMaxTokens = useCallback((value: number) => {
    dispatch({ type: 'SET_FIELD', field: 'maxTokens', value });
  }, []);

  const toggleSkill = useCallback((skill: string) => {
    dispatch({ type: 'TOGGLE_SKILL', skill });
  }, []);

  const toggleConnector = useCallback((connector: string) => {
    dispatch({ type: 'TOGGLE_CONNECTOR', connector });
  }, []);

  const toggleKnowledgeBase = useCallback((kbId: string) => {
    dispatch({ type: 'TOGGLE_KNOWLEDGE_BASE', kbId });
  }, []);

  const toggleWorkflow = useCallback((workflowId: string) => {
    dispatch({ type: 'TOGGLE_WORKFLOW', workflowId });
  }, []);

  const setDeepWorkModels = useCallback((models: DeepWorkModels) => {
    dispatch({ type: 'SET_DEEP_WORK_MODELS', models });
  }, []);

  const setIsSupervisor = useCallback((value: boolean) => {
    dispatch({ type: 'SET_FIELD', field: 'isSupervisor', value });
  }, []);

  const setChildAgentConfig = useCallback((value: AgentFormState['childAgentConfig']) => {
    dispatch({ type: 'SET_FIELD', field: 'childAgentConfig', value });
  }, []);

  const setExecutionMode = useCallback((value: 'sequential' | 'parallel') => {
    dispatch({ type: 'SET_FIELD', field: 'executionMode', value });
  }, []);

  const setResourceConstraints = useCallback((value: AgentFormState['resourceConstraints']) => {
    dispatch({ type: 'SET_FIELD', field: 'resourceConstraints', value });
  }, []);

  const reset = useCallback(() => {
    if (agent) {
      dispatch({ type: 'LOAD_AGENT', agent });
    } else {
      dispatch({ type: 'RESET', initial: DEFAULT_STATE });
    }
  }, [agent]);

  // Get form updates for saving
  const getUpdates = useCallback(() => ({
    name: state.name,
    description: state.description,
    systemPrompt: state.systemPrompt,
    instructionFormat: state.instructionFormat,
    model: state.model,
    temperature: state.temperature,
    maxOutputTokens: state.maxTokens,
    skills: state.selectedSkills,
    connectors: state.selectedConnectors,
    deepWorkModels: state.deepWorkModels,
    knowledgeBaseIds: state.knowledgeBaseIds,
    workflowIds: state.workflowIds,
    isSupervisor: state.isSupervisor,
    childAgentConfig: state.childAgentConfig,
    executionMode: state.executionMode,
    resourceConstraints: state.resourceConstraints,
  }), [state]);

  // Check for unsaved changes
  const hasChanges = hasFormChanges(state, agent);

  return {
    // State values
    ...state,

    // Derived state
    hasChanges,

    // Actions
    setName,
    setDescription,
    setSystemPrompt,
    setInstructionFormat,
    setModel,
    setTemperature,
    setMaxTokens,
    toggleSkill,
    toggleConnector,
    toggleKnowledgeBase,
    toggleWorkflow,
    setDeepWorkModels,
    setIsSupervisor,
    setChildAgentConfig,
    setExecutionMode,
    setResourceConstraints,
    reset,
    getUpdates,
  };
}
