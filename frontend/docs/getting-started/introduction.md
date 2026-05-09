# Introduction to Oniva

Welcome to Oniva, the AI-powered financial services platform built for compliance-focused workflows and intelligent automation.

## What is Oniva?

Oniva is an enterprise-grade platform that combines artificial intelligence with regulatory compliance to automate complex financial services workflows. Built specifically for banks, credit unions, fintech companies, and financial institutions, Oniva helps you:

- **Automate Compliance Operations**: Run KYC/AML checks, OFAC screening, and SAR filings with AI-powered agents
- **Accelerate Financial Analysis**: Analyze portfolios, assess risk, and generate reports in seconds
- **Ensure Regulatory Compliance**: Maintain immutable audit trails with extended thinking capture for FCA, SOX, and EU AI Act compliance
- **Reduce Operational Costs**: Achieve 50-70% automation rates while maintaining human oversight where needed

## Key Capabilities

### 5 Production-Ready AI Agents

Oniva includes specialized agents designed for financial services:

| Agent | Purpose | Use Cases |
|-------|---------|-----------|
| **Analysis Agent** | Financial metrics and data analysis | Portfolio analysis, risk assessment, financial modeling |
| **Compliance Agent** | Regulatory checks and verification | KYC/AML screening, sanctions checks, regulatory reporting |
| **Data Retrieval Agent** | Fetch data from external systems | Core banking integration, credit bureau queries, market data |
| **Research Agent** | Document analysis and knowledge retrieval | Policy research, regulatory document search, case analysis |
| **Custom Agents** | User-defined specialized workflows | Loan decisioning, fraud detection, customer service |

### 85+ Skills Across 8 Categories

Skills are reusable capabilities that agents can use to perform specific tasks:

- **Web Skills** (3): Search, browse URLs, extract structured data
- **Code Skills** (3): Execute Python, analyze code, generate visualizations
- **Financial Skills** (5): Market data, financial statements, risk metrics, economic indicators
- **Compliance Skills** (5): KYC verification, AML screening, sanctions checks, risk assessment
- **Document Skills** (3): PDF parsing, entity extraction, summarization
- **Memory Skills** (4): Session memory, knowledge search, context retention
- **File Skills** (3): Read/write files, parse documents
- **Security Skills** (Custom): Encryption, access control, audit logging

### Visual Workflow Builder

Create complex multi-agent workflows without code using our drag-and-drop interface with 18 node types:

- **Agent Nodes**: Execute AI agents with specific instructions
- **LLM Nodes**: Direct language model calls for flexibility
- **Skill Nodes**: Run individual skills independently
- **Connector Nodes**: Integrate with MCP-enabled external systems
- **Logic Nodes**: Conditional branching, loops, classification
- **Data Nodes**: Transform, aggregate, validate data
- **Output Nodes**: Format and deliver results

### Compliance Features

**Available Now:**
- Immutable audit trails (7-year retention)
- Extended thinking capture for decision transparency
- Real-time monitoring and alerting

**Coming Soon (Phase 1 - Q1 2026):**
- Human-in-the-loop (HITL) review workflows
- CoT faithfulness monitoring
- Compliance dashboard with regulatory export

## Who Should Use Oniva?

### Compliance Officers

Automate repetitive compliance tasks while maintaining full audit trails and regulatory transparency. Perfect for:
- Daily sanctions screening
- KYC/AML verification workflows
- Suspicious Activity Report (SAR) generation
- Regulatory examination responses

### Financial Analysts

Accelerate research and analysis with AI-powered data retrieval and computation:
- Portfolio performance analysis
- Risk metric calculations
- Market research and trend analysis
- Financial statement analysis

### Developers & Technical Teams

Build custom agents and integrate with existing systems:
- Create specialized agents for your workflows
- Connect to core banking systems via MCP connectors
- Extend platform capabilities with custom skills
- Integrate via REST API for programmatic access

### Operations & Administrators

Manage users, monitor system performance, and ensure operational excellence:
- User and permission management
- System monitoring and cost tracking
- Workflow performance optimization
- Audit trail management

## Architecture Highlights

Oniva is built on a modern, scalable architecture:

- **Backend**: FastAPI (Python) with async/await for high performance
- **Database**: PostgreSQL with pgvector for semantic search and 7-year compliance retention
- **Caching**: Redis for session memory and <5ms response times
- **Agent Framework**: Pydantic AI v0.0.14 for type-safe agent execution
- **Workflow Engine**: DBOS for durable, fault-tolerant orchestration
- **Frontend**: Next.js 15 with streaming chat interface
- **LLM Providers**: Multi-provider support (OpenAI, Anthropic, OpenRouter)

## Regulatory Compliance

Oniva is designed to meet stringent regulatory requirements:

- **FCA (Financial Conduct Authority)**: Extended thinking capture and immutable audit trails
- **SOX (Sarbanes-Oxley)**: 7-year retention of decision records
- **GDPR**: Right to deletion with compliant data anonymization
- **EU AI Act**: Explainable AI with reasoning chain transparency
- **BSA/AML**: Complete audit trails for suspicious activity monitoring

## Cost Optimization

Built-in cost controls help you scale efficiently:

- **Prompt Caching**: 80-90% token reduction on repeated queries
- **Intelligent Model Routing**: Use cost-effective models for low-risk tasks
- **Memory Store Integration**: 90% token reduction for long conversations
- **Real-time Cost Tracking**: Monitor spend per workflow, agent, and user

**Target Costs** (at 500K queries/month):
- Current (Phase 0): ~$2,000/month
- Phase 2 (Optimized): ~$485/month (77% reduction)

## Current Status

Oniva is currently **60-70% complete** and production-ready for:

- AI agent execution with 5 specialized agents
- Visual workflow creation and execution
- Custom agent development
- Real-time chat interface with streaming responses
- Multi-provider LLM support
- Basic MCP connector integration
- Audit logging and session memory

**In Active Development** (see [Roadmap](/docs/release-notes/roadmap)):

- Phase 1 (Q1 2026): Compliance MVP with HITL, extended thinking, CoT monitoring
- Phase 2 (Q2 2026): Cost optimization with caching and model routing
- Phase 3 (Q3 2026): Production hardening with monitoring, testing, MCP connectors

## Getting Started

Ready to dive in? Here's your path forward:

1. **[Quick Start Guide](/docs/getting-started/quick-start)**: Get up and running in 5 minutes
2. **[Core Concepts](/docs/getting-started/core-concepts)**: Understand agents, workflows, and skills
3. **[First Workflow](/docs/getting-started/first-workflow)**: Create your first automated workflow
4. **[User Guides](/docs/user-guides)**: Role-specific guides for compliance, analysis, and development

## Support & Resources

- **Documentation**: You're reading it! Navigate using the sidebar
- **API Reference**: [/docs/api-reference](/docs/api-reference)
- **GitHub Issues**: Report bugs and request features
- **Community Forum**: Connect with other Oniva users
- **Enterprise Support**: Contact your account manager for dedicated support

## Next Steps

Choose your path based on your role:

- **Compliance Teams**: [Compliance User Guide](/docs/user-guides/compliance-teams)
- **Financial Analysts**: [Analyst User Guide](/docs/user-guides/analysts)
- **Developers**: [Developer User Guide](/docs/user-guides/developers)
- **Administrators**: [Administrator Guide](/docs/user-guides/administrators)

Or jump straight to creating your first workflow: [Quick Start →](/docs/getting-started/quick-start)
