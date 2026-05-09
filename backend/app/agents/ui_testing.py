"""
UI Testing Agent

A specialized agent for demonstrating and testing AI Chat UI components.
This agent can generate various message part types on command for testing
the frontend component rendering.

Usage via chat:
- "show text streaming" - Demonstrates StreamingText
- "show code block" - Demonstrates StreamingCode with syntax highlighting
- "show tool call" - Demonstrates ToolCallCard with progress steps
- "show question" - Demonstrates AgentQuestion with options
- "show approval" - Demonstrates ApprovalCard
- "show table" - Demonstrates DataTable
- "show chart" - Demonstrates ChartPreview
- "show sql" - Demonstrates SQLPreview and SQLResultTable
- "show json" - Demonstrates JSONViewer
- "show diff" - Demonstrates DiffViewer
- "show timeline" - Demonstrates Timeline
- "show metrics" - Demonstrates MetricCard grid
- "show all" - Demonstrates all components in sequence
- "showcase" - Full component showcase with narrative

This agent is intended for development/testing and can be disabled via feature flag.
"""

import uuid
import asyncio
from typing import AsyncGenerator, Optional

from app.schemas.message_parts import (
    ToolCallStatus,
    RiskLevel,
    ChartType,
    SourceType,
    SqlStatus,
    ThinkingVariant,
    StepStatus,
    MetricTrend,
    FormPart,
    FormFieldPart,
)
from app.services.sse_builder import SSEBuilder, create_sse_builder


class UITestingAgent:
    """
    Agent for testing and demonstrating UI components.

    This agent interprets commands and generates appropriate message parts
    to showcase the frontend chat components.
    """

    def __init__(self):
        self.model = "ui-testing-agent"
        self._builder: Optional[SSEBuilder] = None

    def _get_builder(self) -> SSEBuilder:
        """Get or create SSE builder."""
        if self._builder is None:
            self._builder = create_sse_builder()
        return self._builder

    def reset_builder(self):
        """Reset the builder for a new stream."""
        self._builder = create_sse_builder()

    async def stream_demo(
        self,
        command: str,
    ) -> AsyncGenerator[dict, None]:
        """
        Stream demo content based on command.

        Args:
            command: User command specifying what to demonstrate

        Yields:
            SSE events for the requested demo
        """
        self.reset_builder()
        builder = self._get_builder()
        cmd = command.lower().strip()

        # Parse command
        if "text" in cmd or cmd == "hello":
            async for event in self._demo_text_streaming(builder):
                yield event

        elif "code" in cmd:
            async for event in self._demo_code_block(builder):
                yield event

        elif "tool" in cmd:
            async for event in self._demo_tool_call(builder):
                yield event

        elif "question" in cmd or "ask" in cmd:
            async for event in self._demo_question(builder):
                yield event

        elif "approval" in cmd or "approve" in cmd:
            async for event in self._demo_approval(builder):
                yield event

        elif "table" in cmd or "data" in cmd:
            async for event in self._demo_table(builder):
                yield event

        elif "chart" in cmd or "graph" in cmd:
            async for event in self._demo_chart(builder):
                yield event

        elif "sql" in cmd or "query" in cmd:
            async for event in self._demo_sql(builder):
                yield event

        elif "json" in cmd:
            async for event in self._demo_json(builder):
                yield event

        elif "diff" in cmd or "change" in cmd:
            async for event in self._demo_diff(builder):
                yield event

        elif "timeline" in cmd or "history" in cmd:
            async for event in self._demo_timeline(builder):
                yield event

        elif "metric" in cmd or "kpi" in cmd:
            async for event in self._demo_metrics(builder):
                yield event

        elif "status" in cmd or "alert" in cmd:
            async for event in self._demo_status_messages(builder):
                yield event

        elif "file" in cmd or "download" in cmd:
            async for event in self._demo_file_preview(builder):
                yield event

        elif "link" in cmd or "url" in cmd:
            async for event in self._demo_link_preview(builder):
                yield event

        elif "source" in cmd or "cite" in cmd:
            async for event in self._demo_sources(builder):
                yield event

        elif "form" in cmd or "input" in cmd:
            async for event in self._demo_form(builder):
                yield event

        elif "step" in cmd or "progress" in cmd:
            async for event in self._demo_steps(builder):
                yield event

        elif "showcase" in cmd or "all" in cmd:
            async for event in self._full_showcase(builder):
                yield event

        else:
            # Show help
            async for event in self._show_help(builder):
                yield event

        # Signal completion
        yield builder.done(
            message_id=str(uuid.uuid4()),
            model=self.model,
        )

    # =========================================================================
    # DEMO GENERATORS
    # =========================================================================

    async def _demo_text_streaming(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo streaming text with markdown."""
        yield builder.thinking(message="Preparing text demo...", variant=ThinkingVariant.DEFAULT)
        await asyncio.sleep(0.3)

        text_content = """# Welcome to the AI Chat UI Demo

This demonstrates **streaming text** with full Markdown support.

## Features

- **Bold** and *italic* text
- `Inline code` formatting
- [Links](https://example.com) and references

> Blockquotes for important callouts

1. Numbered lists
2. With multiple items
3. Rendered beautifully

### Key Benefits

The streaming text component supports:
- Real-time character-by-character rendering
- Proper markdown parsing
- Cursor animation while streaming
- Dark mode support

*Thank you for testing!*
"""
        # Stream as text deltas
        for i in range(0, len(text_content), 15):
            yield builder.text_delta(text_content[i:i+15])
            await asyncio.sleep(0.02)

    async def _demo_code_block(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo code block with syntax highlighting."""
        yield builder.thinking(message="Generating code example...")
        await asyncio.sleep(0.2)

        # Python example
        python_code = '''def analyze_portfolio(holdings: list[dict]) -> dict:
    """Analyze a portfolio of financial holdings."""
    total_value = 0
    allocations = {}

    for holding in holdings:
        ticker = holding["ticker"]
        shares = holding["shares"]
        price = holding["current_price"]
        value = shares * price

        total_value += value
        allocations[ticker] = value

    # Calculate percentages
    for ticker in allocations:
        allocations[ticker] = {
            "value": allocations[ticker],
            "percentage": (allocations[ticker] / total_value) * 100
        }

    return {
        "total_value": total_value,
        "allocations": allocations,
        "diversification_score": calculate_diversification(allocations)
    }'''

        yield builder.code_block(
            code=python_code,
            language="python",
            filename="portfolio_analyzer.py"
        )
        await asyncio.sleep(0.3)

        # TypeScript example
        ts_code = '''interface MessagePart {
  type: string;
  content?: string;
  metadata?: Record<string, unknown>;
}

export function renderMessagePart(part: MessagePart): React.ReactNode {
  switch (part.type) {
    case 'text':
      return <StreamingText content={part.content} />;
    case 'code':
      return <StreamingCode code={part.content} language={part.metadata?.language} />;
    default:
      return null;
  }
}'''

        yield builder.code_block(
            code=ts_code,
            language="typescript",
            filename="MessageRenderer.tsx"
        )

    async def _demo_tool_call(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo tool call with progress steps."""
        tool_id = f"tool-{uuid.uuid4().hex[:8]}"

        yield builder.thinking(message="Executing tool demonstration...")
        await asyncio.sleep(0.2)

        # Start tool call
        yield builder.tool_call_start(
            tool_call_id=tool_id,
            tool_name="web_search",
            args={"query": "AI agent frameworks 2026", "max_results": 5}
        )
        await asyncio.sleep(0.4)

        # Progress steps
        steps = [
            ("Parsing query...", "parse"),
            ("Searching web...", "search"),
            ("Ranking results...", "rank"),
            ("Extracting content...", "extract"),
            ("Summarizing...", "summarize"),
        ]

        for label, step_id in steps:
            yield builder.tool_call_progress(
                tool_call_id=tool_id,
                step_id=step_id,
                label=label,
                status=StepStatus.ACTIVE
            )
            await asyncio.sleep(0.5)

        # Complete with result
        yield builder.tool_call_complete(
            tool_call_id=tool_id,
            status=ToolCallStatus.SUCCESS,
            result={
                "results": [
                    {"title": "Pydantic AI Framework", "url": "https://pydantic.ai"},
                    {"title": "LangChain 2.0", "url": "https://langchain.com"},
                    {"title": "AutoGPT Enterprise", "url": "https://autogpt.com"},
                ]
            },
            duration_ms=2456
        )

    async def _demo_question(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo agent question with options."""
        yield builder.text_delta("I need your input to proceed.\n\n")
        await asyncio.sleep(0.2)

        yield builder.question(
            question_id=f"q-{uuid.uuid4().hex[:8]}",
            header="Risk Tolerance",
            question_text="What is your preferred risk level for this analysis?",
            options=[
                {"id": "conservative", "label": "Conservative", "description": "Lower risk, more stable returns"},
                {"id": "moderate", "label": "Moderate", "description": "Balanced risk and return"},
                {"id": "aggressive", "label": "Aggressive", "description": "Higher risk, potential for greater returns"},
                {"id": "custom", "label": "Custom", "description": "Define your own parameters"},
            ],
            multi_select=False,
            timeout=60,
            default_option_id="moderate"
        )

    async def _demo_approval(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo approval card for human-in-the-loop."""
        yield builder.text_delta("This action requires your approval:\n\n")
        await asyncio.sleep(0.2)

        yield builder.approval(
            approval_id=f"apr-{uuid.uuid4().hex[:8]}",
            title="Execute Trade Order",
            description="The following trade will be executed on your behalf. Please review and confirm.",
            risk_level=RiskLevel.HIGH,
            items=[
                {"label": "Action", "value": "BUY", "sensitive": False},
                {"label": "Symbol", "value": "AAPL", "sensitive": False},
                {"label": "Quantity", "value": "100 shares", "sensitive": False},
                {"label": "Price", "value": "$185.50", "sensitive": False},
                {"label": "Total Value", "value": "$18,550.00", "sensitive": False},
                {"label": "Account", "value": "****4521", "sensitive": True},
            ]
        )

    async def _demo_table(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo data table component."""
        yield builder.thinking(message="Loading market data...")
        await asyncio.sleep(0.3)

        yield builder.table(
            columns=[
                {"key": "symbol", "header": "Symbol"},
                {"key": "name", "header": "Company Name"},
                {"key": "price", "header": "Price"},
                {"key": "change", "header": "Change"},
                {"key": "volume", "header": "Volume"},
                {"key": "marketCap", "header": "Market Cap"},
            ],
            data=[
                {"symbol": "AAPL", "name": "Apple Inc.", "price": "$185.50", "change": "+2.3%", "volume": "52.4M", "marketCap": "$2.89T"},
                {"symbol": "MSFT", "name": "Microsoft Corp.", "price": "$378.92", "change": "+1.8%", "volume": "28.1M", "marketCap": "$2.81T"},
                {"symbol": "GOOGL", "name": "Alphabet Inc.", "price": "$142.65", "change": "-0.5%", "volume": "18.7M", "marketCap": "$1.79T"},
                {"symbol": "AMZN", "name": "Amazon.com Inc.", "price": "$178.25", "change": "+3.2%", "volume": "45.2M", "marketCap": "$1.85T"},
                {"symbol": "NVDA", "name": "NVIDIA Corp.", "price": "$875.28", "change": "+5.1%", "volume": "62.8M", "marketCap": "$2.16T"},
                {"symbol": "META", "name": "Meta Platforms", "price": "$485.72", "change": "+1.2%", "volume": "15.3M", "marketCap": "$1.24T"},
            ],
            title="Top Tech Stocks - Real-time Data",
            sortable=True,
            searchable=True
        )

    async def _demo_chart(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo chart preview component."""
        yield builder.thinking(message="Generating chart...")
        await asyncio.sleep(0.2)

        yield builder.chart(
            chart_type=ChartType.LINE,
            title="Portfolio Performance (YTD)",
            description="Showing cumulative returns compared to S&P 500 benchmark",
            svg_content="""<svg viewBox="0 0 400 200" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="grad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" style="stop-color:#3b82f6;stop-opacity:0.3"/>
      <stop offset="100%" style="stop-color:#3b82f6;stop-opacity:0"/>
    </linearGradient>
  </defs>
  <rect fill="#f8fafc" width="400" height="200" rx="8"/>
  <path d="M 40 150 Q 100 140, 150 120 T 250 80 T 360 40" stroke="#3b82f6" fill="none" stroke-width="2"/>
  <path d="M 40 150 Q 100 140, 150 120 T 250 80 T 360 40 L 360 180 L 40 180 Z" fill="url(#grad)"/>
  <path d="M 40 150 Q 100 145, 150 135 T 250 110 T 360 90" stroke="#94a3b8" fill="none" stroke-width="1.5" stroke-dasharray="4"/>
  <text x="45" y="190" font-size="10" fill="#64748b">Jan</text>
  <text x="195" y="190" font-size="10" fill="#64748b">Jun</text>
  <text x="350" y="190" font-size="10" fill="#64748b">Dec</text>
</svg>"""
        )

    async def _demo_sql(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo SQL query and results."""
        yield builder.thinking(message="Executing SQL query...")
        await asyncio.sleep(0.2)

        # Show query
        yield builder.sql_query(
            query="""SELECT
    t.symbol,
    t.transaction_date,
    t.type,
    t.shares,
    t.price_per_share,
    (t.shares * t.price_per_share) AS total_value
FROM transactions t
WHERE t.account_id = 'ACC-12345'
    AND t.transaction_date >= '2026-01-01'
ORDER BY t.transaction_date DESC
LIMIT 10;""",
            database="portfolio_db",
            status=SqlStatus.RUNNING
        )
        await asyncio.sleep(0.8)

        # Show results
        yield builder.sql_result(
            columns=[
                {"name": "symbol", "type": "varchar"},
                {"name": "transaction_date", "type": "date"},
                {"name": "type", "type": "varchar"},
                {"name": "shares", "type": "integer"},
                {"name": "price_per_share", "type": "decimal"},
                {"name": "total_value", "type": "decimal"},
            ],
            rows=[
                ["AAPL", "2026-01-28", "BUY", 50, 184.25, 9212.50],
                ["MSFT", "2026-01-25", "BUY", 25, 375.80, 9395.00],
                ["NVDA", "2026-01-22", "SELL", 10, 865.50, 8655.00],
                ["GOOGL", "2026-01-18", "BUY", 40, 141.20, 5648.00],
                ["AMZN", "2026-01-15", "BUY", 30, 175.45, 5263.50],
            ],
            execution_time=42,
            total_rows=5
        )

    async def _demo_json(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo JSON viewer component."""
        yield builder.text_delta("Here's the API response:\n\n")
        await asyncio.sleep(0.2)

        yield builder.json_data(
            data={
                "status": "success",
                "data": {
                    "portfolio": {
                        "id": "PF-12345",
                        "name": "Growth Portfolio",
                        "created_at": "2025-06-15T10:30:00Z",
                        "holdings": [
                            {"symbol": "AAPL", "shares": 100, "avg_cost": 175.50},
                            {"symbol": "MSFT", "shares": 50, "avg_cost": 350.25},
                            {"symbol": "NVDA", "shares": 25, "avg_cost": 750.00},
                        ],
                        "metrics": {
                            "total_value": 85250.00,
                            "daily_change": 1250.50,
                            "daily_change_pct": 1.49,
                            "ytd_return": 12.5,
                        }
                    }
                },
                "metadata": {
                    "request_id": "req-abc123",
                    "timestamp": "2026-02-01T14:30:00Z",
                    "api_version": "2.0"
                }
            },
            title="Portfolio API Response",
            collapsed_depth=2
        )

    async def _demo_diff(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo diff viewer component."""
        yield builder.text_delta("Code changes detected:\n\n")
        await asyncio.sleep(0.2)

        yield builder.diff(
            diff_text="""@@ -15,8 +15,12 @@ def calculate_returns(portfolio):
     total_value = 0
     daily_returns = []

-    for holding in portfolio.holdings:
-        value = holding.shares * holding.current_price
+    # Optimized calculation with caching
+    for holding in portfolio.holdings:
+        cached_price = price_cache.get(holding.symbol)
+        if cached_price is None:
+            cached_price = fetch_current_price(holding.symbol)
+            price_cache.set(holding.symbol, cached_price, ttl=60)
+        value = holding.shares * cached_price
         total_value += value

     return total_value""",
            title="Performance Optimization",
            old_filename="portfolio.py",
            new_filename="portfolio.py"
        )

    async def _demo_timeline(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo timeline component."""
        yield builder.text_delta("Workflow execution history:\n\n")
        await asyncio.sleep(0.2)

        yield builder.timeline(events=[
            {
                "title": "Workflow Started",
                "description": "User initiated portfolio analysis workflow",
                "status": "completed",
                "timestamp": "2026-02-01T14:00:00Z"
            },
            {
                "title": "Data Collection",
                "description": "Retrieved market data from 3 sources",
                "status": "completed",
                "timestamp": "2026-02-01T14:00:15Z"
            },
            {
                "title": "Risk Analysis",
                "description": "Calculating VaR and stress test scenarios",
                "status": "in_progress",
                "timestamp": "2026-02-01T14:00:30Z"
            },
            {
                "title": "Report Generation",
                "description": "Compile final analysis report",
                "status": "pending",
                "timestamp": None
            },
            {
                "title": "Delivery",
                "description": "Send report to stakeholders",
                "status": "pending",
                "timestamp": None
            },
        ])

    async def _demo_metrics(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo metric cards."""
        yield builder.thinking(message="Loading key metrics...")
        await asyncio.sleep(0.3)

        metrics = [
            ("Portfolio Value", 1285750.00, 2.5, MetricTrend.UP, "$", None, "as of close"),
            ("Daily P&L", 31250.00, 2.5, MetricTrend.UP, "$", None, None),
            ("YTD Return", 15.8, 3.2, MetricTrend.UP, None, "%", "vs benchmark"),
            ("Sharpe Ratio", 1.85, 0.15, MetricTrend.UP, None, None, "1yr rolling"),
            ("Max Drawdown", -8.2, -1.5, MetricTrend.DOWN, None, "%", "YTD"),
            ("Win Rate", 68.5, 2.3, MetricTrend.UP, None, "%", "last 30 trades"),
        ]

        for label, value, change, trend, prefix, suffix, context in metrics:
            yield builder.metric(
                label=label,
                value=value,
                change=change,
                trend=trend,
                prefix=prefix,
                suffix=suffix,
                context=context
            )
            await asyncio.sleep(0.1)

    async def _demo_status_messages(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo status message components."""
        yield builder.text_delta("Status message examples:\n\n")
        await asyncio.sleep(0.2)

        yield builder.success_message(
            message="Portfolio rebalancing completed successfully.",
            title="Rebalancing Complete",
            details="12 trades executed, total fees: $24.50"
        )
        await asyncio.sleep(0.3)

        yield builder.warning_message(
            message="Your portfolio allocation exceeds recommended sector concentration limits.",
            title="Concentration Warning",
            details="Technology sector: 45% (recommended max: 30%)"
        )
        await asyncio.sleep(0.3)

        yield builder.info_message(
            message="Market opens in 2 hours. Pre-market data is now available.",
            title="Market Status"
        )
        await asyncio.sleep(0.3)

        yield builder.error(
            message="Failed to connect to market data provider.",
            title="Connection Error",
            details="Timeout after 30 seconds. Retrying in 5 seconds...",
            retryable=True
        )

    async def _demo_file_preview(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo file preview components."""
        yield builder.text_delta("Generated reports:\n\n")
        await asyncio.sleep(0.2)

        yield builder.file(
            filename="portfolio_analysis_2026Q1.pdf",
            size=2458624,
            file_type="application/pdf",
            download_url="/api/v1/artifacts/report-123"
        )
        await asyncio.sleep(0.2)

        yield builder.file(
            filename="transaction_history.xlsx",
            size=156432,
            file_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            download_url="/api/v1/artifacts/transactions-456"
        )

    async def _demo_link_preview(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo link preview components."""
        yield builder.text_delta("Related resources:\n\n")
        await asyncio.sleep(0.2)

        yield builder.link(
            url="https://investor.apple.com/sec-filings",
            title="Apple SEC Filings",
            description="Official SEC filings including 10-K, 10-Q, and 8-K reports for Apple Inc.",
            site_name="Apple Investor Relations"
        )
        await asyncio.sleep(0.2)

        yield builder.link(
            url="https://finance.yahoo.com/quote/AAPL",
            title="AAPL Stock Quote - Yahoo Finance",
            description="View real-time stock prices, historical data, and analyst recommendations.",
            site_name="Yahoo Finance"
        )

    async def _demo_sources(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo source/citation components."""
        yield builder.text_delta("Information sources used:\n\n")
        await asyncio.sleep(0.2)

        sources = [
            (SourceType.DOCUMENT, "Q4 2025 Earnings Report", "Official earnings call transcript and financial statements", 1),
            (SourceType.WEB, "SEC 10-K Filing", "Annual report with comprehensive financial data", 2),
            (SourceType.DATABASE, "Internal Trade History", "Historical transaction data from portfolio management system", 3),
            (SourceType.API, "Bloomberg Market Data", "Real-time market prices and analytics", 4),
        ]

        for source_type, title, description, citation_num in sources:
            yield builder.source(
                source_type=source_type,
                title=title,
                description=description,
                citation_number=citation_num,
                url=f"https://example.com/source/{citation_num}"
            )
            await asyncio.sleep(0.15)

    async def _demo_form(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo form card component."""
        yield builder.text_delta("Please provide the following information:\n\n")
        await asyncio.sleep(0.2)

        form_part = FormPart(
            id=f"form-{uuid.uuid4().hex[:8]}",
            title="Create New Alert",
            description="Set up a price alert for your watchlist",
            fields=[
                FormFieldPart(id="symbol", label="Stock Symbol", type="text", placeholder="e.g., AAPL", required=True),
                FormFieldPart(id="price", label="Target Price", type="number", placeholder="Enter price", required=True),
                FormFieldPart(id="direction", label="Alert When", type="select", options=[
                    {"value": "above", "label": "Price goes above"},
                    {"value": "below", "label": "Price goes below"},
                ], required=True),
                FormFieldPart(id="notes", label="Notes", type="textarea", placeholder="Optional notes..."),
            ],
            submitLabel="Create Alert"
        )
        yield builder.part(form_part)

    async def _demo_steps(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Demo step indicator component."""
        yield builder.text_delta("Analysis workflow progress:\n\n")
        await asyncio.sleep(0.2)

        yield builder.steps(
            steps=[
                {"id": "collect", "label": "Data Collection", "description": "Gather market data", "status": "complete"},
                {"id": "validate", "label": "Validation", "description": "Verify data quality", "status": "complete"},
                {"id": "analyze", "label": "Analysis", "description": "Run risk models", "status": "active"},
                {"id": "review", "label": "Review", "description": "Human review step", "status": "pending"},
                {"id": "report", "label": "Report", "description": "Generate final report", "status": "pending"},
            ],
            orientation="vertical"
        )

    async def _full_showcase(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Full component showcase with narrative."""

        # ── Chain of Thought (thinking + tool calls grouped in timeline)
        # These come BEFORE any text so ChainOfThought groups them.
        yield builder.thinking(
            variant=ThinkingVariant.DEFAULT,
            thinking_content=(
                "The user wants a comprehensive showcase of our UI components. "
                "Let me gather relevant data and prepare demonstrations."
            ),
            is_thinking=True,
        )
        await asyncio.sleep(0.3)

        # Tool calls inside the chain-of-thought timeline
        cot_tools = [
            ("search_knowledge", 820),
            ("list_workspace", 540),
            ("web_search", 1250),
            ("fetch_from_connector", 670),
        ]
        for tool_name, duration in cot_tools:
            tid = f"cot-{uuid.uuid4().hex[:6]}"
            yield builder.tool_call_start(tid, tool_name)
            await asyncio.sleep(0.4)
            yield builder.tool_call_complete(
                tid, ToolCallStatus.SUCCESS, {"status": "ok"}, duration_ms=duration
            )
            await asyncio.sleep(0.15)

        # Mark thinking done
        yield builder.thinking(
            variant=ThinkingVariant.DEFAULT,
            thinking_content=(
                "The user wants a comprehensive showcase of our UI components. "
                "Let me gather relevant data and prepare demonstrations."
            ),
            is_thinking=False,
        )
        await asyncio.sleep(0.3)

        # ── Response parts (rendered below the collapsed chain)
        intro = """# AI Chat UI Component Showcase

Welcome to the comprehensive demonstration of our AI chat interface components.
This showcase will walk you through each component category.

---

## 1. Text Streaming

First, let's see how text content streams in real-time with markdown support:

"""
        for i in range(0, len(intro), 20):
            yield builder.text_delta(intro[i:i+20])
            await asyncio.sleep(0.015)

        await asyncio.sleep(0.5)

        # Code blocks
        yield builder.text_delta("\n## 2. Code Blocks\n\nSyntax-highlighted code with copy functionality:\n\n")
        await asyncio.sleep(0.2)
        yield builder.code_block(
            code='const greeting = (name: string) => `Hello, ${name}!`;',
            language="typescript",
            filename="utils.ts"
        )
        await asyncio.sleep(0.5)

        # Tool calls (standalone, in the response section)
        yield builder.text_delta("\n## 3. Tool Execution\n\nWatching a tool execute with real-time progress:\n\n")
        await asyncio.sleep(0.2)
        tool_id = f"demo-tool-{uuid.uuid4().hex[:6]}"
        yield builder.tool_call_start(tool_id, "analyze_data", {"dataset": "portfolio"})
        await asyncio.sleep(0.3)
        for step in ["Loading data...", "Processing...", "Complete"]:
            yield builder.tool_call_progress(tool_id, f"step-{step[:4]}", step, StepStatus.ACTIVE)
            await asyncio.sleep(0.3)
        yield builder.tool_call_complete(tool_id, ToolCallStatus.SUCCESS, {"status": "ok"}, duration_ms=900)
        await asyncio.sleep(0.5)

        # Metrics
        yield builder.text_delta("\n## 4. Key Metrics\n\nReal-time KPI dashboard cards:\n\n")
        await asyncio.sleep(0.2)
        yield builder.metric("Total Value", 1285750, 2.5, MetricTrend.UP, "$", None, "portfolio")
        yield builder.metric("Daily P&L", 31250, 2.5, MetricTrend.UP, "$", None, None)
        yield builder.metric("YTD Return", 15.8, None, MetricTrend.UP, None, "%", None)
        await asyncio.sleep(0.5)

        # Table
        yield builder.text_delta("\n## 5. Data Tables\n\nSortable, searchable data presentation:\n\n")
        await asyncio.sleep(0.2)
        yield builder.table(
            columns=[
                {"key": "name", "header": "Name"},
                {"key": "value", "header": "Value"},
                {"key": "status", "header": "Status"},
            ],
            data=[
                {"name": "Alpha Fund", "value": "$125,000", "status": "Active"},
                {"name": "Beta Fund", "value": "$85,000", "status": "Active"},
                {"name": "Gamma Fund", "value": "$45,000", "status": "Closed"},
            ],
            title="Fund Overview",
            sortable=True
        )
        await asyncio.sleep(0.5)

        # Status messages
        yield builder.text_delta("\n## 6. Status Messages\n\nVarious alert types:\n\n")
        await asyncio.sleep(0.2)
        yield builder.success_message("Operation completed successfully!", "Success")
        yield builder.warning_message("Check configuration before proceeding.", "Warning")
        yield builder.info_message("New features available in this release.", "Info")
        await asyncio.sleep(0.5)

        # Conclusion
        conclusion = """

---

## Showcase Complete!

You've seen the key components of our AI chat interface:
- **Streaming text** with markdown
- **Code blocks** with syntax highlighting
- **Tool calls** with progress tracking
- **Metric cards** for KPIs
- **Data tables** for structured data
- **Status messages** for feedback

These components can be combined to create rich, interactive AI experiences.
"""
        for i in range(0, len(conclusion), 25):
            yield builder.text_delta(conclusion[i:i+25])
            await asyncio.sleep(0.01)

    async def _show_help(self, builder: SSEBuilder) -> AsyncGenerator[dict, None]:
        """Show available commands."""
        help_text = """# UI Testing Agent Commands

Use these commands to test specific components:

| Command | Component |
|---------|-----------|
| `show text` | Streaming text with markdown |
| `show code` | Code blocks with syntax highlighting |
| `show tool` | Tool call with progress steps |
| `show question` | Agent question with options |
| `show approval` | Human-in-the-loop approval card |
| `show table` | Data table with sorting |
| `show chart` | Chart/graph visualization |
| `show sql` | SQL query and results |
| `show json` | JSON tree viewer |
| `show diff` | Git-style diff viewer |
| `show timeline` | Timeline visualization |
| `show metrics` | KPI metric cards |
| `show status` | Success/warning/error alerts |
| `show file` | File download preview |
| `show link` | Link preview cards |
| `show source` | Citation/source cards |
| `show form` | Form input card |
| `show steps` | Step progress indicator |
| `showcase` | Full component demonstration |

Try any command to see the component in action!
"""
        for i in range(0, len(help_text), 30):
            yield builder.text_delta(help_text[i:i+30])
            await asyncio.sleep(0.008)


# Singleton instance
ui_testing_agent = UITestingAgent()
