import { apiFetch, APIError } from './index';
import { useQuery } from '@tanstack/react-query';
import { BUILTIN_AGENT_UUIDS, isBuiltinAgentId, uuidToSlug } from '@/lib/builtin-agents';


/**
 * Agent types
 */
export interface AgentInfo {
  id: string;
  name: string;
  type: string;
  description: string;
  status: string;
}

export interface DeepWorkModels {
  high?: string;
  mid?: string;
  low?: string;
}

export interface CustomAgent {
  id: string;
  name: string;
  description: string;
  icon: string;
  type: string;
  systemPrompt?: string;
  instructionFormat?: 'plain_text' | 'tiptap_json';
  model?: string;
  temperature?: number;
  maxOutputTokens?: number;
  skills?: string[];
  connectors?: string[];
  flows: string[];
  createdAt: string;
  updatedAt: string;
  isCustom: true;
  // Phase B: Registry
  visibility?: 'private' | 'team' | 'tenant';
  category?: string;
  publishedAt?: string;
  cloneCount?: number;
  clonedFromId?: string;
  complianceTags?: string[];
  complianceNotes?: string;
  // Phase B: Orchestration
  isSupervisor?: boolean;
  childAgentConfig?: Array<{ agent_id: string; task: string; order: number }>;
  executionMode?: 'sequential' | 'parallel';
  resourceConstraints?: { max_concurrent?: number; token_budget?: number; timeout_seconds?: number; max_depth?: number };
  // Background agent execution
  backgroundConfig?: {
    enabled?: boolean;
    cron_expression?: string;
    timezone?: string;
    webhook_url?: string;
    webhook_headers?: Record<string, string>;
  } | null;
  // Deep Work model tier overrides
  deepWorkModels?: DeepWorkModels;
  // Knowledge base selections (stored in config JSON)
  knowledgeBaseIds?: string[];
  // Workflow resources
  workflowIds?: string[];
}

export interface OrganizationAgentsResponse {
  agents: CustomAgent[];
  total: number;
  limit: number;
  offset: number;
}

export interface CategoryInfo {
  name: string;
  count: number;
}

export interface CreateCustomAgentRequest {
  name: string;
  description?: string;
  icon?: string;
  systemPrompt?: string;
  instructionFormat?: 'plain_text' | 'tiptap_json';
  model?: string;
  temperature?: number;
  maxOutputTokens?: number;
  skills?: string[];
  connectors?: string[];
  deepWorkModels?: DeepWorkModels;
  knowledgeBaseIds?: string[];
  workflowIds?: string[];
  isSupervisor?: boolean;
  childAgentConfig?: Array<{ agent_id: string; task: string; order: number }>;
  executionMode?: 'sequential' | 'parallel';
  resourceConstraints?: { max_concurrent?: number; token_budget?: number; timeout_seconds?: number; max_depth?: number };
  backgroundConfig?: {
    enabled?: boolean;
    cron_expression?: string;
    timezone?: string;
    webhook_url?: string;
    webhook_headers?: Record<string, string>;
  } | null;
}

export interface UpdateCustomAgentRequest extends Partial<CreateCustomAgentRequest> { }

/**
 * Agent styles configuration for UI (keyed by slug)
 */
const AGENT_STYLES_BY_SLUG: Record<string, { icon: string; color: string; bgColor: string }> = {
  'general': {
    icon: 'ChatCircle',
    color: 'text-primary',
    bgColor: 'bg-primary/10',
  },
  'data-retrieval': {
    icon: 'Database',
    color: 'text-blue-600',
    bgColor: 'bg-blue-100',
  },
  'analysis': {
    icon: 'ChartLineUp',
    color: 'text-green-600',
    bgColor: 'bg-green-100',
  },
  'compliance': {
    icon: 'ShieldCheck',
    color: 'text-purple-600',
    bgColor: 'bg-purple-100',
  },
  'kyc-aml': {
    icon: 'IdentificationCard',
    color: 'text-indigo-600',
    bgColor: 'bg-indigo-100',
  },
  'market-abuse': {
    icon: 'Warning',
    color: 'text-red-600',
    bgColor: 'bg-red-100',
  },
  'report-generator': {
    icon: 'FileText',
    color: 'text-gray-600',
    bgColor: 'bg-gray-100',
  },
  'research': {
    icon: 'BookOpenText',
    color: 'text-orange-600',
    bgColor: 'bg-orange-100',
  },
};

/**
 * AGENT_STYLES keyed by both slug and UUID for backwards compatibility
 */
export const AGENT_STYLES: Record<string, { icon: string; color: string; bgColor: string }> = {
  ...AGENT_STYLES_BY_SLUG,
  ...Object.fromEntries(
    Object.entries(BUILTIN_AGENT_UUIDS).map(([slug, uuid]) => [uuid, AGENT_STYLES_BY_SLUG[slug]])
  ),
};

/**
 * Get agent style by any ID (slug or UUID). Returns default style if not found.
 */
export function getAgentStyle(id: string): { icon: string; color: string; bgColor: string } {
  return AGENT_STYLES[id] || { icon: 'ShieldCheck', color: 'text-muted-foreground', bgColor: 'bg-muted' };
}

/**
 * LocalStorage key for custom agents
 */
const CUSTOM_AGENTS_KEY = 'oniva-custom';

/**
 * Example/template agents to seed into localStorage
 * These are pre-configured agents that work with Agent Studio
 */
const EXAMPLE_AGENTS: Omit<CustomAgent, 'createdAt' | 'updatedAt'>[] = [
  {
    id: BUILTIN_AGENT_UUIDS['general'],
    name: 'General Assistant',
    description: 'Conversational AI helper - handles greetings, general questions, and guides to specialized agents. Your go-to assistant for everyday tasks.',
    icon: 'ChatCircle',
    type: 'general',
    systemPrompt: `You are a friendly and helpful AI assistant for the Oniva platform.

Your role:
1. Engage naturally in conversation - respond to greetings warmly
2. Answer general questions helpfully
3. Guide users to specialized agents when they have specific needs:
   - Research Agent: For document search, RAG-based Q&A, research tasks
   - Analysis Agent: For financial analysis, stock recommendations, metrics
   - Compliance Agent: For regulatory checks, compliance verification
   - Data Retrieval Agent: For fetching data from external sources

When responding:
- Be conversational and natural
- Keep responses concise but helpful
- If unsure about user intent, ask clarifying questions
- Suggest switching to a specialized agent when appropriate

Example interactions:
- "Hi" → Friendly greeting and offer to help
- "How are you?" → Warm response, ask how you can help
- "What can you do?" → Explain the platform's capabilities
- "Help me analyze AAPL" → Suggest using the Analysis Agent`,
    model: 'openai/gpt-4o-mini',
    temperature: 0.7,
    maxOutputTokens: 2000,
    skills: ['web_search', 'read_file', 'store_memory', 'recall_memory'],
    connectors: [],
    flows: [],
    isCustom: true,
  },
  {
    id: BUILTIN_AGENT_UUIDS['data-retrieval'],
    name: 'Data Retrieval Agent',
    description: 'Fetches financial data from external APIs and data sources. Supports stock prices, market data, company fundamentals, and economic indicators.',
    icon: 'Database',
    type: 'data',
    systemPrompt: `You are a Data Retrieval Agent specialized in financial data. Your job is to:

1. Fetch real-time and historical financial data from various sources
2. Aggregate data from multiple APIs (stock prices, fundamentals, economic indicators)
3. Clean and normalize data for downstream analysis
4. Handle rate limits and retries gracefully
5. Cache frequently accessed data to improve performance

When users request data, always:
- Clarify the specific data points needed
- Specify the date range and frequency
- Note any data limitations or delays
- Provide data in a structured format`,
    model: 'gpt-4o',
    temperature: 0.3,
    maxOutputTokens: 4000,
    skills: ['web_search', 'read_file', 'store_memory'],
    connectors: [],
    flows: [],
    isCustom: true,
  },
  {
    id: BUILTIN_AGENT_UUIDS['analysis'],
    name: 'Analysis Agent',
    description: 'Performs financial analysis including ratio calculations, trend analysis, and statistical modeling on financial data.',
    icon: 'ChartLineUp',
    type: 'analysis',
    systemPrompt: `You are a Financial Analysis Agent. Your capabilities include:

1. Calculating financial ratios (P/E, P/B, ROE, ROA, etc.)
2. Performing trend analysis on time series data
3. Statistical modeling and regression analysis
4. Risk metrics calculation (VaR, Sharpe, Beta, etc.)
5. Comparative analysis across companies and sectors

When analyzing data:
- Always show your calculations
- Provide context for the numbers
- Compare against benchmarks when relevant
- Highlight key insights and anomalies
- Include confidence intervals where appropriate`,
    model: 'gpt-4o',
    temperature: 0.5,
    maxOutputTokens: 4000,
    skills: ['execute_python', 'generate_chart', 'analyze_code'],
    connectors: [],
    flows: [],
    isCustom: true,
  },
  {
    id: BUILTIN_AGENT_UUIDS['compliance'],
    name: 'Compliance Agent',
    description: 'Checks regulatory compliance, identifies risk factors, and ensures adherence to financial regulations like SOX, MiFID II, and Basel III.',
    icon: 'ShieldCheck',
    type: 'compliance',
    systemPrompt: `You are a Compliance Agent specialized in financial regulations. Your responsibilities:

1. Check transactions and activities against regulatory requirements
2. Identify potential compliance violations
3. Assess risk levels and flag high-risk activities
4. Generate compliance reports and documentation
5. Stay updated on regulatory changes

Regulations you monitor:
- SOX (Sarbanes-Oxley Act)
- MiFID II (Markets in Financial Instruments Directive)
- Basel III capital requirements
- AML (Anti-Money Laundering) rules
- GDPR for data handling

Always cite specific regulations when flagging issues.`,
    model: 'gpt-4o',
    temperature: 0.2,
    maxOutputTokens: 4000,
    skills: ['read_file', 'search_knowledge', 'parse_document'],
    connectors: [],
    flows: [],
    isCustom: true,
  },
  {
    id: BUILTIN_AGENT_UUIDS['research'],
    name: 'Research Agent',
    description: 'RAG-based document research and Q&A. Searches through documents, reports, and knowledge bases to answer questions.',
    icon: 'BookOpenText',
    type: 'research',
    systemPrompt: `You are a Research Agent with RAG (Retrieval-Augmented Generation) capabilities. You:

1. Search through document repositories and knowledge bases
2. Extract relevant information from research reports
3. Synthesize information from multiple sources
4. Provide citations and source references
5. Answer questions with evidence-based responses

Research approach:
- Always cite your sources
- Distinguish between facts and interpretations
- Note when information may be outdated
- Identify gaps in available information
- Suggest additional sources when helpful`,
    model: 'gpt-4o',
    temperature: 0.4,
    maxOutputTokens: 4000,
    skills: ['web_search', 'browse_url', 'search_knowledge', 'recall_memory'],
    connectors: [],
    flows: [],
    isCustom: true,
  },
  {
    id: BUILTIN_AGENT_UUIDS['kyc-aml'],
    name: 'KYC/AML Agent',
    description: 'Validates Know Your Customer requirements and performs Anti-Money Laundering checks on transactions and entities.',
    icon: 'IdentificationCard',
    type: 'compliance',
    systemPrompt: `You are a KYC/AML (Know Your Customer / Anti-Money Laundering) Agent. Your role:

1. Verify customer identity documents and information
2. Screen against sanctions lists and PEP databases
3. Analyze transaction patterns for suspicious activity
4. Calculate risk scores for customers and transactions
5. Generate SAR (Suspicious Activity Reports) when needed

Red flags you monitor:
- Unusual transaction patterns
- Structuring (smurfing) attempts
- Inconsistent customer information
- High-risk jurisdictions
- Shell company indicators

Always document your findings with evidence.`,
    model: 'gpt-4o',
    temperature: 0.2,
    maxOutputTokens: 4000,
    skills: ['web_search', 'search_knowledge', 'parse_document'],
    connectors: [],
    flows: [],
    isCustom: true,
  },
  {
    id: BUILTIN_AGENT_UUIDS['market-abuse'],
    name: 'Market Abuse Agent',
    description: 'Detects potential insider trading, market manipulation, and other forms of market abuse in trading activities.',
    icon: 'Warning',
    type: 'compliance',
    systemPrompt: `You are a Market Abuse Detection Agent. You monitor for:

1. Insider trading indicators
2. Market manipulation patterns
3. Spoofing and layering
4. Front-running
5. Wash trading

Detection methods:
- Statistical analysis of trading patterns
- Correlation with material events
- Order book analysis
- Communication surveillance
- Network analysis of trading relationships

When detecting potential abuse:
- Calculate confidence scores
- Provide evidence trail
- Suggest investigation priorities
- Document for regulatory reporting`,
    model: 'gpt-4o',
    temperature: 0.2,
    maxOutputTokens: 4000,
    skills: ['execute_python', 'analyze_code', 'search_knowledge'],
    connectors: [],
    flows: [],
    isCustom: true,
  },
  {
    id: BUILTIN_AGENT_UUIDS['report-generator'],
    name: 'Regulatory Report Generator',
    description: 'Synthesizes analysis into formal regulatory reports following standard formats like Form 10-K, FINRA reports, and custom templates.',
    icon: 'FileText',
    type: 'compliance',
    systemPrompt: `You are a Regulatory Report Generator. Your responsibilities:

1. Generate structured regulatory reports
2. Follow standard formats (10-K, 10-Q, FINRA, etc.)
3. Synthesize data from multiple sources
4. Ensure completeness and accuracy
5. Apply proper formatting and structure

Report types you generate:
- SEC filings (10-K, 10-Q, 8-K)
- FINRA reports
- Basel III compliance reports
- Internal audit reports
- Risk assessment reports

Quality standards:
- Cross-reference all data points
- Include required disclosures
- Use consistent terminology
- Maintain proper formatting`,
    model: 'gpt-4o',
    temperature: 0.3,
    maxOutputTokens: 8000,
    skills: ['write_file', 'read_file', 'search_knowledge'],
    connectors: [],
    flows: [],
    isCustom: true,
  },
];

/**
 * Old slug IDs that may exist in localStorage from before the UUID migration.
 */
const OLD_SLUG_IDS = new Set(Object.keys(BUILTIN_AGENT_UUIDS));

/**
 * Seed example agents into localStorage if they don't exist.
 * Also migrates any old slug-keyed agents to their stable UUIDs.
 * Called once when the app loads.
 */
export function seedExampleAgents(): void {
  if (typeof window === 'undefined') return;

  const existing = getCustomAgents();
  const existingIds = new Set(existing.map(a => a.id));
  const now = new Date().toISOString();

  let updated = false;

  // Migrate old slug IDs → stable UUIDs
  for (const agent of existing) {
    if (OLD_SLUG_IDS.has(agent.id)) {
      const oldId = agent.id;
      const newId = BUILTIN_AGENT_UUIDS[oldId];
      // Store migration for URL redirects
      storeMigratedAgentId(oldId, newId);
      agent.id = newId;
      existingIds.delete(oldId);
      existingIds.add(newId);
      updated = true;
    }
  }

  // Seed any missing example agents
  for (const example of EXAMPLE_AGENTS) {
    if (!existingIds.has(example.id)) {
      existing.push({
        ...example,
        createdAt: now,
        updatedAt: now,
      });
      updated = true;
    }
  }

  if (updated) {
    localStorage.setItem(CUSTOM_AGENTS_KEY, JSON.stringify(existing));
  }
}

/**
 * API Functions for template agents
 */
export async function getAgents(): Promise<AgentInfo[]> {
  return apiFetch<AgentInfo[]>('/api/v1/agents', { timeout: 10000 });
}

export async function getAgent(id: string): Promise<AgentInfo> {
  return apiFetch<AgentInfo>(`/api/v1/agents/${id}`);
}

/**
 * React Query hooks for agents
 */
export function useAgents() {
  return useQuery({
    queryKey: ['agents'],
    queryFn: getAgents,
  });
}

/**
 * Custom Agents API Functions with localStorage fallback
 * Backend-first approach: tries API, falls back to localStorage for offline mode
 */

/**
 * Backend response format for custom agents
 */
interface BackendCustomAgent {
  id: string;
  user_id: string;
  name: string;
  description: string | null;
  icon: string;
  type: string;
  model: string;
  system_prompt: string | null;
  instruction_format: string | null;
  temperature: number;
  max_tokens: number;
  skills: string[];
  connectors: string[];
  config: Record<string, unknown>;
  is_active: boolean;
  is_template: boolean;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
  usage_count: number;
  // Phase B: Registry
  visibility?: string;
  category?: string | null;
  published_at?: string | null;
  clone_count?: number;
  cloned_from_id?: string | null;
  compliance_tags?: string[];
  compliance_notes?: string | null;
  // Background agent execution
  background_config?: {
    enabled?: boolean;
    cron_expression?: string;
    timezone?: string;
    webhook_url?: string;
    webhook_headers?: Record<string, string>;
  } | null;
  // Phase B: Orchestration
  is_supervisor?: boolean;
  child_agent_config?: unknown[];
  execution_mode?: string;
  resource_constraints?: Record<string, unknown>;
  // Workflow resources
  workflow_ids?: string[];
}

/**
 * Convert backend format to frontend CustomAgent format
 */
function backendToFrontend(agent: BackendCustomAgent): CustomAgent {
  return {
    id: agent.id,
    name: agent.name,
    description: agent.description || '',
    icon: agent.icon,
    type: agent.type,
    systemPrompt: agent.system_prompt || '',
    instructionFormat: (agent.instruction_format === 'tiptap_json' ? 'tiptap_json' : 'plain_text') as 'plain_text' | 'tiptap_json',
    model: agent.model,
    temperature: agent.temperature,
    maxOutputTokens: agent.max_tokens,
    skills: agent.skills || [],
    connectors: agent.connectors || [],
    flows: [],
    createdAt: agent.created_at,
    updatedAt: agent.updated_at,
    isCustom: true,
    // Phase B: Registry
    visibility: (agent.visibility as CustomAgent['visibility']) || 'private',
    category: agent.category || undefined,
    publishedAt: agent.published_at || undefined,
    cloneCount: agent.clone_count || 0,
    clonedFromId: agent.cloned_from_id || undefined,
    complianceTags: agent.compliance_tags || [],
    complianceNotes: agent.compliance_notes || undefined,
    // Phase B: Orchestration
    isSupervisor: agent.is_supervisor || false,
    childAgentConfig: (agent.child_agent_config as CustomAgent['childAgentConfig']) || [],
    executionMode: (agent.execution_mode as CustomAgent['executionMode']) || 'sequential',
    resourceConstraints: (agent.resource_constraints as CustomAgent['resourceConstraints']) || {},
    // Background agent execution
    backgroundConfig: agent.background_config || null,
    // Deep Work model tiers (stored in config JSONB)
    deepWorkModels: (agent.config?.deep_work_models as DeepWorkModels) || undefined,
    // Knowledge base selections (stored in config JSONB)
    knowledgeBaseIds: (agent.config?.knowledge_base_ids as string[]) || [],
    // Workflow resources (first-class column)
    workflowIds: agent.workflow_ids || [],
  };
}

/**
 * Convert frontend format to backend request format
 */
function frontendToBackend(agent: Partial<CustomAgent>): Record<string, unknown> {
  const backend: Record<string, unknown> = {};
  if (agent.name !== undefined) backend.name = agent.name;
  if (agent.description !== undefined) backend.description = agent.description;
  if (agent.icon !== undefined) backend.icon = agent.icon;
  if (agent.type !== undefined) backend.agent_type = agent.type;
  if (agent.model !== undefined) backend.model = agent.model;
  if (agent.systemPrompt !== undefined) backend.system_prompt = agent.systemPrompt;
  if (agent.instructionFormat !== undefined) backend.instruction_format = agent.instructionFormat;
  if (agent.temperature !== undefined) backend.temperature = agent.temperature;
  if (agent.maxOutputTokens !== undefined) backend.max_tokens = agent.maxOutputTokens;
  if (agent.skills !== undefined) backend.skills = agent.skills;
  if (agent.connectors !== undefined) backend.connectors = agent.connectors;
  if (agent.resourceConstraints !== undefined) backend.resource_constraints = agent.resourceConstraints;
  if (agent.backgroundConfig !== undefined) backend.background_config = agent.backgroundConfig;
  // Pack deep_work_models and knowledge_base_ids into config JSONB
  if (agent.deepWorkModels !== undefined || agent.knowledgeBaseIds !== undefined) {
    const existingConfig = (backend.config as Record<string, unknown>) || {};
    if (agent.deepWorkModels !== undefined) {
      const hasValues = agent.deepWorkModels.high || agent.deepWorkModels.mid || agent.deepWorkModels.low;
      existingConfig.deep_work_models = hasValues ? agent.deepWorkModels : undefined;
    }
    if (agent.knowledgeBaseIds !== undefined) {
      existingConfig.knowledge_base_ids = agent.knowledgeBaseIds.length > 0 ? agent.knowledgeBaseIds : undefined;
    }
    backend.config = existingConfig;
  }
  // Workflow resources (first-class column, not in config JSONB)
  if (agent.workflowIds !== undefined) {
    backend.workflow_ids = agent.workflowIds;
  }
  return backend;
}

/**
 * Check if a string is a valid UUID
 */
export function isValidUUID(str: string): boolean {
  const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  return uuidRegex.test(str);
}

/**
 * Get migrated agent ID (for URL redirects)
 */
export function getMigratedAgentId(oldId: string): string | null {
  if (typeof window === 'undefined') return null;
  const mapStr = localStorage.getItem('agent_id_migration_map');
  if (!mapStr) return null;
  try {
    const map = JSON.parse(mapStr);
    return map[oldId] || null;
  } catch {
    return null;
  }
}

function storeMigratedAgentId(oldId: string, newId: string): void {
  if (typeof window === 'undefined') return;
  const mapStr = localStorage.getItem('agent_id_migration_map');
  const map = mapStr ? JSON.parse(mapStr) : {};
  map[oldId] = newId;
  localStorage.setItem('agent_id_migration_map', JSON.stringify(map));
}

/**
 * Get localStorage agents (for fallback)
 */
function getLocalAgents(): CustomAgent[] {
  if (typeof window === 'undefined') return [];
  const stored = localStorage.getItem(CUSTOM_AGENTS_KEY);
  if (!stored) return [];
  try {
    return JSON.parse(stored);
  } catch {
    return [];
  }
}

/**
 * Save agents to localStorage (for offline cache)
 */
function saveLocalAgents(agents: CustomAgent[]): void {
  if (typeof window === 'undefined') return;
  localStorage.setItem(CUSTOM_AGENTS_KEY, JSON.stringify(agents));
}

/**
 * Fetch custom agents from backend API with localStorage fallback
 */
export async function getCustomAgentsAsync(): Promise<CustomAgent[]> {
  try {
    const response = await apiFetch<BackendCustomAgent[]>('/api/v1/agents/custom/', { timeout: 10000 });
    const agents = response.map(backendToFrontend);
    // Merge with example agents so built-in agents aren't lost
    const backendIds = new Set(agents.map(a => a.id));
    const local = getLocalAgents();
    const exampleIds = new Set(EXAMPLE_AGENTS.map(e => e.id));
    for (const localAgent of local) {
      if (exampleIds.has(localAgent.id) && !backendIds.has(localAgent.id)) {
        agents.push(localAgent);
      }
    }
    saveLocalAgents(agents);
    return agents;
  } catch (error) {
    console.log('Backend unavailable for custom agents, using localStorage:', error);
    return getLocalAgents();
  }
}

/**
 * Synchronous version for immediate rendering (returns localStorage, triggers async refresh)
 */
export function getCustomAgents(): CustomAgent[] {
  // Return cached localStorage data immediately
  const localAgents = getLocalAgents();

  // Trigger async refresh in the background (fire-and-forget)
  if (typeof window !== 'undefined') {
    getCustomAgentsAsync().catch(() => {});
  }

  return localAgents;
}

/**
 * Get a single custom agent by ID
 */
export async function getCustomAgentAsync(id: string): Promise<CustomAgent | undefined> {
  try {
    const response = await apiFetch<BackendCustomAgent>(`/api/v1/agents/custom/${id}`, { timeout: 10000 });
    return backendToFrontend(response);
  } catch {
    // Fallback to localStorage
    const agents = getLocalAgents();
    return agents.find(agent => agent.id === id);
  }
}

export function getCustomAgent(id: string): CustomAgent | undefined {
  const agents = getLocalAgents();
  return agents.find(agent => agent.id === id);
}

/**
 * Create a new custom agent (API-first with localStorage fallback)
 */
export async function createNewAgentAsync(
  name: string,
  description: string = '',
  options: Partial<CreateCustomAgentRequest> = {}
): Promise<CustomAgent> {
  const requestData = {
    name,
    description,
    icon: options.icon || 'Robot',
    agent_type: 'custom',
    model: options.model || 'gpt-4o',
    system_prompt: options.systemPrompt || '',
    instruction_format: options.instructionFormat || 'plain_text',
    temperature: options.temperature ?? 0.7,
    max_tokens: options.maxOutputTokens ?? 2000,
    skills: options.skills || [],
    connectors: options.connectors || [],
  };

  try {
    const response = await apiFetch<BackendCustomAgent>('/api/v1/agents/custom/', {
      method: 'POST',
      body: JSON.stringify(requestData),
      timeout: 5000,
    });
    const agent = backendToFrontend(response);
    // Update localStorage cache
    const agents = getLocalAgents();
    agents.push(agent);
    saveLocalAgents(agents);
    return agent;
  } catch (error) {
    // Do NOT silently fall back to localStorage — agents created locally
    // cannot be published, deployed, or evaluated via the backend.
    // Re-throw so the UI can show the error to the user.
    console.error('Failed to create agent via API:', error);
    throw error;
  }
}

/**
 * Synchronous create (localStorage only - for backward compatibility)
 */
export function createNewAgent(
  name: string,
  description: string = '',
  options: Partial<CreateCustomAgentRequest> = {}
): CustomAgent {
  const agent = createNewAgentLocal(name, description, options);
  // Trigger async sync to backend (fire-and-forget)
  syncAgentToBackend(agent).catch(() => {});
  return agent;
}

function createNewAgentLocal(
  name: string,
  description: string = '',
  options: Partial<CreateCustomAgentRequest> = {}
): CustomAgent {
  const agents = getLocalAgents();

  const newAgent: CustomAgent = {
    id: crypto.randomUUID(),
    name,
    description,
    icon: options.icon || 'Robot',
    type: 'custom',
    systemPrompt: options.systemPrompt || '',
    instructionFormat: options.instructionFormat || 'plain_text',
    model: options.model || 'gpt-4o',
    temperature: options.temperature ?? 0.7,
    maxOutputTokens: options.maxOutputTokens ?? 2000,
    skills: options.skills || [],
    connectors: options.connectors || [],
    flows: [],
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    isCustom: true,
  };

  agents.push(newAgent);
  saveLocalAgents(agents);

  return newAgent;
}

/**
 * Sync a locally-created agent to the backend
 */
async function syncAgentToBackend(agent: CustomAgent): Promise<void> {
  // Include agent.id so the backend preserves the localStorage UUID
  const payload = { ...frontendToBackend(agent), id: agent.id };
  await apiFetch<BackendCustomAgent>('/api/v1/agents/custom/', {
    method: 'POST',
    body: JSON.stringify(payload),
    timeout: 5000,
  });
}

/**
 * Update a custom agent (API-first with localStorage fallback)
 */
export async function updateCustomAgentAsync(id: string, updates: UpdateCustomAgentRequest): Promise<CustomAgent | null> {
  try {
    const response = await apiFetch<BackendCustomAgent>(`/api/v1/agents/custom/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(frontendToBackend(updates)),
      timeout: 5000,
    });
    const agent = backendToFrontend(response);
    // Update localStorage cache
    const agents = getLocalAgents();
    const index = agents.findIndex(a => a.id === id);
    if (index !== -1) {
      agents[index] = agent;
      saveLocalAgents(agents);
    }
    return agent;
  } catch (error) {
    console.log('Failed to update agent via API, updating localStorage:', error);
    return updateCustomAgentLocal(id, updates);
  }
}

export function updateCustomAgent(id: string, updates: UpdateCustomAgentRequest): CustomAgent | null {
  const agent = updateCustomAgentLocal(id, updates);
  // Trigger async sync to backend (fire-and-forget)
  if (agent) {
    updateCustomAgentAsync(id, updates).catch(() => {});
  }
  return agent;
}

function updateCustomAgentLocal(id: string, updates: UpdateCustomAgentRequest): CustomAgent | null {
  const agents = getLocalAgents();
  const index = agents.findIndex(agent => agent.id === id);

  if (index === -1) return null;

  agents[index] = {
    ...agents[index],
    ...updates,
    updatedAt: new Date().toISOString(),
  };

  saveLocalAgents(agents);

  return agents[index];
}

/**
 * Clone an agent
 */
export async function cloneAgentAsync(templateId: string, name?: string): Promise<CustomAgent | null> {
  // Try to get agent from backend first
  let agent: CustomAgent | undefined;
  try {
    agent = await getCustomAgentAsync(templateId);
  } catch {
    agent = getCustomAgent(templateId);
  }

  if (agent) {
    return createNewAgentAsync(
      name || `${agent.name} (Copy)`,
      agent.description,
      {
        systemPrompt: agent.systemPrompt,
        model: agent.model,
        temperature: agent.temperature,
        maxOutputTokens: agent.maxOutputTokens,
        skills: agent.skills,
        connectors: agent.connectors,
      }
    );
  }

  // Template names for built-in agents (keyed by slug)
  const templateNames: Record<string, string> = {
    'data-retrieval': 'Data Retrieval Agent',
    'analysis': 'Analysis Agent',
    'compliance': 'Compliance Agent',
    'kyc-aml': 'KYC/AML Agent',
    'market-abuse': 'Market Abuse Agent',
    'report-generator': 'Report Generator Agent',
    'research': 'Research Agent',
  };

  // Normalise UUID→slug for lookup
  const slug = uuidToSlug(templateId);
  const templateName = templateNames[slug] || 'Agent';

  return createNewAgentAsync(
    name || `${templateName} (Copy)`,
    `Cloned from ${templateName}`,
    {}
  );
}

export function cloneAgent(templateId: string, name?: string): CustomAgent | null {
  // Try to find in custom agents first
  let agent = getCustomAgent(templateId);

  if (agent) {
    return createNewAgent(
      name || `${agent.name} (Copy)`,
      agent.description,
      {
        systemPrompt: agent.systemPrompt,
        model: agent.model,
        temperature: agent.temperature,
        maxOutputTokens: agent.maxOutputTokens,
        skills: agent.skills,
        connectors: agent.connectors,
      }
    );
  }

  const templateNames: Record<string, string> = {
    'data-retrieval': 'Data Retrieval Agent',
    'analysis': 'Analysis Agent',
    'compliance': 'Compliance Agent',
    'kyc-aml': 'KYC/AML Agent',
    'market-abuse': 'Market Abuse Agent',
    'report-generator': 'Report Generator Agent',
    'research': 'Research Agent',
  };

  // Normalise UUID→slug for lookup
  const slug = uuidToSlug(templateId);
  const templateName = templateNames[slug] || 'Agent';

  return createNewAgent(
    name || `${templateName} (Copy)`,
    `Cloned from ${templateName}`,
    {}
  );
}

/**
 * Delete a custom agent (API-first with localStorage fallback)
 */
export async function deleteCustomAgentAsync(id: string): Promise<boolean> {
  try {
    await apiFetch<void>(`/api/v1/agents/custom/${id}`, {
      method: 'DELETE',
      timeout: 5000,
    });
    // Update localStorage cache
    const agents = getLocalAgents();
    const filtered = agents.filter(agent => agent.id !== id);
    saveLocalAgents(filtered);
    return true;
  } catch (error) {
    console.log('Failed to delete agent via API, deleting from localStorage:', error);
    return deleteCustomAgentLocal(id);
  }
}

export function deleteCustomAgent(id: string): boolean {
  const success = deleteCustomAgentLocal(id);
  // Trigger async sync to backend (fire-and-forget)
  if (success) {
    deleteCustomAgentAsync(id).catch(() => {});
  }
  return success;
}

function deleteCustomAgentLocal(id: string): boolean {
  const agents = getLocalAgents();
  const filtered = agents.filter(agent => agent.id !== id);

  if (filtered.length === agents.length) {
    return false; // Agent not found
  }

  saveLocalAgents(filtered);
  return true;
}

/**
 * Agent Version types
 */
export interface AgentVersion {
  id: string;
  tenant_id: string;
  agent_id: string;
  version: string;
  config: Record<string, unknown>;
  is_deployed: boolean;
  deployed_at?: string;
  deployed_by?: string;
  notes?: string;
  accuracy_at_deploy?: number;  // Accuracy percentage when version was deployed
  created_at: string;
}

export interface AgentVersionList {
  versions: AgentVersion[];
  total: number;
  current_deployed?: string;
}

export interface CreateVersionRequest {
  agent_id: string;
  version?: string;
  increment_type?: 'major' | 'minor' | 'patch';
  notes?: string;
  deploy?: boolean;
}

export interface DeployVersionResponse {
  success: boolean;
  version: string;
  deployed_at: string;
  message: string;
}

/**
 * Agent Version API Functions
 */
export async function createAgentVersion(data: CreateVersionRequest): Promise<AgentVersion> {
  return apiFetch<AgentVersion>('/api/v1/agent-versions/', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function getAgentVersions(agentId: string): Promise<AgentVersionList> {
  return apiFetch<AgentVersionList>(`/api/v1/agent-versions/agent/${agentId}`);
}

export async function deployVersion(versionId: string): Promise<DeployVersionResponse> {
  return apiFetch<DeployVersionResponse>('/api/v1/agent-versions/deploy', {
    method: 'POST',
    body: JSON.stringify({ version_id: versionId }),
  });
}

/**
 * LocalStorage enhanced functions for draft/deploy workflow
 * These simulate versioning in localStorage until backend supports custom agents
 */

// localStorage versioning removed — all version operations go through backend API
// See frontend/lib/api/agent-versions.ts and frontend/hooks/useAgentVersions.ts

/**
 * React Query hook for custom agents
 */
export function useCustomAgents() {
  return useQuery({
    queryKey: ['customAgents'],
    queryFn: getCustomAgentsAsync,
    staleTime: 60000, // 1 minute
  });
}


// ── Phase B: Organization Registry ──────────────────────────────────────────

/**
 * Browse organization-wide published agents
 */
export async function getOrganizationAgentsAsync(params?: {
  query?: string;
  category?: string;
  complianceTag?: string;
  sortBy?: string;
  limit?: number;
  offset?: number;
}): Promise<OrganizationAgentsResponse> {
  const searchParams = new URLSearchParams();
  if (params?.query) searchParams.set('query', params.query);
  if (params?.category) searchParams.set('category', params.category);
  if (params?.complianceTag) searchParams.set('compliance_tag', params.complianceTag);
  if (params?.sortBy) searchParams.set('sort_by', params.sortBy);
  if (params?.limit) searchParams.set('limit', String(params.limit));
  if (params?.offset) searchParams.set('offset', String(params.offset));

  const qs = searchParams.toString();
  const url = `/api/v1/agents/custom/organization${qs ? `?${qs}` : ''}`;

  try {
    const response = await apiFetch<{
      agents: BackendCustomAgent[];
      total: number;
      limit: number;
      offset: number;
    }>(url, { timeout: 5000 });

    return {
      agents: response.agents.map(backendToFrontend),
      total: response.total,
      limit: response.limit,
      offset: response.offset,
    };
  } catch (error) {
    console.log('Failed to fetch organization agents:', error);
    return { agents: [], total: 0, limit: 20, offset: 0 };
  }
}

/**
 * Get categories for organization agents
 */
export async function getOrganizationCategoriesAsync(): Promise<CategoryInfo[]> {
  try {
    return await apiFetch<CategoryInfo[]>('/api/v1/agents/custom/organization/categories', { timeout: 10000 });
  } catch {
    return [];
  }
}

/**
 * Publish an agent to team or organization.
 * If the agent only exists in localStorage (404 from backend), attempts to
 * sync it to the backend first, then retries publish.
 */
export async function publishAgentAsync(agentId: string, visibility: 'team' | 'tenant', category?: string, complianceTags?: string[]): Promise<CustomAgent | null> {
  try {
    const response = await apiFetch<BackendCustomAgent>(`/api/v1/agents/custom/${agentId}/publish`, {
      method: 'POST',
      body: JSON.stringify({ visibility, category, compliance_tags: complianceTags }),
      timeout: 5000,
    });
    return backendToFrontend(response);
  } catch (error) {
    // If agent doesn't exist in backend (404), try to sync from localStorage first
    if (error instanceof APIError && error.status === 404) {
      const localAgent = getCustomAgent(agentId);
      if (localAgent) {
        try {
          await syncAgentToBackend(localAgent);
          // Retry publish after sync
          const response = await apiFetch<BackendCustomAgent>(`/api/v1/agents/custom/${agentId}/publish`, {
            method: 'POST',
            body: JSON.stringify({ visibility, category, compliance_tags: complianceTags }),
            timeout: 5000,
          });
          return backendToFrontend(response);
        } catch (syncError) {
          console.error('Failed to sync agent to backend before publish:', syncError);
          throw new APIError(
            'Agent must be saved to the server before publishing. Please re-create the agent.',
            404,
          );
        }
      }
    }
    console.error('Failed to publish agent:', error);
    throw error;
  }
}

/**
 * Unpublish an agent (reset to private)
 */
export async function unpublishAgentAsync(agentId: string): Promise<CustomAgent | null> {
  try {
    const response = await apiFetch<BackendCustomAgent>(`/api/v1/agents/custom/${agentId}/publish`, {
      method: 'DELETE',
      timeout: 5000,
    });
    return backendToFrontend(response);
  } catch (error) {
    console.error('Failed to unpublish agent:', error);
    return null;
  }
}

/**
 * React Query hook for organization agents
 */
export function useOrganizationAgents(params?: {
  query?: string;
  category?: string;
  sortBy?: string;
  enabled?: boolean;
}) {
  return useQuery({
    queryKey: ['organizationAgents', params?.query, params?.category, params?.sortBy],
    queryFn: () => getOrganizationAgentsAsync({
      query: params?.query,
      category: params?.category,
      sortBy: params?.sortBy,
    }),
    staleTime: 60000, // 1 minute
    enabled: params?.enabled !== false,
  });
}

/**
 * React Query hook for organization categories
 */
export function useOrganizationCategories(enabled = true) {
  return useQuery({
    queryKey: ['organizationCategories'],
    queryFn: getOrganizationCategoriesAsync,
    staleTime: 300000, // 5 minutes
    enabled,
  });
}
