# Key Terminology

A comprehensive glossary of terms used throughout the Oniva platform. Terms are organized alphabetically with examples and cross-references.

---

## A

### Agent
An AI-powered specialist designed to perform specific tasks using language models, skills, and reasoning capabilities.

**Example**: The Compliance Agent performs KYC/AML screening and generates regulatory reports.

**Related**: [Skills](#skill), [Workflow](#workflow), [Custom Agent](#custom-agent)

### AML (Anti-Money Laundering)
Regulatory framework and processes to detect and prevent money laundering activities.

**In Oniva**: AML screening skill checks transaction patterns, customer risk factors, and suspicious activities per BSA/PATRIOT Act requirements.

### API Key
Encrypted authentication credential used to access Oniva APIs and external services.

**Security**: Stored using Fernet encryption with PBKDF2 key derivation. Never logged in plaintext.

**Related**: [Authentication](/docs/api-reference/authentication)

### Audit Log
Immutable record of all actions, decisions, and data access within the platform.

**Retention**: 7 years minimum for regulatory compliance (SOX, FCA, BSA)

**Features**:
- Append-only (no UPDATE/DELETE operations)
- Includes reasoning chains for high-stakes decisions
- Exportable for regulatory examination

**Related**: [Extended Thinking](#extended-thinking), [Immutability](#immutability)

---

## B

### BaseAgent
Core agent class that all specialized agents inherit from. Provides common functionality for LLM interaction, skill execution, and memory management.

**Technical**: Pydantic AI agent with dependency injection pattern.

**Related**: [Custom Agent](#custom-agent)

### BSA (Bank Secrecy Act)
US federal law requiring financial institutions to report suspicious activities and maintain anti-money laundering programs.

**In Oniva**: Compliance Agent implements BSA requirements including SAR filing thresholds and CTR reporting.

---

## C

### Cache Breakpoint
Marker in prompts that separates cached (static) content from dynamic content.

**Purpose**: Reduce token usage by 80-90% on repeated content (system prompts, skill definitions).

**Example**:
```
[System Prompt] ← Cached
[Skill Definitions] ← Cached
----CACHE BREAKPOINT----
[User Query] ← Not Cached
```

**Phase**: Phase 2 feature

**Related**: [Prompt Caching](#prompt-caching), [Cost Optimization](#cost-optimization)

### Chain-of-Thought (CoT)
Reasoning process where the agent shows intermediate thinking steps before reaching a conclusion.

**In Oniva**: Used in extended thinking mode to create transparent decision trails.

**Related**: [Extended Thinking](#extended-thinking), [CoT Faithfulness](#cot-faithfulness)

### Checkpoint
Immutable snapshot of conversation state stored in PostgreSQL via LangGraph.

**Contents**: Full conversation history, agent decisions, execution state.

**Compliance**: Prevents retroactive modification of decision records.

**Related**: [Memory Architecture](#memory-architecture), [LangGraph](#langgraph)

### Circuit Breaker
Protective pattern that prevents cascading failures when external services are down.

**States**:
- Closed: Normal operation
- Open: Service failing, block requests
- Half-Open: Testing recovery

**Example**: If credit bureau API fails 5 times in 1 minute, circuit breaker opens for 5 minutes.

**Related**: [Error Handling](#error-handling)

### Compliance Agent
Pre-configured agent specializing in regulatory checks, KYC/AML screening, and compliance workflows.

**Skills**: kyc_verification, aml_screening, sanctions_check, regulatory_filing, risk_assessment

**Related**: [Agents](/docs/features/ai-agents)

### Condition Node
Workflow node that routes execution based on boolean expressions or business rules.

**Example**:
```yaml
if credit_score >= 700 AND dti < 43:
  route_to: auto_approve
else:
  route_to: manual_review
```

**Related**: [Workflow Nodes](#workflow-node)

### Connector
Integration component that enables communication with external systems via MCP (Model Context Protocol).

**Available**:
- Core banking APIs
- OFAC screening service
- Credit bureau APIs

**Related**: [MCP](#mcp-model-context-protocol)

### Context Window
The amount of text (in tokens) that a language model can process in a single request.

**Limits**:
- Claude 3.5 Sonnet: 200K tokens
- GPT-4 Turbo: 128K tokens
- Effective usage: 8K-16K tokens per request (with headroom)

**Optimization**: Use memory compression to fit long conversations within context limits.

**Related**: [Memory Store](#memory-store), [Token](#token)

### CoT Faithfulness
Monitoring system that checks whether agent reasoning includes all required decision factors.

**Purpose**: Detect incomplete reasoning that could fail regulatory examination.

**Example**: Loan denial must cite specific factors per ECOA. CoT faithfulness monitors ensure all required factors are mentioned.

**Phase**: Phase 1 feature

**Related**: [Extended Thinking](#extended-thinking), [HITL](#hitl-human-in-the-loop)

### Cost Tracking
System for measuring and monitoring LLM token usage and costs.

**Metrics**:
- Prompt tokens (input)
- Completion tokens (output)
- Cached tokens (reused content)
- Total cost per request
- Cost by agent/workflow/user

**Phase**: Phase 2 feature

**Related**: [Cost Optimization](#cost-optimization)

### Custom Agent
User-defined agent with specialized skills, system prompts, and behavior for specific business processes.

**Example**: Loan Officer Agent, Fraud Detection Agent, Customer Service Agent.

**Related**: [Agents](/docs/features/ai-agents), [Custom Agent Guide](/docs/features/custom-agents)

---

## D

### DBOS (Durable Execution)
Workflow orchestration engine that provides fault-tolerant, stateful execution.

**Features**:
- Automatic retries on failure
- State persistence at each step
- Recovery from crashes
- Complete execution audit trail

**Related**: [Workflow](#workflow), [Workflow Orchestration](/docs/concepts/workflow-orchestration)

### Dependency Injection
Pattern where agent dependencies (database, API keys, connectors) are provided at runtime rather than hardcoded.

**In Oniva**: BaseAgent receives AgentDependencies object containing db, user_id, api_keys, connectors, memory context.

**Benefits**: Testability, security, flexibility.

### DTI Ratio (Debt-to-Income)
Financial metric calculated as monthly debt payments divided by gross monthly income.

**Used In**: Loan approval workflows to assess borrower capacity.

**Threshold**: Typically 43% maximum for conventional loans.

---

## E

### Entity
A distinct object tracked in the knowledge graph (customer, account, loan, transaction).

**Phase**: Phase 3 feature (Zep Graphiti)

**Attributes**:
- Entity type (customer, account, loan, etc.)
- Properties (name, balance, status, etc.)
- Relationships (owns, applied_for, transacted_with)
- Temporal validity (valid_at, invalid_at timestamps)

**Related**: [Entity Graph](#entity-graph), [Temporal Versioning](#temporal-versioning)

### Entity Graph
Knowledge graph representing relationships between financial entities with temporal tracking.

**Storage**: Neo4j graph database

**Query Capabilities**:
- Relationship traversal (customer → accounts → transactions)
- Time-travel queries (entity state at specific date)
- Pattern matching (find all high-risk customers with recent large loans)

**Phase**: Phase 3 feature

**Related**: [Zep](#zep), [Graphiti](#graphiti)

### Error Handler
Configuration that defines what happens when a node fails during workflow execution.

**Options**:
- Retry with backoff
- Route to alternative path
- Fall back to default value
- Escalate to human review
- Fail entire workflow

**Related**: [Circuit Breaker](#circuit-breaker), [Graceful Degradation](#graceful-degradation)

### Extended Thinking
Mode where agents capture their complete reasoning process for transparency and compliance.

**Captured**:
- Observation: What data was considered
- Analysis: How data was interpreted
- Decision: What conclusion was reached
- Factors: Which regulations were evaluated

**Use Cases**: Loan decisions, SAR filings, trade surveillance, risk classifications.

**Phase**: Phase 1 feature

**Related**: [CoT](#chain-of-thought-cot), [Audit Log](#audit-log)

---

## F

### Faithfulness Monitor
See [CoT Faithfulness](#cot-faithfulness)

### Fernet Encryption
Symmetric encryption algorithm used to encrypt API keys and sensitive data at rest.

**Key Derivation**: PBKDF2 with SHA256

**Security**: Industry-standard cryptographic library (cryptography package)

---

## G

### Graceful Degradation
Design pattern where system continues operating with reduced functionality when components fail.

**Example**: If credit bureau API is down, use cached credit data or route to manual review instead of failing completely.

**Related**: [Circuit Breaker](#circuit-breaker), [Error Handler](#error-handler)

### Graphiti
Open-source temporal knowledge graph engine by Zep AI used for entity relationship tracking.

**Features**:
- Temporal versioning (valid_at/invalid_at)
- Relationship reasoning
- Graph pattern matching

**Phase**: Phase 3 feature

**Related**: [Entity Graph](#entity-graph), [Zep](#zep)

---

## H

### HITL (Human-in-the-Loop)
Workflow pattern where high-risk or uncertain decisions are routed to human reviewers.

**Triggers**:
- Risk score above threshold
- Dollar amount exceeds limit
- Missing decision factors
- Compliance flags
- Manual escalation

**Phase**: Phase 1 feature

**Related**: [Extended Thinking](#extended-thinking), [Review Queue](#review-queue)

---

## I

### Immutability
Property of audit logs and checkpoints that prevents modification or deletion after creation.

**Implementation**: PostgreSQL triggers that block UPDATE and DELETE operations.

**Compliance**: Required for SOX, FCA, and financial audit trails.

**Related**: [Audit Log](#audit-log), [Checkpoint](#checkpoint)

---

## K

### KYC (Know Your Customer)
Regulatory process to verify customer identity and assess risk.

**In Oniva**: KYC verification skill checks identity documents, sanctions lists, and PEP status.

**Regulations**: PATRIOT Act Section 326, BSA 31 USC 5311

**Related**: [Compliance Agent](#compliance-agent), [AML](#aml-anti-money-laundering)

---

## L

### LangGraph
Framework for building stateful, multi-agent applications with persistent checkpointing.

**In Oniva**: Provides:
- Conversation checkpointing (immutable audit trail)
- Memory Store integration (cross-session persistence)
- State management for workflows

**Related**: [Checkpoint](#checkpoint), [Memory Store](#memory-store)

### Long-Term Memory
Persistent storage for user preferences, patterns, and cross-session facts.

**Storage**: PostgreSQL with 7-year retention

**Contents**: Preferences, learned patterns, historical decisions

**Access Time**: <50ms p95

**Related**: [Memory Architecture](#memory-architecture), [Session Memory](#session-memory)

---

## M

### MCP (Model Context Protocol)
Standardized protocol for connecting LLMs to external tools and data sources.

**In Oniva**: Used for connectors to core banking APIs, OFAC services, credit bureaus.

**Phase**: Phase 3 for production connectors

**Related**: [Connector](#connector)

### Memory Architecture
Four-layer hybrid memory system:

**Layer 1**: Session memory (Redis, <5ms, 24h TTL)
**Layer 2**: Long-term memory (PostgreSQL, <50ms, 7 years)
**Layer 3**: Memory Store (LangGraph, compression, permanent)
**Layer 4**: Entity graph (Zep, <200ms, relationships)

**Related**: [Session Memory](#session-memory), [Long-Term Memory](#long-term-memory)

### Memory Store
LangGraph-based system for cross-session fact persistence and conversation summarization.

**Token Reduction**: 90% via summarization (5,000 tokens → 500 tokens)

**Phase**: Phase 2 feature

**Related**: [Conversation Summarization](#conversation-summarization)

### Model Router
Intelligent system that selects the optimal LLM based on risk, cost, and complexity.

**Routing Strategy**:
- High-risk (score >7): Claude 3.7 Sonnet
- Standard: Claude 3.5 Sonnet
- Cached/repetitive: DeepSeek-V3

**Phase**: Phase 2 feature

**Related**: [Cost Optimization](#cost-optimization)

---

## N

### Node
Individual step in a workflow that performs a specific action.

**Types**: Agent, LLM, Skill, Connector, HTTP, Code, Condition, Iterator, Classifier, Aggregator, Template, Knowledge, Output (18 total)

**Related**: [Workflow](#workflow)

---

## O

### OFAC (Office of Foreign Assets Control)
US Treasury department that administers economic and trade sanctions.

**In Oniva**: Sanctions check skill screens entities against OFAC SDN list.

**Compliance**: Required for financial institutions under 31 CFR Chapter X.

**Related**: [Sanctions Screening](#sanctions-screening)

### Orchestration
See [Workflow Orchestration](#workflow-orchestration)

---

## P

### pgvector
PostgreSQL extension for vector embeddings and semantic search.

**In Oniva**: Powers knowledge search skill for RAG (Retrieval-Augmented Generation).

**Search Method**: Cosine similarity combined with BM25 (hybrid search).

**Related**: [RAG](#rag-retrieval-augmented-generation), [Semantic Search](#semantic-search)

### Prompt Caching
Technique to reuse static portions of prompts across requests, reducing token costs by 80-90%.

**Implementation**: Cache breakpoints separate static from dynamic content.

**Providers**: Anthropic (Claude), OpenAI (ephemeral)

**Phase**: Phase 2 feature

**Related**: [Cache Breakpoint](#cache-breakpoint), [Cost Optimization](#cost-optimization)

### Pydantic AI
Python framework for building type-safe AI agents with structured outputs.

**Version**: v0.0.14

**In Oniva**: All agents built on Pydantic AI BaseAgent class.

---

## R

### RAG (Retrieval-Augmented Generation)
Technique where LLMs are augmented with retrieved documents from a knowledge base.

**In Oniva**: Knowledge search skill uses pgvector + BM25 hybrid search.

**Use Cases**: Policy research, regulatory guidance retrieval, case analysis.

**Related**: [pgvector](#pgvector), [Research Agent](#research-agent)

### Reasoning Chain
Structured sequence of thinking steps captured during extended thinking mode.

**Storage**: PostgreSQL reasoning_chains table (immutable)

**Link**: Foreign key to audit_logs (decision record)

**Related**: [Extended Thinking](#extended-thinking), [Audit Log](#audit-log)

### Review Queue
List of decisions awaiting human review in HITL workflow.

**Management**:
- Priority sorting (risk score, amount, age)
- Reviewer assignment (round-robin, workload-based)
- SLA tracking (escalate if >48 hours)

**Phase**: Phase 1 feature

**Related**: [HITL](#hitl-human-in-the-loop)

---

## S

### Sanctions Screening
Process of checking individuals and entities against government sanctions lists (OFAC, EU, UN).

**In Oniva**: sanctions_check skill queries OFAC SDN list.

**Requirement**: Mandatory for US financial institutions under 31 CFR Chapter X.

**Related**: [OFAC](#ofac-office-of-foreign-assets-control), [Compliance Agent](#compliance-agent)

### SAR (Suspicious Activity Report)
Report filed with FinCEN when suspicious transactions or patterns are detected.

**Threshold**: $5,000 for most activities, $2,000 for structuring

**Timing**: Within 30 days of detection

**In Oniva**: Compliance Agent can draft SARs based on transaction analysis.

**Related**: [BSA](#bsa-bank-secrecy-act), [AML](#aml-anti-money-laundering)

### Semantic Search
Search method using vector embeddings to find conceptually similar content, not just keyword matches.

**Example**: Search for "money laundering" also finds "structuring", "smurfing", "layering".

**Implementation**: pgvector cosine similarity

**Related**: [pgvector](#pgvector), [RAG](#rag-retrieval-augmented-generation)

### Session Memory
Real-time conversation state storage for active user sessions.

**Storage**: Redis (in-memory)

**Duration**: 24 hours (auto-expire)

**Access Time**: <5ms p95

**Contents**: Last 10 turns, entities mentioned, session metadata

**Related**: [Memory Architecture](#memory-architecture), [Redis](#redis)

### Skill
Reusable capability that agents can invoke to perform specific actions.

**Categories**: Web (3), Code (3), Financial (5), Compliance (5), Document (3), Memory (4), File (3), Security (custom)

**Total**: 85+ skills across 8 categories

**Related**: [Skills Library](/docs/features/skills-library), [Agent](#agent)

### System Prompt
Instructions that define agent behavior, decision criteria, and tone.

**Best Practices**:
- Define role clearly
- Specify decision criteria
- Set constraints (ALWAYS/NEVER)
- Establish tone and style

**Related**: [Custom Agent](#custom-agent)

---

## T

### Temporal Versioning
Tracking how entity properties change over time with valid_at/invalid_at timestamps.

**Purpose**: Regulatory time-travel queries ("What did the system know on date X?")

**Implementation**: Neo4j graph database with temporal properties

**Phase**: Phase 3 feature

**Related**: [Entity Graph](#entity-graph), [Time-Travel Query](#time-travel-query)

### Time-Travel Query
Query that retrieves entity state as it existed at a specific point in time.

**Example**: "Show me customer risk score when loan was approved on March 20, 2025"

**Compliance**: Critical for regulatory examination and legal disputes

**Phase**: Phase 3 feature

**Related**: [Temporal Versioning](#temporal-versioning), [Entity](#entity)

### Token
Unit of text used by language models. Roughly 4 characters = 1 token.

**Examples**:
- "Hello" = 1 token
- "Hello world" = 2 tokens
- Average English word = 1.3 tokens

**Cost**: Varies by model (GPT-4 > Claude 3.5 > DeepSeek)

**Related**: [Context Window](#context-window), [Cost Tracking](#cost-tracking)

---

## W

### Workflow
Visual automation that chains multiple agents, skills, and logic together to complete complex processes.

**Engine**: DBOS (durable execution)

**Node Types**: 18 types including Agent, Skill, Condition, Iterator, HITL

**Related**: [Workflow Node](#workflow-node), [Orchestration](#workflow-orchestration)

### Workflow Node
Individual step in a workflow. See [Node](#node).

### Workflow Orchestration
System for managing workflow execution, state persistence, error handling, and recovery.

**Engine**: DBOS

**Features**:
- Durable execution (survives crashes)
- State checkpointing
- Automatic retries
- Complete audit trail

**Related**: [DBOS](#dbos-durable-execution), [Workflow](/docs/features/workflows)

---

## Z

### Zep
Platform for building context-aware AI applications with entity memory and temporal reasoning.

**In Oniva**: Provides entity graph and temporal knowledge features.

**Phase**: Phase 3 feature

**Related**: [Graphiti](#graphiti), [Entity Graph](#entity-graph)

---

## Acronyms Quick Reference

| Acronym | Full Name | Description |
|---------|-----------|-------------|
| AML | Anti-Money Laundering | Regulatory framework to prevent money laundering |
| API | Application Programming Interface | Programmatic interface for system integration |
| BSA | Bank Secrecy Act | US law requiring suspicious activity reporting |
| CoT | Chain-of-Thought | Step-by-step reasoning process |
| CTR | Currency Transaction Report | Report for cash transactions >$10K |
| DBOS | Durable Execution OS | Workflow orchestration engine |
| DTI | Debt-to-Income | Financial ratio for lending decisions |
| ECOA | Equal Credit Opportunity Act | Fair lending law |
| FCA | Financial Conduct Authority | UK financial regulator |
| HITL | Human-in-the-Loop | Pattern requiring human decision |
| KYC | Know Your Customer | Identity verification process |
| LLM | Large Language Model | AI model powering agents |
| MCP | Model Context Protocol | Standard for LLM tool integration |
| OFAC | Office of Foreign Assets Control | US sanctions authority |
| PEP | Politically Exposed Person | High-risk individual category |
| RAG | Retrieval-Augmented Generation | LLM + knowledge base technique |
| SAR | Suspicious Activity Report | Regulatory filing for suspicious activity |
| SOX | Sarbanes-Oxley Act | Corporate accountability law |
| TTL | Time-to-Live | Expiration time for cached data |

---

## Next Steps

**Understand Core Concepts**
→ [Core Concepts Guide](/docs/getting-started/core-concepts): Deep dive into agents, skills, workflows

**Build Your First Workflow**
→ [First Workflow Tutorial](/docs/getting-started/first-workflow): Step-by-step loan approval automation

**Explore Features**
→ [Features Overview](/docs/features/): Agents, workflows, skills, connectors

**API Integration**
→ [API Reference](/docs/api-reference/): Programmatic platform access
