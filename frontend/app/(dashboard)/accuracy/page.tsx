'use client';

import { useState, useEffect, memo } from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
  ChartBar,
  CheckCircle,
  XCircle,
  ArrowUp,
  ArrowDown,
  CircleNotch,
  Target,
  TestTube,
  Lightning,
  Info,
} from '@phosphor-icons/react';
import { getAccuracyMetrics, AccuracyDashboardMetrics } from '@/lib/api/evaluation';
import { cn } from '@/lib/utils';

// Agent display configuration
const AGENT_CONFIG: Record<string, { name: string; color: string; bgColor: string }> = {
  compliance: {
    name: 'Compliance',
    color: 'text-emerald-600',
    bgColor: 'bg-emerald-100',
  },
  data_retrieval: {
    name: 'Data Retrieval',
    color: 'text-blue-600',
    bgColor: 'bg-blue-100',
  },
  analysis: {
    name: 'Analysis',
    color: 'text-violet-600',
    bgColor: 'bg-violet-100',
  },
  research: {
    name: 'Research',
    color: 'text-amber-600',
    bgColor: 'bg-amber-100',
  },
};

// Accuracy gauge component
interface AccuracyGaugeProps {
  accuracy: number;
  size?: 'sm' | 'md' | 'lg';
  showLabel?: boolean;
}

const AccuracyGauge = memo(function AccuracyGauge({
  accuracy,
  size = 'md',
  showLabel = true,
}: AccuracyGaugeProps) {
  const percentage = Math.round(accuracy * 100);
  const circumference = 2 * Math.PI * 45;
  const strokeDashoffset = circumference - (accuracy * circumference);

  const sizeClasses = {
    sm: 'w-16 h-16',
    md: 'w-24 h-24',
    lg: 'w-32 h-32',
  };

  const textSizeClasses = {
    sm: 'text-lg',
    md: 'text-2xl',
    lg: 'text-3xl',
  };

  const getColor = () => {
    if (percentage >= 95) return 'text-emerald-500';
    if (percentage >= 85) return 'text-amber-500';
    return 'text-red-500';
  };

  return (
    <div className={cn('relative', sizeClasses[size])}>
      <svg className="w-full h-full transform -rotate-90" viewBox="0 0 100 100">
        {/* Background circle */}
        <circle
          cx="50"
          cy="50"
          r="45"
          stroke="currentColor"
          strokeWidth="8"
          fill="none"
          className="text-muted/30"
        />
        {/* Progress circle */}
        <circle
          cx="50"
          cy="50"
          r="45"
          stroke="currentColor"
          strokeWidth="8"
          fill="none"
          strokeLinecap="round"
          className={cn('transition-all duration-1000 ease-out', getColor())}
          style={{
            strokeDasharray: circumference,
            strokeDashoffset: strokeDashoffset,
          }}
        />
      </svg>
      {showLabel && (
        <div className="absolute inset-0 flex items-center justify-center">
          <span className={cn('font-bold', textSizeClasses[size], getColor())}>
            {percentage}%
          </span>
        </div>
      )}
    </div>
  );
});

// Agent accuracy card
interface AgentCardProps {
  agentType: string;
  accuracy: number;
  testCount: number;
  categories: string[];
}

const AgentCard = memo(function AgentCard({
  agentType,
  accuracy,
  testCount,
  categories,
}: AgentCardProps) {
  const config = AGENT_CONFIG[agentType] || {
    name: agentType,
    color: 'text-gray-600',
    bgColor: 'bg-gray-100',
  };

  const percentage = Math.round(accuracy * 100);
  const passedTests = Math.round(testCount * accuracy);

  return (
    <Card className="border-border/60 hover:shadow-md transition-shadow duration-200">
      <CardContent className="p-5">
        <div className="flex items-start justify-between">
          <div className="space-y-1">
            <div className={cn('inline-flex px-2 py-1 rounded-md text-xs font-medium', config.bgColor, config.color)}>
              {config.name}
            </div>
            <div className="mt-3">
              <span className="text-3xl font-bold">{percentage}%</span>
              <span className="text-sm text-muted-foreground ml-1">accuracy</span>
            </div>
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <CheckCircle className="h-4 w-4 text-emerald-500" weight="fill" />
              <span>{passedTests}/{testCount} tests passed</span>
            </div>
          </div>
          <AccuracyGauge accuracy={accuracy} size="sm" showLabel={false} />
        </div>

        {/* Categories */}
        <div className="mt-4 pt-4 border-t border-border/40">
          <p className="text-overline mb-2">Test Categories</p>
          <div className="flex flex-wrap gap-1.5">
            {categories.map((category) => (
              <span
                key={category}
                className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground"
              >
                {category.replace(/_/g, ' ')}
              </span>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
});

// Stats card component
interface StatsCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: React.ElementType;
  iconColor: string;
  trend?: {
    value: number;
    label: string;
  };
}

const StatsCard = memo(function StatsCard({
  title,
  value,
  subtitle,
  icon: Icon,
  iconColor,
  trend,
}: StatsCardProps) {
  return (
    <Card className="border-border/60">
      <CardContent className="p-5">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-card-title text-muted-foreground">{title}</p>
            <p className="text-stat mt-1">{value}</p>
            {subtitle && (
              <p className="text-detail mt-0.5">{subtitle}</p>
            )}
            {trend && (
              <div className={cn(
                'flex items-center gap-1 mt-2 text-xs font-medium',
                trend.value >= 0 ? 'text-emerald-600' : 'text-red-600'
              )}>
                {trend.value >= 0 ? (
                  <ArrowUp className="h-3 w-3" weight="bold" />
                ) : (
                  <ArrowDown className="h-3 w-3" weight="bold" />
                )}
                <span>{Math.abs(trend.value)}% {trend.label}</span>
              </div>
            )}
          </div>
          <div className={cn('w-10 h-10 rounded-xl flex items-center justify-center', iconColor)}>
            <Icon className="h-5 w-5 text-white" weight="fill" />
          </div>
        </div>
      </CardContent>
    </Card>
  );
});

// Fallback metrics when backend is unavailable
const FALLBACK_METRICS: AccuracyDashboardMetrics = {
  overall_accuracy: 0.95,
  total_test_cases: 200,
  last_run: new Date().toISOString(),
  agents: [
    { agent_type: 'compliance', accuracy: 0.96, test_count: 50, categories: ['policy_check', 'risk_assessment', 'regulatory'] },
    { agent_type: 'data_retrieval', accuracy: 0.94, test_count: 50, categories: ['api_fetch', 'data_transform', 'validation'] },
    { agent_type: 'analysis', accuracy: 0.95, test_count: 50, categories: ['financial_calc', 'metric_analysis', 'reporting'] },
    { agent_type: 'research', accuracy: 0.95, test_count: 50, categories: ['rag_search', 'document_qa', 'summarization'] },
  ],
};

export default function AccuracyDashboardPage() {
  const [metrics, setMetrics] = useState<AccuracyDashboardMetrics | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showFallback, setShowFallback] = useState(false);

  // After 1.5 seconds, show fallback data if still loading
  useEffect(() => {
    const timer = setTimeout(() => setShowFallback(true), 1500);
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    async function fetchMetrics() {
      try {
        setIsLoading(true);
        const data = await getAccuracyMetrics();
        setMetrics(data);
        setError(null);
      } catch (err) {
        setError('Failed to load accuracy metrics');
        console.error('Error fetching accuracy metrics:', err);
      } finally {
        setIsLoading(false);
      }
    }

    fetchMetrics();
  }, []);

  // Use fetched metrics or fallback when timeout reached
  const displayMetrics = metrics || (showFallback ? FALLBACK_METRICS : null);
  const actuallyLoading = isLoading && !showFallback && !metrics;

  if (actuallyLoading) {
    return (
      <div className="p-6 lg:p-8 max-w-7xl mx-auto">
        <div className="flex items-center justify-center h-64">
          <div className="flex flex-col items-center gap-3">
            <CircleNotch className="h-8 w-8 animate-spin text-primary" />
            <span className="text-sm text-muted-foreground">Loading accuracy metrics...</span>
          </div>
        </div>
      </div>
    );
  }

  if (!displayMetrics) return null;

  const showingFallback = !metrics && showFallback;
  const overallPercentage = Math.round(displayMetrics.overall_accuracy * 100);
  const passedTests = displayMetrics.agents.reduce(
    (sum, agent) => sum + Math.round(agent.test_count * agent.accuracy),
    0
  );

  return (
    <div className="p-6 lg:p-8 max-w-7xl mx-auto space-y-8 animate-in">
      {/* Header */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
          <span className="text-overline">
            Evaluation Framework
          </span>
        </div>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-heading-1 text-balance">
              <span className="gradient-text">Accuracy Dashboard</span>
            </h1>
            <p className="text-muted-foreground text-body-lg max-w-2xl mt-1">
              {showingFallback && <span className="text-amber-600">(Showing demo data - backend offline) </span>}
              Monitor agent performance with {displayMetrics.total_test_cases} automated test cases
              covering compliance, data retrieval, analysis, and research workflows.
            </p>
          </div>
          <Button className="gap-2" disabled>
            <TestTube className="h-4 w-4" />
            Run Evaluation
          </Button>
        </div>
      </div>

      {/* Overall Accuracy Hero */}
      <Card className="border-border/60 bg-gradient-to-br from-card to-muted/20">
        <CardContent className="p-8">
          <div className="flex items-center gap-8">
            <AccuracyGauge accuracy={displayMetrics.overall_accuracy} size="lg" />
            <div className="flex-1">
              <h2 className="text-heading-4 text-muted-foreground">Overall Accuracy</h2>
              <p className="text-4xl font-bold mt-1">
                {overallPercentage}%
                {overallPercentage >= 95 && (
                  <span className="ml-2 text-sm font-normal text-emerald-600 bg-emerald-100 px-2 py-0.5 rounded-full">
                    Target Met
                  </span>
                )}
              </p>
              <p className="text-body-muted mt-2">
                {passedTests} of {displayMetrics.total_test_cases} test cases passing
              </p>
              {displayMetrics.last_run && (
                <p className="text-detail mt-3">
                  Last evaluated: {new Date(displayMetrics.last_run).toLocaleString()}
                </p>
              )}
            </div>
            <div className="hidden lg:block">
              <div className="p-4 rounded-xl bg-muted/50 border border-border/40">
                <div className="flex items-start gap-2">
                  <Info className="h-4 w-4 text-muted-foreground mt-0.5" />
                  <div className="text-xs text-muted-foreground">
                    <p className="font-medium text-foreground">Phase 1 Target: 95%</p>
                    <p className="mt-1">
                      Tests include deterministic checks for schema validation,
                      numeric accuracy, and LLM-as-judge evaluation for qualitative responses.
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Quick Stats */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatsCard
          title="Total Test Cases"
          value={displayMetrics.total_test_cases}
          subtitle="Across all agents"
          icon={TestTube}
          iconColor="bg-primary"
        />
        <StatsCard
          title="Tests Passing"
          value={passedTests}
          subtitle={`${displayMetrics.total_test_cases - passedTests} failing`}
          icon={CheckCircle}
          iconColor="bg-emerald-500"
        />
        <StatsCard
          title="Agent Types"
          value={displayMetrics.agents.length}
          subtitle="Under evaluation"
          icon={Target}
          iconColor="bg-violet-500"
        />
        <StatsCard
          title="Accuracy Target"
          value="95%"
          subtitle="Production threshold"
          icon={Lightning}
          iconColor="bg-amber-500"
        />
      </div>

      {/* Agent Breakdown */}
      <section className="space-y-4">
        <div>
          <h2 className="text-heading-3">Agent Performance</h2>
          <p className="text-body-muted mt-1">
            Individual accuracy metrics for each agent type
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {displayMetrics.agents.map((agent) => (
            <AgentCard
              key={agent.agent_type}
              agentType={agent.agent_type}
              accuracy={agent.accuracy}
              testCount={agent.test_count}
              categories={agent.categories}
            />
          ))}
        </div>
      </section>

      {/* Test Categories Legend */}
      <section className="space-y-4">
        <Card className="border-border/60">
          <CardHeader className="pb-2">
            <CardTitle className="text-lg flex items-center gap-2">
              <ChartBar className="h-5 w-5 text-primary" />
              Evaluation Categories
            </CardTitle>
            <CardDescription>
              Test cases are organized by category for comprehensive coverage
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
              {displayMetrics.agents.map((agent) => {
                const config = AGENT_CONFIG[agent.agent_type] || {
                  name: agent.agent_type,
                  color: 'text-gray-600',
                  bgColor: 'bg-gray-100',
                };
                return (
                  <div key={agent.agent_type}>
                    <p className={cn('text-sm font-medium mb-2', config.color)}>
                      {config.name}
                    </p>
                    <ul className="space-y-1">
                      {agent.categories.map((category) => (
                        <li
                          key={category}
                          className="text-xs text-muted-foreground flex items-center gap-2"
                        >
                          <span className={cn('w-1.5 h-1.5 rounded-full', config.bgColor)} />
                          {category.replace(/_/g, ' ')}
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
