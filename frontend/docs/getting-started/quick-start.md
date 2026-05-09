# Quick Start Guide

Get started with Oniva in 5 minutes. This guide will walk you through your first agent interaction and workflow creation.

## Prerequisites

Before you begin, ensure you have:

- Access to Oniva platform (contact your administrator for credentials)
- At least one LLM provider API key (OpenAI, Anthropic, or OpenRouter)
- Modern web browser (Chrome, Firefox, Safari, or Edge)

## Step 1: Configure Your LLM Provider (2 minutes)

Oniva requires an LLM provider to power AI agents. You'll need to add your API key.

### Add Your API Key

1. Navigate to **Settings** → **API Keys** from the sidebar
2. Click **Add New API Key**
3. Select your provider:
   - **OpenAI**: For GPT-4 and embeddings
   - **Anthropic**: For Claude 3.x models
   - **OpenRouter**: For multi-model access
4. Enter your API key
5. Click **Save** (keys are encrypted before storage)

**Security Note**: API keys are encrypted using Fernet encryption with PBKDF2 key derivation and never logged or exposed in responses.

### Verify Configuration

The system will automatically test your API key. You should see a green checkmark indicating successful validation.

## Step 2: Chat with Your First Agent (1 minute)

Let's try the simplest interaction: chatting with an AI agent.

### Start a Chat Session

1. Click **Chat** in the sidebar
2. The Analysis Agent is selected by default
3. Type a question:

```
What are the key components of a KYC verification process?
```

4. Press **Send** or hit `Enter`

### What to Expect

You'll see:
- **Streaming Response**: The agent's answer appears in real-time
- **Thinking Process** (if extended thinking enabled): See the agent's reasoning
- **Session Memory**: The agent remembers context across the conversation

### Try a Follow-up Question

```
How does OFAC screening fit into this process?
```

The agent will reference your previous question, demonstrating session memory.

## Step 3: Create Your First Workflow (2 minutes)

Workflows let you chain multiple agents and skills together for complex tasks.

### Example: Portfolio Analysis Workflow

Let's create a workflow that fetches market data and analyzes it.

#### Open Workflow Builder

1. Click **Workflows** in the sidebar
2. Click **Create New Workflow**
3. Give it a name: "Portfolio Analysis"

#### Add Nodes

**1. Start Node** (automatically created)
- This is where your workflow begins
- No configuration needed

**2. Add Data Retrieval Agent**
- Drag **Agent Node** from the palette
- Configure:
  - **Agent**: Data Retrieval Agent
  - **Instruction**: "Fetch current price and volume for AAPL stock"
- Connect from **Start** to this node

**3. Add Analysis Agent**
- Drag another **Agent Node**
- Configure:
  - **Agent**: Analysis Agent
  - **Instruction**: "Analyze the market data and provide a brief summary"
  - **Context Variable**: Use output from previous node
- Connect from **Data Retrieval** to this node

**4. Add Output Node**
- Drag **Output Node** from palette
- Configure:
  - **Format**: Markdown
  - **Template**: "{{result}}"
- Connect from **Analysis Agent** to **Output**

#### Save and Execute

1. Click **Save Workflow**
2. Click **Run**
3. Watch the execution in real-time:
   - Each node lights up as it executes
   - See intermediate results
   - View final output

### What You Built

You just created a two-agent workflow that:
1. Fetches real-time market data (Data Retrieval Agent)
2. Analyzes that data (Analysis Agent)
3. Formats the result

**Execution Time**: ~3-5 seconds

## Understanding What Happened

### Session Memory

Your chat conversation is stored in Redis with:
- Last 10 turns immediately accessible (<5ms)
- Full conversation history archived to PostgreSQL
- 24-hour session expiration

### Workflow Execution

Your workflow ran through DBOS orchestrator which:
- Executed each node in sequence
- Passed data between agents
- Logged execution to audit trail
- Handled errors gracefully

### Cost Tracking

Each interaction is tracked:
- Tokens used: ~1,500 (prompt + completion)
- Cost: ~$0.03 (varies by provider)
- View detailed costs in Settings → Analytics

## Next Steps

Now that you've completed your first interactions, here's where to go next:

### Learn Core Concepts

Understand the fundamental building blocks:
- [Core Concepts →](/docs/getting-started/core-concepts)

### Explore More Workflows

Try these example workflows:
- [KYC/AML Verification](/docs/how-to/kyc-aml-checks)
- [Loan Application Processing](/docs/how-to/loan-approval)
- [Suspicious Activity Detection](/docs/how-to/ofac-screening)

### Create a Custom Agent

Build an agent specialized for your needs:
- [Custom Agent Guide](/docs/features/custom-agents)
- [Skills Library Reference](/docs/features/skills-library)

### Connect External Systems

Integrate with your existing infrastructure:
- [MCP Connectors Guide](/docs/features/connectors)
- [API Integration](/docs/api-reference/authentication)

## Common Next Questions

**Q: Can I use multiple AI providers in one workflow?**

Yes! Each agent node can specify a different provider. For example:
- Use GPT-4 for complex reasoning
- Use Claude for compliance tasks
- Use DeepSeek for high-volume, cached queries

**Q: How do I share workflows with my team?**

Workflows are stored per user account. To share:
1. Export workflow as JSON (Export button)
2. Share file with team member
3. They import via Import button

Enterprise teams get shared workspace (coming in Phase 3).

**Q: What happens if an agent fails?**

Workflows have built-in error handling:
- Automatic retries (3 attempts with exponential backoff)
- Error nodes for graceful degradation
- Execution logs for debugging
- Detailed error messages in workflow viewer

**Q: Can I schedule workflows to run automatically?**

Not yet. Scheduled execution is planned for Phase 3. Currently, workflows are:
- Manually triggered via UI
- Triggered via API calls
- Embedded in other workflows

## Troubleshooting

### "Invalid API Key" Error

**Solution**:
1. Verify your API key is active with your provider
2. Check for extra spaces or characters
3. Ensure you selected the correct provider

### "Agent Not Responding"

**Solution**:
1. Check your internet connection
2. Verify LLM provider status (status.openai.com)
3. Try switching to a different provider
4. Contact administrator if issue persists

### "Workflow Failed to Execute"

**Solution**:
1. Check workflow execution logs (click on failed node)
2. Verify all nodes are properly connected
3. Ensure required skills are enabled for agents
4. Review error message for specific guidance

## Getting Help

If you're stuck:

- **Documentation**: Search in the sidebar
- **FAQ**: [Common questions](/docs/troubleshooting/faq)
- **Error Reference**: [Error messages explained](/docs/troubleshooting/error-messages)
- **Support**: Contact your administrator or open a ticket

## What's Next?

You've successfully:
- ✓ Configured your first LLM provider
- ✓ Chatted with an AI agent
- ✓ Created and executed a workflow

**Ready to dive deeper?**

- [Core Concepts](/docs/getting-started/core-concepts): Learn agents, workflows, and skills
- [Your First Workflow](/docs/getting-started/first-workflow): Build a complete compliance workflow
- [User Guides](/docs/user-guides): Role-specific guides for your use case

**Continue to Core Concepts →**
