'use client';

/**
 * Feature gate component for conditional rendering based on tenant features.
 *
 * Frontend feature gates are a UX convenience — NOT a security boundary.
 * Every feature MUST also have backend enforcement via require_feature().
 *
 * Usage:
 *   <FeatureGate flag="ENABLE_COMPLIANCE">
 *     <ComplianceDashboard />
 *   </FeatureGate>
 *
 *   <FeatureGate flag="ENABLE_COMPLIANCE" fallback={<UpgradePrompt />}>
 *     <ComplianceDashboard />
 *   </FeatureGate>
 */
import { type ReactNode } from 'react';
import { useFeature } from '@/lib/hooks/useFeature';

interface FeatureGateProps {
  /** The feature flag name to check (e.g., "ENABLE_COMPLIANCE") */
  flag: string;
  /** Content to render when the feature is enabled */
  children: ReactNode;
  /** Optional content to render when the feature is disabled (default: null) */
  fallback?: ReactNode;
}

export function FeatureGate({ flag, children, fallback = null }: FeatureGateProps) {
  const enabled = useFeature(flag);
  return enabled ? <>{children}</> : <>{fallback}</>;
}
