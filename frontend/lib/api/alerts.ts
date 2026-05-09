/**
 * Alert History API client functions.
 */
import { apiFetch } from './index';

// ============================================
// Types
// ============================================

export interface AlertHistoryItem {
  id: string;
  alert_rule_id: string;
  trigger_type: string;
  message: string;
  notification_status: string;
  created_at: string;
}

// ============================================
// API Functions
// ============================================

export async function fetchAlertHistory(
  limit: number = 50
): Promise<AlertHistoryItem[]> {
  return apiFetch<AlertHistoryItem[]>(
    `/api/v1/alert-history?limit=${limit}`
  );
}
