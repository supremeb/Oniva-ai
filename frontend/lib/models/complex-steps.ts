/**
 * Complex Step Types for AI Workflow Builder
 *
 * Supports multi-action steps (sequential actions) and conditional steps (branching).
 * Nested sub-workflows deferred to v2.
 */

// Base step remains the same structure as existing nodes
export interface BaseStep {
  id: string;
  type: string;
  label: string;
  config: Record<string, unknown>;
}

// Complex step type enum
export type ComplexStepType = 'multi-action' | 'conditional';

// Complex step wrapper
export interface ComplexStep extends BaseStep {
  type: 'complex';
  complexType: ComplexStepType;
  isExpanded: boolean;
}

// Action types for multi-action steps
export type ActionType = 'connector' | 'llm' | 'code' | 'http' | 'template' | 'wait' | 'notification';

export interface Action {
  id: string;
  type: ActionType;
  label: string;
  config: Record<string, unknown>;
  dependsOn?: string; // Previous action ID for sequential execution
}

// Multi-action: Multiple sequential actions in one step
export interface MultiActionStep extends ComplexStep {
  complexType: 'multi-action';
  actions: Action[];
}

// Condition operators
export type ConditionOperator = 'eq' | 'neq' | 'gt' | 'lt' | 'gte' | 'lte' | 'contains' | 'not_contains' | 'exists' | 'not_exists';

export interface Condition {
  field: string;
  operator: ConditionOperator;
  value: unknown;
}

// Branch in conditional step
export interface Branch {
  id: string;
  label: string; // e.g., "High Risk", "Auto-Approve"
  condition: Condition;
  steps: BaseStep[]; // Steps to execute if condition matches
}

// Conditional: Branching logic in one step
export interface ConditionalStep extends ComplexStep {
  complexType: 'conditional';
  branches: Branch[];
  defaultBranch?: Branch;
}

// Union type for all complex step variants
export type AnyComplexStep = MultiActionStep | ConditionalStep;

// Type guard functions
export function isComplexStep(step: BaseStep): step is ComplexStep {
  return step.type === 'complex';
}

export function isMultiActionStep(step: BaseStep): step is MultiActionStep {
  return isComplexStep(step) && step.complexType === 'multi-action';
}

export function isConditionalStep(step: BaseStep): step is ConditionalStep {
  return isComplexStep(step) && step.complexType === 'conditional';
}

// Helper to get complex step summary for display
export function getComplexStepSummary(step: ComplexStep): string {
  if (isMultiActionStep(step)) {
    const count = step.actions.length;
    return `${count} action${count !== 1 ? 's' : ''} in sequence`;
  }
  if (isConditionalStep(step)) {
    const count = step.branches.length;
    const hasDefault = step.defaultBranch ? ' + default' : '';
    return `${count} branch${count !== 1 ? 'es' : ''}${hasDefault}`;
  }
  return '';
}

// Helper to get badge text for complex step type
export function getComplexTypeBadge(complexType: ComplexStepType): string {
  switch (complexType) {
    case 'multi-action':
      return 'MULTI';
    case 'conditional':
      return 'COND';
    default:
      return '';
  }
}

// Factory function to create a new multi-action step
export function createMultiActionStep(label: string, actions: Action[] = []): MultiActionStep {
  return {
    id: `complex-${Date.now()}`,
    type: 'complex',
    label,
    config: {},
    complexType: 'multi-action',
    isExpanded: false,
    actions,
  };
}

// Factory function to create a new conditional step
export function createConditionalStep(label: string, branches: Branch[] = []): ConditionalStep {
  return {
    id: `complex-${Date.now()}`,
    type: 'complex',
    label,
    config: {},
    complexType: 'conditional',
    isExpanded: false,
    branches,
    defaultBranch: undefined,
  };
}

// Factory function to create a new action
export function createAction(type: ActionType, label: string, config: Record<string, unknown> = {}): Action {
  return {
    id: `action-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    type,
    label,
    config,
  };
}

// Factory function to create a new branch
export function createBranch(label: string, condition: Condition, steps: BaseStep[] = []): Branch {
  return {
    id: `branch-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    label,
    condition,
    steps,
  };
}
