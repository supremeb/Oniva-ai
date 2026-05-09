# AI Agents

AI agents are the intelligent specialists at the heart of Oniva. Each agent is pre-configured with domain expertise, tools (skills), and reasoning capabilities tailored for specific financial services tasks.

## Overview

Oniva provides **5 production-ready agents** plus the ability to create unlimited **custom agents** for your specific needs.

### What Makes Oniva Agents Different?

**1. Compliance-First Design**
- Extended thinking capture for decision transparency
- Immutable audit trails (7-year retention)
- Regulatory citations in responses
- HITL integration for high-risk decisions

**2. Financial Services Expertise**
- Pre-trained on financial terminology
- Understanding of regulatory frameworks (FCA, SOX, BSA/AML)
- Optimized prompts for compliance accuracy

**3. Tool-Augmented**
- Access to 85+ skills
- Can invoke external systems via MCP connectors
- Combine multiple data sources intelligently

**4. Memory-Enabled**
- Session memory (last 10 turns, <5ms recall)
- Long-term user preferences
- Entity relationship tracking

## Production Agents

### 1. Analysis Agent

**Purpose**: Financial metrics, calculations, and data analysis

**Default Model**: Claude 3.5 Sonnet

**Default Skills**:
- `execute_python`: Run calculations and data transformations
- `generate_chart`: Create visualizations
- `analyze_code`: Review financial models
- `fetch_market_data`: Get real-time market data
- `analyze_risk_metrics`: Calculate VaR, Sharpe, Beta

**Typical Use Cases**:
```
✓ Portfolio performance analysis
✓ Risk metric calculations (VaR, Sharpe ratio, Beta)
✓ Financial modeling and projections
✓ Investment research and recommendations
✓ Market trend analysis
```

**Example Interaction**:

```markdown
User: Analyze the portfolio performance for account #12345 over the last quarter

Agent Response:
I'll analyze the portfolio performance for Q4 2025. Let me gather the data:

[Uses fetch_market_data skill to retrieve holdings]
[Uses execute_python to calculate returns]
[Uses generate_chart to create visualization]

Portfolio Performance Summary (Q4 2025):
- Total Return: +12.3%
- Sharpe Ratio: 1.85
- Maximum Drawdown: -4.2%
- Volatility: 14.5%

Key Insights:
- Outperformed S&P 500 by 3.8%
- Technology sector drove 65% of gains
- Defensive positions limited downside during November correction

[Chart embedded: Performance vs benchmark over 90 days]

The portfolio demonstrates strong risk-adjusted returns with the Sharpe ratio
of 1.85 indicating favorable excess return per unit of risk.
```

**Best Practices**:
- Provide account numbers or tickers explicitly
- Specify time periods for analysis
- Request specific metrics if you have preferences
- Ask for visualizations when helpful

---

### 2. Compliance Agent

**Purpose**: Regulatory checks, verification, and compliance workflows

**Default Model**: Claude 3.5 Sonnet (will upgrade to 3.7 Sonnet for high-risk in Phase 2)

**Default Skills**:
- `kyc_verification`: Identity verification against watchlists
- `aml_screening`: Transaction pattern analysis
- `sanctions_check`: OFAC/EU sanctions screening
- `regulatory_filing`: Parse SEC/FINRA filings
- `risk_assessment`: Calculate composite risk scores
- `parse_pdf`: Extract data from compliance documents
- `search_knowledge`: Retrieve regulatory guidance

**Typical Use Cases**:
```
✓ KYC (Know Your Customer) verification
✓ AML (Anti-Money Laundering) screening
✓ OFAC sanctions list checks
✓ Suspicious Activity Report (SAR) generation
✓ Regulatory research and interpretation
✓ Compliance risk assessment
```

**Example Interaction**:

```markdown
User: Screen customer John Smith, SSN: XXX-XX-1234, for KYC and sanctions

Agent Response:
I'll perform a comprehensive compliance screening for this customer.

[Uses kyc_verification skill]
[Uses sanctions_check skill]
[Uses risk_assessment skill]

KYC/AML Screening Results:

1. Identity Verification: ✓ PASSED
   - Name matches government records
   - SSN verified with credit bureaus
   - Address confirmed

2. Sanctions Screening: ✓ CLEAR
   - OFAC SDN List: No matches
   - EU Sanctions List: No matches
   - UN Consolidated List: No matches

3. AML Risk Assessment: LOW RISK
   - Risk Score: 2.3/10
   - Geographic Risk: Low (US domestic)
   - Transaction Patterns: Normal
   - PEP Status: Not identified

4. Regulatory Flags: None

Recommendation: APPROVED for onboarding
Compliance Officer Review: Not required (risk score < 5.0)

This screening was conducted on 2026-01-06 14:23:00 UTC and logged to
audit trail #AUD-2026-00145 per BSA/AML requirements.

Regulatory Citations:
- 31 CFR Chapter X (OFAC Regulations)
- Bank Secrecy Act (BSA) 31 USC 5311
- USA PATRIOT Act Section 326
```

**Compliance Features**:
- All decisions logged to immutable audit trail
- Regulatory citations automatically included
- Risk scores calculated using standard frameworks
- Extended thinking captured for examination

**Best Practices**:
- Provide complete customer information
- Specify jurisdiction if international
- Request specific checks if you have requirements
- Always review high-risk assessments manually

---

### 3. Data Retrieval Agent

**Purpose**: Fetch data from external systems and data sources

**Default Model**: Claude 3.5 Sonnet

**Default Skills**:
- `web_search`: Search internet for current information
- `browse_url`: Fetch content from specific URLs
- `extract_structured_data`: Parse tables and lists from web pages
- `fetch_market_data`: Real-time market data
- `get_financial_statements`: Public company financials
- MCP connector skills:
  - `core_banking_*`: Core banking system integration (vendor-agnostic)
  - `ofac_*`: OFAC screening service
  - `credit_bureau_*`: Credit report retrieval

**Typical Use Cases**:
```
✓ Core banking data retrieval
✓ Market data fetching
✓ Credit report retrieval
✓ Regulatory filing access
✓ Web research and fact-finding
✓ External API integration
```

**Example Interaction**:

```markdown
User: Get the latest transaction history for account #67890

Agent Response:
I'll retrieve the transaction history from the core banking system.

[Uses MCP connector to a core banking API - Phase 3 feature]

Transaction History - Account #67890
Period: Last 30 days (2025-12-07 to 2026-01-06)

| Date | Type | Description | Amount | Balance |
|------|------|-------------|--------|---------|
| 01/06 | Debit | ACH Transfer to Wells Fargo | -$2,500.00 | $15,234.50 |
| 01/05 | Credit | Direct Deposit - Acme Corp | +$4,250.00 | $17,734.50 |
| 01/03 | Debit | Check #1234 | -$850.00 | $13,484.50 |
| 01/02 | Debit | Card Purchase - Amazon | -$127.43 | $14,334.50 |
| 12/30 | Credit | Wire Transfer | +$10,000.00 | $14,461.93 |
| ... | ... | ... | ... | ... |

Summary:
- Total Deposits: $18,750.00
- Total Withdrawals: $6,234.12
- Net Change: +$12,515.88
- Average Daily Balance: $14,892.33

Notable Transactions:
- Wire transfer of $10,000 on 12/30 (above $5K threshold)
- Regular salary deposits from Acme Corp
- Normal spending patterns observed
```

**Security Considerations**:
- All API calls logged to audit trail
- PII (Personal Identifiable Information) masked in logs
- Credentials stored encrypted
- Rate limiting enforced

---

### 4. Research Agent

**Purpose**: Document analysis, knowledge retrieval, and research

**Default Model**: Claude 3.5 Sonnet

**Default Skills**:
- `search_knowledge`: Semantic search via pgvector RAG
- `parse_pdf`: Extract text from PDF documents
- `summarize_document`: Generate concise summaries
- `extract_entities`: Extract dates, amounts, organizations
- `web_search`: Search internet for recent information
- `browse_url`: Read web content

**Typical Use Cases**:
```
✓ Policy and procedure research
✓ Regulatory guidance interpretation
✓ Historical case analysis
✓ Document summarization
✓ Competitive intelligence
✓ Market research
```

**Example Interaction**:

```markdown
User: Summarize the FCA's latest guidance on crypto asset regulation

Agent Response:
I'll search for the latest FCA guidance on crypto assets.

[Uses web_search skill]
[Uses browse_url to read FCA website]
[Uses summarize_document skill]

FCA Crypto Asset Regulation - Latest Guidance Summary

Published: October 2025
Source: FCA Policy Statement PS25/12

Key Points:

1. Registration Requirements:
   - All crypto asset firms must register by Q2 2026
   - Enhanced due diligence for stablecoin issuers
   - Custody requirements for customer assets

2. Consumer Protection:
   - Marketing restrictions on high-risk products
   - Mandatory risk warnings on all promotional materials
   - Cooling-off periods for retail investors

3. AML/CTF Requirements:
   - Travel rule implementation for crypto transfers
   - Enhanced transaction monitoring
   - Suspicious activity reporting thresholds

4. Custody Standards:
   - Segregation of customer and firm assets
   - Insurance requirements
   - Operational resilience standards

5. Effective Dates:
   - Phase 1 (Immediate): Marketing restrictions
   - Phase 2 (Q2 2026): Registration deadline
   - Phase 3 (Q4 2026): Full custody requirements

Implications for Oniva Users:
- Update KYC processes for crypto-related customers
- Implement enhanced transaction monitoring
- Review marketing materials for compliance
- Prepare for registration if offering crypto services

Full Guidance: https://www.fca.org.uk/publications/policy-statements/ps25-12

Would you like me to extract specific sections or compare this to previous guidance?
```

**RAG Knowledge Base**:
- Your organization's policies and procedures
- Regulatory documents and guidance
- Historical case files
- Training materials
- Best practices documentation

---

### 5. Custom Agents

**Purpose**: User-defined specialized workflows

Custom agents let you create specialized AI assistants for your unique business processes.

**Who Can Create Custom Agents?**
- Administrators (full access)
- Developers (via API)
- Power users (with permissions)

**How Custom Agents Work**:

1. **Define Purpose**: What specific task will this agent handle?
2. **Select Skills**: Which capabilities does it need?
3. **Configure System Prompt**: Instructions that shape behavior
4. **Choose Model**: GPT-4, Claude, or other
5. **Set Memory Options**: What context to retain
6. **Deploy**: Make available to users

**Example: Loan Officer Agent**

```yaml
name: "Loan Officer Agent"
description: "Automates loan application review and decisioning"
model: "claude-3-5-sonnet"
skills:
  - credit_bureau_credit_report  # Custom skill
  - calculate_dti_ratio  # Custom skill
  - kyc_verification
  - aml_screening
  - risk_assessment
  - generate_chart

system_prompt: |
  You are an expert loan officer reviewing applications for a community bank.

  Your role is to:
  1. Verify applicant identity and compliance (KYC/AML)
  2. Analyze creditworthiness (credit score, DTI, employment)
  3. Assess collateral value and loan-to-value ratio
  4. Calculate risk-adjusted pricing
  5. Make recommendations: Approve, Deny, or Refer to underwriting

  Decision Criteria:
  - Credit Score: Minimum 640 for approval
  - DTI Ratio: Maximum 43%
  - LTV Ratio: Maximum 80% without PMI
  - Employment: Minimum 2 years current employer

  ALWAYS:
  - Cite specific decision factors
  - Flag applications requiring human review (>$250K or risk score >7)
  - Generate detailed reasoning for denials (ECOA compliance)
  - Be empathetic yet professional

  NEVER:
  - Make discriminatory decisions
  - Ignore red flags
  - Skip required verifications
```

**Usage Example**:

```markdown
User: Review loan application #2024-12345

Loan Officer Agent Response:
I'll review application #2024-12345 for loan approval.

[Retrieves application data]
[Runs credit check]
[Verifies employment]
[Calculates DTI ratio]
[Assesses risk]

Loan Application Review
Application: #2024-12345
Applicant: Jane Doe
Requested Amount: $185,000
Purpose: Home Purchase
Property Value: $240,000

Verification Results:
✓ Identity Verification: Passed
✓ KYC/AML Screening: Clear
✓ Employment Verification: Confirmed (5 years at ABC Corp)

Financial Analysis:
- Credit Score: 742 (Good)
- Monthly Gross Income: $6,500
- Monthly Debt Payments: $1,850
- DTI Ratio: 28.5% ✓ (Within 43% limit)
- LTV Ratio: 77% ✓ (Within 80% limit)
- Debt-to-Income: Acceptable

Risk Assessment:
- Risk Score: 3.2/10 (Low Risk)
- Risk Factors: None identified
- Compensating Factors: Strong credit, stable employment

**RECOMMENDATION: APPROVE**

Proposed Terms:
- Loan Amount: $185,000
- Interest Rate: 6.75% (prime + 150bps based on risk)
- Term: 30 years
- Monthly Payment: $1,199.67
- Estimated Closing: 45 days

**Action Required**: None - automated approval
Compliance Notes: Application logged to audit trail per ECOA requirements.
Decision Date: 2026-01-06 14:45:00 UTC

This decision was made following bank policy BLP-2024-08 and federal
lending regulations (Reg B, ECOA, TILA).
```

**See Full Guide**: [Creating Custom Agents](/docs/features/custom-agents)

---

## Agent Configuration

### System Prompts

System prompts define agent behavior and decision-making criteria.

**Best Practices for System Prompts**:

1. **Define Role Clearly**
```
You are a compliance officer specializing in AML transaction monitoring.
```

2. **Specify Goals**
```
Your role is to identify suspicious patterns and flag for SAR filing when needed.
```

3. **Set Decision Criteria**
```
Flag transactions if:
- Single transaction > $10,000
- Structured deposits (multiple <$10K to avoid reporting)
- High-risk jurisdictions (per FATF list)
```

4. **Define Constraints**
```
ALWAYS:
- Cite regulatory sources (BSA, PATRIOT Act)
- Generate audit trail entries
- Escalate to human review when uncertain

NEVER:
- Make final SAR filing decision (requires human review)
- Ignore partial matches on watchlists
- Skip required verifications
```

5. **Set Tone and Style**
```
Maintain a professional, objective tone. Be thorough but concise.
Use regulatory terminology appropriately.
```

### Model Selection

Choose the right LLM for your use case:

| Model | Best For | Cost | Speed | Quality |
|-------|----------|------|-------|---------|
| **Claude 3.7 Sonnet** | High-stakes compliance | High | Slow | Excellent |
| **Claude 3.5 Sonnet** | General use (default) | Medium | Medium | Excellent |
| **GPT-4** | Complex reasoning | High | Medium | Excellent |
| **GPT-4 Turbo** | High-volume tasks | Low | Fast | Very Good |
| **DeepSeek-V3** | Cached queries | Very Low | Fast | Good |

**Routing Strategy**:
- High-risk decisions (risk score >7) → Claude 3.7 Sonnet
- Standard operations → Claude 3.5 Sonnet
- Cached/repetitive queries → DeepSeek-V3

### Memory Configuration

Control what context agents remember:

**Session Memory** (Always Enabled):
- Last 10 conversation turns
- Active entities mentioned
- Session metadata

**Long-Term Memory** (Optional):
- User preferences
- Historical patterns
- Cross-session facts

**Entity Graph**:
- Relationship tracking
- Temporal versioning
- Point-in-time queries

### Skills Assignment

Assign skills strategically:

**Minimal Set** (Faster, Cheaper):
- Only essential skills for the task
- Reduces prompt size and cost
- Faster decision-making

**Comprehensive Set** (More Capable):
- Broad range of skills available
- Agent can handle varied requests
- Higher context cost

**Recommendation**: Start minimal, add skills as needed based on usage patterns.

---

## Agent Execution Flow

Understanding how agents process requests:

```
[User Request] →
  ↓
[1. Parse Intent] →
  ↓
[2. Recall Memory] →
  ↓
[3. Select Skills] →
  ↓
[4. Plan Execution] →
  ↓
[5. Execute Steps] →
  ↓
[6. Generate Response] →
  ↓
[7. Update Memory] →
  ↓
[8. Log Audit Trail] →
  ↓
[Response to User]
```

**Average Execution Time**: 2-5 seconds

**Factors Affecting Speed**:
- Model selected (GPT-4 slower than Claude)
- Number of skills invoked
- External API latency
- Memory recall complexity
- Extended thinking enabled

---

## Extended Thinking

For high-stakes decisions, capture the agent's reasoning process.

**What is Extended Thinking?**

Extended thinking is a mode where the agent explicitly shows its reasoning before making a decision. This creates a transparent audit trail required for regulatory examination.

**When to Use Extended Thinking?**

✓ Loan approvals/denials
✓ SAR (Suspicious Activity Report) decisions
✓ Large transaction approvals
✓ Customer risk classifications
✓ Trading decisions (market abuse detection)

**How It Works**:

The agent's response includes structured reasoning:

```
Observation:
- Customer has 3 large cash deposits in 7 days
- Total: $28,500 ($9,500 + $9,800 + $9,200)
- All deposits just below $10K reporting threshold

Analysis:
- Pattern consistent with structuring (31 USC 5324)
- Deposits timed to avoid CTR filing
- Customer is self-employed contractor (some cash normal)
- No prior suspicious activity in 3-year history

Regulatory Considerations:
- Bank Secrecy Act requires SAR for suspected structuring
- Even if amounts <$10K, pattern triggers suspicion
- Customer intent unknown but pattern clear

Decision:
FILE SAR - Pattern meets structuring criteria per FinCEN guidance.
While customer may have legitimate business, pattern requires reporting.

Recommendation:
- File SAR within 30 days
- Continue monitoring account
- Do not notify customer per 31 USC 5318(g)(2)

Confidence: HIGH (pattern unambiguous)
Human Review: RECOMMENDED (verify business documentation)
```

**Benefits**:
- Regulatory examination readiness
- Decision transparency
- Training material for human reviewers
- Reduced liability

**Performance Impact**:
- +10-20% tokens (higher cost)
- +0.5-1.5 seconds latency
- Worth it for high-stakes decisions

---

## Human-in-the-Loop Integration

Route high-risk decisions to human reviewers.

**Automatic HITL Triggers**:
- Risk score above threshold (configurable, default: 7/10)
- Dollar amount exceeds limit (e.g., >$250K)
- Missing decision factors detected by CoT faithfulness monitor
- Compliance flags present
- Manual escalation requested

**HITL Workflow**:

1. **Agent Makes Decision**: Generates recommendation with reasoning
2. **Routing Logic**: Checks if HITL required
3. **Queue for Review**: Assigns to compliance officer
4. **Human Reviews**: Sees full context, agent reasoning, all data
5. **Human Decision**: Approve, Deny, or Override with explanation
6. **Workflow Resumes**: Continues with human-approved decision
7. **Audit Log**: Complete trail including human review

**Review Interface**:
- Shows agent recommendation
- Displays extended thinking
- Lists all factors considered
- Highlights missing factors (if any)
- Provides approve/reject/override buttons
- Requires reviewer notes

---

## Agent Performance Metrics

Monitor agent effectiveness:

**Accuracy Metrics**:
- Decision accuracy (vs human review)
- False positive rate (flagged but not issues)
- False negative rate (missed issues)

**Efficiency Metrics**:
- Average response time
- Tasks completed per hour
- Automation rate (% without human intervention)

**Cost Metrics**:
- Cost per request
- Token usage per decision
- Cost by agent type

**Compliance Metrics**:
- Audit trail completeness (target: 100%)
- Extended thinking capture rate
- HITL escalation rate
- Regulatory citation accuracy

---

## Best Practices

### For Compliance Officers

1. **Always Enable Extended Thinking** for high-stakes decisions
2. **Review HITL Queue Daily** to prevent backlog
3. **Calibrate Risk Thresholds** based on your risk appetite
4. **Monitor False Positives** and adjust agent prompts
5. **Export Audit Trails Quarterly** for examination readiness

### For Analysts

1. **Be Specific with Requests** (tickers, time periods, metrics)
2. **Request Visualizations** when helpful for presentations
3. **Verify Agent Calculations** for high-stakes analysis
4. **Save Useful Prompts** as custom agents
5. **Provide Feedback** on accuracy to improve agents

### For Developers

1. **Start with Production Agents** before building custom
2. **Test Custom Agents Thoroughly** before deployment
3. **Use Minimal Skill Sets** to reduce cost
4. **Monitor Token Usage** and optimize prompts
5. **Version Control System Prompts** like code

### For Administrators

1. **Monitor Cost by Agent** to identify optimization opportunities
2. **Track Automation Rates** to measure ROI
3. **Review Audit Logs Regularly** for compliance
4. **Calibrate HITL Thresholds** based on queue depth
5. **Update Models Periodically** as providers improve

---

## Next Steps

**Learn About Workflows**
→ [Workflows Guide](/docs/features/workflows): Chain agents together

**Create Custom Agents**
→ [Custom Agents Guide](/docs/features/custom-agents): Build specialized assistants

**Explore Skills**
→ [Skills Library](/docs/features/skills-library): 85+ capabilities agents can use

**Understand Architecture**
→ [Agent Execution Model](/docs/concepts/agent-execution): How agents work under the hood

**API Integration**
→ [Agent API Reference](/docs/api-reference/agents): Programmatic agent control
