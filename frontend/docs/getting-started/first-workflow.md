# Your First Workflow: Loan Approval Automation

This tutorial will guide you through creating a complete loan approval workflow from scratch. You'll learn how to use the visual workflow designer, configure nodes, test execution, and monitor results.

**What You'll Build**:
A production-ready loan approval workflow that:
- Retrieves applicant credit data
- Performs KYC/AML compliance checks
- Calculates financial ratios
- Makes approval decisions based on risk
- Routes high-risk applications to human review

**Time to Complete**: 30-45 minutes

**Prerequisites**:
- Oniva account with Workflow Designer access
- Basic understanding of [Core Concepts](/docs/getting-started/core-concepts)
- API keys configured (if using external connectors)

---

## Step 1: Access the Workflow Designer

1. Log in to Oniva
2. Navigate to **Workflows** in the left sidebar
3. Click **Create New Workflow**
4. Enter workflow name: `Loan Approval - Tutorial`
5. Add description: `Automated loan application review with compliance checks`
6. Click **Create**

You'll see the visual workflow canvas with a **Start** node already placed.

![Workflow Designer](./images/workflow-designer-empty.png)

---

## Step 2: Configure the Start Node

The Start node defines inputs your workflow will receive.

1. Click the **Start** node to open properties panel
2. Define input schema:

```json
{
  "applicant_id": "string",
  "requested_amount": "number",
  "loan_purpose": "string",
  "applicant_ssn": "string",
  "applicant_name": "string",
  "annual_income": "number"
}
```

3. Add input validation rules:
   - `requested_amount`: Minimum $1,000, Maximum $500,000
   - `annual_income`: Minimum $20,000
   - `applicant_ssn`: Format XXX-XX-XXXX

4. Click **Save**

**Best Practice**: Always validate inputs at the start to catch errors early.

---

## Step 3: Add Credit Bureau Data Retrieval

Now we'll fetch the applicant's credit report.

### Add Data Retrieval Agent Node

1. Click **Add Node** button or press `N`
2. Select **Agent** from node types
3. Choose **Data Retrieval Agent**
4. Name the node: `Fetch Credit Report`

### Configure the Node

```yaml
Agent: Data Retrieval Agent
Instructions: |
  Retrieve the credit report for applicant SSN: {{start.applicant_ssn}}

  Extract and return:
  - Credit score
  - Total debt
  - Monthly debt payments
  - Delinquencies in last 24 months
  - Credit history length (years)

Skills Enabled:
  - credit_bureau_credit_report (requires Phase 3)
  - Or use mock data for testing

Output Variables:
  credit_score: number
  total_debt: number
  monthly_debt: number
  delinquencies: number
  credit_history_years: number
```

### Connect to Start Node

1. Drag from Start node's output port
2. Drop on Credit Report node's input port
3. Green line indicates connection

**Testing Tip**: Use the **Test with Mock Data** feature to simulate credit bureau responses during development.

---

## Step 4: Add KYC/AML Compliance Check

Verify the applicant meets regulatory requirements.

### Add Compliance Agent Node

1. Add new **Agent** node
2. Select **Compliance Agent**
3. Name: `KYC/AML Screening`
4. Connect from `Fetch Credit Report` node

### Configure Compliance Check

```yaml
Agent: Compliance Agent
Instructions: |
  Perform comprehensive KYC/AML screening for:
  - Name: {{start.applicant_name}}
  - SSN: {{start.applicant_ssn}}
  - Loan Amount: ${{start.requested_amount}}

  Check:
  1. Identity verification
  2. OFAC sanctions screening
  3. PEP (Politically Exposed Person) status
  4. Transaction risk assessment

  Return structured results with pass/fail status and risk score.

Skills Enabled:
  - kyc_verification
  - aml_screening
  - sanctions_check
  - risk_assessment

Extended Thinking: Enabled
  (Captures reasoning for regulatory audit)

Output Variables:
  kyc_status: "passed" | "failed" | "review_required"
  aml_flags: array
  risk_score: number (0-10)
  compliance_notes: string
```

**Important**: Extended thinking creates an audit trail required for regulatory examination.

---

## Step 5: Calculate Financial Metrics

Use the Analysis Agent to calculate debt-to-income ratio.

### Add Analysis Agent Node

1. Add **Agent** node
2. Select **Analysis Agent**
3. Name: `Calculate DTI Ratio`
4. Connect from `KYC/AML Screening`

### Configure Financial Calculation

```yaml
Agent: Analysis Agent
Instructions: |
  Calculate the debt-to-income (DTI) ratio:

  Monthly gross income: ${{start.annual_income / 12}}
  Existing monthly debt: ${{fetch_credit_report.monthly_debt}}
  Proposed loan payment (estimate): Calculate for ${{start.requested_amount}} at 7% APR, 30-year term

  Return:
  - DTI ratio (existing debt only)
  - Projected DTI ratio (including new loan)
  - Assessment (acceptable if < 43%, high_risk if >= 43%)

Skills Enabled:
  - execute_python

Python Code:
  ```python
  import numpy as np

  # Inputs
  annual_income = {{start.annual_income}}
  monthly_income = annual_income / 12
  existing_debt = {{fetch_credit_report.monthly_debt}}
  loan_amount = {{start.requested_amount}}

  # Calculate monthly payment (7% APR, 30 years)
  rate = 0.07 / 12
  n_payments = 30 * 12
  monthly_payment = loan_amount * (rate * (1 + rate)**n_payments) / ((1 + rate)**n_payments - 1)

  # DTI calculations
  current_dti = (existing_debt / monthly_income) * 100
  projected_dti = ((existing_debt + monthly_payment) / monthly_income) * 100

  {
    "current_dti": round(current_dti, 2),
    "projected_dti": round(projected_dti, 2),
    "monthly_payment": round(monthly_payment, 2),
    "assessment": "acceptable" if projected_dti < 43 else "high_risk"
  }
  ```

Output Variables:
  current_dti: number
  projected_dti: number
  monthly_payment: number
  dti_assessment: string
```

**Pro Tip**: The Analysis Agent can execute Python code in a sandboxed environment for complex calculations.

---

## Step 6: Add Risk Assessment Logic

Create a conditional node to route based on risk.

### Add Condition Node

1. Add **Condition** node
2. Name: `Risk Evaluation`
3. Connect from `Calculate DTI Ratio`

### Configure Decision Logic

```yaml
Node Type: Condition
Name: Risk Evaluation

Conditions:
  1. Auto-Approve Path:
     Expression: |
       credit_score >= 700 AND
       projected_dti < 43 AND
       kyc_status == "passed" AND
       risk_score < 5 AND
       requested_amount <= 250000 AND
       delinquencies == 0

     Label: "Low Risk - Auto Approve"

  2. Human Review Path:
     Expression: |
       credit_score >= 640 AND
       projected_dti < 50 AND
       kyc_status != "failed" AND
       risk_score < 8

     Label: "Medium Risk - HITL Review"

  3. Auto-Deny Path:
     Expression: true (default/catch-all)
     Label: "High Risk - Auto Deny"
```

**Understanding Conditions**:
- Conditions evaluated in order (top to bottom)
- First matching condition determines route
- Use `true` as final catch-all condition

---

## Step 7: Handle Auto-Approve Path

Create the success output for approved applications.

### Add Template Node

1. Add **Template** node
2. Name: `Generate Approval Letter`
3. Connect from `Risk Evaluation` → "Low Risk" output

### Configure Approval Template

```yaml
Node Type: Template
Template Engine: Jinja2

Template:
  ```
  LOAN APPROVAL NOTICE

  Date: {{current_date}}
  Application ID: {{start.applicant_id}}

  Dear {{start.applicant_name}},

  We are pleased to inform you that your loan application has been APPROVED.

  Loan Details:
  - Approved Amount: ${{start.requested_amount | number_format}}
  - Purpose: {{start.loan_purpose}}
  - Estimated Monthly Payment: ${{calculate_dti.monthly_payment | number_format}}
  - Interest Rate: 7.00% APR (subject to final underwriting)
  - Term: 30 years

  Financial Assessment:
  - Credit Score: {{fetch_credit_report.credit_score}}
  - Debt-to-Income Ratio: {{calculate_dti.projected_dti}}%
  - Risk Rating: Low

  Next Steps:
  1. Final underwriting review (2-3 business days)
  2. Property appraisal (if applicable)
  3. Closing scheduled within 45 days

  This approval is based on information provided as of {{current_date}} and is
  subject to verification and final underwriting approval.

  Sincerely,
  Automated Lending System

  Decision ID: {{execution_id}}
  Compliance Reference: {{audit_log_id}}
  ```

Output Variables:
  decision: "approved"
  approval_letter: string
```

---

## Step 8: Handle HITL Review Path

Route medium-risk applications to human reviewers.

### Add HITL Node (Phase 1 Feature)

1. Add **Human-in-the-Loop** node
2. Name: `Loan Officer Review`
3. Connect from `Risk Evaluation` → "Medium Risk" output

### Configure HITL Review

```yaml
Node Type: HITL Review
Reviewer Role: loan_officer

Review Context:
  Applicant: {{start.applicant_name}}
  Amount: ${{start.requested_amount}}
  Credit Score: {{fetch_credit_report.credit_score}}
  DTI Ratio: {{calculate_dti.projected_dti}}%
  AML Risk: {{kyc_aml.risk_score}}/10

Review Instructions: |
  Please review this medium-risk loan application:

  AUTOMATED ASSESSMENT:
  - Credit score meets minimum (640+) but below preferred (700+)
  - DTI ratio within limits but elevated
  - Compliance checks passed

  REVIEW REQUIRED:
  - Verify income documentation
  - Assess compensating factors
  - Check credit report details
  - Confirm loan purpose aligns with property use

  Decision Options:
  - APPROVE: Meets manual underwriting standards
  - DENY: Risk factors outweigh compensating factors
  - REQUEST MORE INFO: Additional documentation needed

Approval Options:
  - Approve
  - Deny
  - Request Additional Information

Required Fields:
  - Decision (required)
  - Underwriter Notes (required, min 50 characters)
  - Conditions (optional, if approved with stipulations)

Timeout: 48 hours (escalate to manager if no response)

Output Variables:
  human_decision: "approved" | "denied" | "more_info_needed"
  underwriter_notes: string
  review_timestamp: datetime
  reviewer_id: string
```

**HITL Best Practices**:
- Provide complete context to reviewers
- Set reasonable timeouts
- Require detailed notes for audit trail
- Escalate stale reviews automatically

---

## Step 9: Handle Auto-Deny Path

Create denial notification for high-risk applications.

### Add Template Node

1. Add **Template** node
2. Name: `Generate Denial Letter`
3. Connect from `Risk Evaluation` → "High Risk" output

### Configure Denial Template

```yaml
Node Type: Template
Template Engine: Jinja2

Template:
  ```
  LOAN APPLICATION DECISION

  Date: {{current_date}}
  Application ID: {{start.applicant_id}}

  Dear {{start.applicant_name}},

  We regret to inform you that we are unable to approve your loan application
  at this time.

  Application Details:
  - Requested Amount: ${{start.requested_amount | number_format}}
  - Purpose: {{start.loan_purpose}}

  This decision was made based on the following factors (as required by ECOA):

  {% if fetch_credit_report.credit_score < 640 %}
  ⚠ Credit score below minimum requirement (640)
     Your score: {{fetch_credit_report.credit_score}}
  {% endif %}

  {% if calculate_dti.projected_dti >= 50 %}
  ⚠ Debt-to-income ratio exceeds acceptable limit (50%)
     Your projected DTI: {{calculate_dti.projected_dti}}%
  {% endif %}

  {% if fetch_credit_report.delinquencies > 0 %}
  ⚠ Recent delinquencies in credit history
     Delinquencies (24 months): {{fetch_credit_report.delinquencies}}
  {% endif %}

  {% if kyc_aml.risk_score >= 8 %}
  ⚠ Compliance risk assessment
     (AML/KYC review indicated elevated risk)
  {% endif %}

  Your Rights Under ECOA:
  - You have the right to a statement of specific reasons for this action
    within 60 days of this notice
  - You have the right to a copy of your credit report used in this decision
  - The federal Equal Credit Opportunity Act prohibits creditors from
    discriminating on the basis of race, color, religion, national origin,
    sex, marital status, age, or receipt of public assistance

  What You Can Do:
  - Review your credit report for errors
  - Work to improve your credit score
  - Reduce existing debt to improve DTI ratio
  - Reapply after addressing the factors above

  If you have questions, please contact us at lending@example.com

  Sincerely,
  Automated Lending System

  Decision ID: {{execution_id}}
  Compliance Reference: {{audit_log_id}}

  Adverse Action Notice - Equal Credit Opportunity Act
  ```

Output Variables:
  decision: "denied"
  denial_letter: string
  adverse_action_reasons: array
```

**Legal Compliance**: ECOA (Equal Credit Opportunity Act) requires specific reasons for denial. This template ensures compliance.

---

## Step 10: Add Final Output Node

Consolidate all paths into a single output.

### Add Aggregator Node

1. Add **Aggregator** node
2. Name: `Consolidate Results`
3. Connect ALL three paths (Approve, HITL, Deny) to this node

### Configure Aggregation

```yaml
Node Type: Aggregator
Aggregation Strategy: First Response

Output Schema:
  {
    "decision": "approved | denied | pending_review",
    "decision_date": "datetime",
    "applicant_id": "string",
    "requested_amount": "number",
    "credit_score": "number",
    "dti_ratio": "number",
    "risk_score": "number",
    "compliance_status": "passed | failed | review_required",
    "notification_letter": "string",
    "audit_trail_id": "string",
    "next_steps": "string"
  }
```

### Add Output Node

1. Add **Output** node
2. Name: `Workflow Complete`
3. Connect from `Consolidate Results`

```yaml
Node Type: Output
Format: JSON

Final Output:
  ```json
  {
    "workflow_id": "{{execution_id}}",
    "status": "completed",
    "decision": "{{consolidate.decision}}",
    "applicant_id": "{{start.applicant_id}}",
    "timestamp": "{{current_timestamp}}",
    "details": {{consolidate}},
    "audit_trail_url": "/compliance/audit/{{audit_log_id}}"
  }
  ```
```

---

## Step 11: Test Your Workflow

Before production deployment, thoroughly test your workflow.

### Run Test Execution

1. Click **Test Run** button in toolbar
2. Select **Mock Data** or **Custom Input**

**Test Case 1: Low-Risk Applicant (Auto-Approve)**
```json
{
  "applicant_id": "TEST-001",
  "applicant_name": "Jane Doe",
  "applicant_ssn": "123-45-6789",
  "requested_amount": 200000,
  "loan_purpose": "Home Purchase",
  "annual_income": 95000
}
```

Expected: Credit score 750, DTI 28%, Auto-Approved

**Test Case 2: Medium-Risk Applicant (HITL)**
```json
{
  "applicant_id": "TEST-002",
  "applicant_name": "John Smith",
  "applicant_ssn": "987-65-4321",
  "requested_amount": 180000,
  "loan_purpose": "Home Purchase",
  "annual_income": 62000
}
```

Expected: Credit score 680, DTI 41%, Sent to HITL Review

**Test Case 3: High-Risk Applicant (Auto-Deny)**
```json
{
  "applicant_id": "TEST-003",
  "applicant_name": "Bob Johnson",
  "applicant_ssn": "555-12-3456",
  "requested_amount": 150000,
  "loan_purpose": "Home Purchase",
  "annual_income": 45000
}
```

Expected: Credit score 590, DTI 52%, Auto-Denied

### Review Test Results

1. Click on each node to see outputs
2. Verify data flows correctly between nodes
3. Check conditional routing worked
4. Review final output format

![Test Execution View](./images/workflow-test-execution.png)

---

## Step 12: Monitor Execution

Watch your workflow execute in real-time.

### Execution View Features

**Visual Progress**:
- Green checkmark: Node completed successfully
- Blue pulse: Node currently executing
- Red X: Node failed
- Yellow warning: Node needs attention

**Execution Logs**:
```
[10:23:45] Workflow started - Loan Approval Tutorial
[10:23:45] Start node: Input validated
[10:23:46] Fetch Credit Report: Calling credit bureau API...
[10:23:48] Fetch Credit Report: Retrieved credit score 750
[10:23:48] KYC/AML Screening: Running compliance checks...
[10:23:51] KYC/AML Screening: Status PASSED, Risk score 2.3
[10:23:51] Calculate DTI Ratio: Executing Python code...
[10:23:52] Calculate DTI Ratio: DTI 28.5%, Assessment ACCEPTABLE
[10:23:52] Risk Evaluation: Condition matched - Low Risk
[10:23:53] Generate Approval Letter: Template rendered
[10:23:53] Consolidate Results: Aggregated from approval path
[10:23:53] Workflow Complete: Status SUCCESS
```

**Performance Metrics**:
- Total execution time: 8.2 seconds
- Nodes executed: 7 of 9 (conditional paths)
- Tokens used: 3,450
- Estimated cost: $0.07

---

## Step 13: Configure Error Handling

Make your workflow resilient to failures.

### Add Error Handlers

For each critical node (credit bureau, compliance):

1. Click node → **Error Handling** tab
2. Configure retry policy:

```yaml
Error Handling:
  Retry Policy:
    Max Attempts: 3
    Backoff: Exponential (1s, 2s, 4s)
    Retry On:
      - Network timeout
      - Rate limit exceeded
      - Temporary service unavailability

  Fallback Behavior:
    On Permanent Failure:
      - Log error to audit trail
      - Send to manual review queue
      - Notify: loan_ops_team@example.com

    Timeout: 30 seconds per attempt

  Circuit Breaker:
    Enabled: true
    Failure Threshold: 5 failures in 1 minute
    Reset After: 5 minutes
```

### Add Workflow-Level Error Handler

1. Click **Workflow Settings** → **Error Handling**
2. Configure global error behavior:

```yaml
Global Error Handling:
  On Unhandled Error:
    - Capture full execution state
    - Store in audit log
    - Rollback database transactions
    - Notify administrator
    - Return error response to caller

  Notification Recipients:
    - workflow_admin@example.com
    - dev_team@example.com

  Error Response Format:
    {
      "status": "error",
      "execution_id": "uuid",
      "error_message": "string",
      "failed_node": "string",
      "timestamp": "datetime",
      "support_ticket": "TICKET-ID"
    }
```

---

## Step 14: Deploy to Production

Make your workflow live.

### Pre-Deployment Checklist

✅ Test all execution paths (approve, deny, HITL)
✅ Verify error handling works
✅ Review audit trail entries
✅ Confirm HITL routing to correct reviewers
✅ Validate output format matches API contract
✅ Check compliance requirements met
✅ Load test with expected volume (if applicable)
✅ Document workflow for operations team

### Deployment Process

1. Click **Deploy** button
2. Select environment: **Production**
3. Add deployment notes:
   ```
   Version: 1.0.0
   Changes: Initial production deployment
   Testing: All paths verified with 15 test cases
   Approver: John Doe (Compliance Officer)
   ```

4. Set deployment schedule:
   - **Immediate**: Deploy now
   - **Scheduled**: Deploy at specific time
   - **Gradual Rollout**: 10% → 50% → 100% over 1 week

5. Click **Deploy**

**Post-Deployment**:
- Monitor for first 100 executions
- Review error rates and latency
- Gather feedback from HITL reviewers
- Track automation rate

---

## Step 15: Integrate via API

Call your workflow programmatically.

### REST API Integration

**Endpoint**: `POST /api/v1/workflows/{workflow_id}/execute`

**Authentication**: Bearer token in `Authorization` header

**Example: cURL**
```bash
curl -X POST https://api.oniva.com/api/v1/workflows/wf_loan_approval_123/execute \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "applicant_id": "APP-2026-00145",
    "applicant_name": "Jane Doe",
    "applicant_ssn": "123-45-6789",
    "requested_amount": 250000,
    "loan_purpose": "Home Purchase",
    "annual_income": 95000
  }'
```

**Example: Python**
```python
import requests

url = "https://api.oniva.com/api/v1/workflows/wf_loan_approval_123/execute"
headers = {
    "Authorization": "Bearer YOUR_API_KEY",
    "Content-Type": "application/json"
}
payload = {
    "applicant_id": "APP-2026-00145",
    "applicant_name": "Jane Doe",
    "applicant_ssn": "123-45-6789",
    "requested_amount": 250000,
    "loan_purpose": "Home Purchase",
    "annual_income": 95000
}

response = requests.post(url, json=payload, headers=headers)
result = response.json()

print(f"Decision: {result['decision']}")
print(f"Execution ID: {result['workflow_id']}")
```

**Example: TypeScript**
```typescript
const executeWorkflow = async (applicantData: ApplicantInput) => {
  const response = await fetch(
    'https://api.oniva.com/api/v1/workflows/wf_loan_approval_123/execute',
    {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${process.env.ONIVA_API_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(applicantData)
    }
  );

  const result = await response.json();

  return {
    decision: result.decision,
    executionId: result.workflow_id,
    details: result.details
  };
};
```

### Webhook Integration

Receive notifications when workflow completes:

1. Go to **Workflow Settings** → **Webhooks**
2. Add webhook URL: `https://your-app.com/webhooks/loan-decision`
3. Select events:
   - Workflow Completed
   - Workflow Failed
   - HITL Review Required

**Webhook Payload**:
```json
{
  "event": "workflow.completed",
  "workflow_id": "wf_loan_approval_123",
  "execution_id": "exec_abc123xyz",
  "timestamp": "2026-01-06T14:23:45Z",
  "status": "success",
  "result": {
    "decision": "approved",
    "applicant_id": "APP-2026-00145",
    "credit_score": 750,
    "dti_ratio": 28.5,
    "notification_letter": "LOAN APPROVAL NOTICE..."
  }
}
```

---

## Common Patterns & Best Practices

### Pattern 1: Conditional Routing Based on Business Rules

```yaml
# Multiple business rules evaluated in sequence
Condition Node: Risk Assessment
  Rule 1: VIP Customer Fast Track
    Expression: customer.vip_status == true AND requested_amount < 100000
    Route: Auto-Approve (VIP)

  Rule 2: Standard Low Risk
    Expression: credit_score >= 700 AND dti < 43
    Route: Auto-Approve (Standard)

  Rule 3: Medium Risk Manual Review
    Expression: credit_score >= 640 AND dti < 50
    Route: HITL Review

  Rule 4: Catch-all Deny
    Expression: true
    Route: Auto-Deny
```

### Pattern 2: Parallel Processing for Speed

```yaml
# Execute independent checks in parallel
After "Start" node, branch to 3 parallel paths:

  Branch 1: Credit Bureau Check (2-3 seconds)
  Branch 2: KYC/AML Screening (3-4 seconds)
  Branch 3: Employment Verification (2-3 seconds)

Then: Aggregator Node waits for all 3 to complete
Result: 4 seconds total vs 9 seconds sequential
```

### Pattern 3: Iterative Processing

```yaml
# Process multiple items (batch screening)
Iterator Node: Screen Transaction Batch
  Input: Array of transactions from previous node
  Loop Variable: transaction

  Loop Body:
    1. AML Screening (transaction)
    2. Risk Assessment (transaction)
    3. Store Result

  Aggregation: Collect all results into array

  Output: {
    "total_transactions": 50,
    "flagged_count": 3,
    "flagged_transactions": [...]
  }
```

### Pattern 4: Graceful Degradation

```yaml
# Handle service outages gracefully
Credit Bureau Node:
  Primary: Credit Bureau API

  Error Handler:
    On Service Unavailable:
      - Log error
      - Switch to: Alternative Credit Scorer
      - If also fails: Use historical credit data
      - Last resort: Route to manual review

    Notification:
      - Alert: ops_team@example.com
      - Severity: Warning (not critical)
```

---

## Monitoring & Optimization

### Key Metrics to Track

**Performance Metrics**:
- Average execution time: Target <10 seconds
- P95 execution time: Target <15 seconds
- Error rate: Target <1%
- Timeout rate: Target <0.5%

**Business Metrics**:
- Automation rate: Applications auto-processed without human intervention
- HITL queue depth: Applications awaiting review
- Average review time: Time from HITL queue to decision
- Decision distribution: % Approved / Denied / Review

**Cost Metrics**:
- Cost per execution
- Token usage per workflow run
- Most expensive nodes (optimization targets)

### Optimization Strategies

**1. Reduce Token Usage**
```yaml
# Before: Full credit report in prompt (5,000 tokens)
Instructions: Use complete credit report: {{credit_report}}

# After: Extract only needed fields (500 tokens)
Instructions: |
  Credit Score: {{credit_report.score}}
  Monthly Debt: {{credit_report.monthly_debt}}
  Delinquencies: {{credit_report.delinquencies_24mo}}
```

**2. Use Caching**
```yaml
# Cache stable data like applicant info
Agent Node Settings:
  Enable Prompt Caching: true
  Cache Breakpoints:
    - After system prompt
    - After skill definitions
    - Before dynamic applicant data

  Expected Savings: 80-90% token reduction
```

**3. Parallel Execution**
```yaml
# Replace sequential with parallel where possible
# Sequential: 12 seconds total
Start → Credit (3s) → KYC (4s) → Employment (3s) → Decision (2s)

# Parallel: 7 seconds total
Start → [Credit (3s) | KYC (4s) | Employment (3s)] → Aggregator → Decision (2s)
```

**4. Model Routing**
```yaml
# Use cheaper models for simple tasks
Low-Risk Path (80% of volume):
  Model: DeepSeek-V3
  Cost: $0.01 per execution

High-Risk Path (20% of volume):
  Model: Claude 3.7 Sonnet
  Cost: $0.08 per execution

Average Cost: 0.8 * 0.01 + 0.2 * 0.08 = $0.024 per execution
vs All Claude: $0.08 per execution
Savings: 70%
```

---

## Troubleshooting

### Common Issues

**Issue 1: Workflow Times Out**
```
Symptom: Execution exceeds 2-minute timeout
Cause: External API (credit bureau) slow to respond

Solution:
1. Add timeout to API call node (30 seconds)
2. Implement retry with exponential backoff
3. Add fallback to cached data if available
4. Consider async pattern with webhooks for slow APIs
```

**Issue 2: HITL Queue Overflows**
```
Symptom: 100+ applications in review queue
Cause: Risk thresholds too strict, routing too many to HITL

Solution:
1. Analyze HITL approval rate (if >90% approved, loosen threshold)
2. Adjust risk score threshold: 7 → 8 (fewer HITL routes)
3. Train additional reviewers
4. Implement auto-escalation for stale reviews (>48 hours)
```

**Issue 3: Inconsistent Decisions**
```
Symptom: Similar applications get different decisions
Cause: Agent not deterministic, using conversation history inconsistently

Solution:
1. Ensure all decision factors explicitly in prompt
2. Disable session memory for workflows (use only workflow variables)
3. Set model temperature to 0 (deterministic)
4. Add extensive logging to compare decision paths
```

**Issue 4: High Cost**
```
Symptom: $500/month for 1,000 executions
Cause: Using GPT-4 for all steps, large context windows

Solution:
1. Switch to Claude 3.5 Sonnet (40% cheaper)
2. Enable prompt caching (80% savings)
3. Use model routing (cheaper models for low-risk)
4. Reduce context size (only essential data in prompts)
Target: $100/month for 1,000 executions
```

---

## Next Steps

Congratulations! You've built a complete loan approval workflow.

**Expand Your Workflow**:
- Add property appraisal integration
- Include income verification
- Add fraud detection checks
- Implement decision explanations (ECOA compliance)

**Learn Advanced Topics**:
- [Workflow Orchestration](/docs/concepts/workflow-orchestration): Deep dive into DBOS engine
- [Custom Agents](/docs/features/custom-agents): Build specialized loan officer agent
- [API Integration](/docs/api-reference/workflows-api): Programmatic workflow control
- [Cost Optimization](/docs/concepts/cost-optimization): Reduce expenses with caching

**Explore More Workflows**:
- [KYC/AML Workflow](/docs/how-to/kyc-aml-check): Customer onboarding automation
- [SAR Filing Workflow](/docs/how-to/automate-regulatory-reporting): Suspicious activity reporting
- [Portfolio Analysis](/docs/how-to/analyze-portfolio): Investment analysis automation

**Get Help**:
- [Troubleshooting Guide](/docs/troubleshooting/common-issues)
- [Community Forum](https://community.oniva.com)
- [Support](mailto:support@oniva.com)
