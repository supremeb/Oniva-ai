/**
 * Inline Feedback API client (Frontier Phase E).
 *
 * Submit thumbs up/down on agent responses and retrieve summaries.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./index";

// ============================================================================
// Types
// ============================================================================

export interface FeedbackItem {
  id: string;
  tenant_id: string;
  agent_id: string | null;
  user_id: string | null;
  session_id: string | null;
  message_index: number | null;
  rating: "positive" | "negative";
  feedback_text: string | null;
  agent_response_summary: string | null;
  created_at: string | null;
}

export interface FeedbackListResponse {
  feedback: FeedbackItem[];
  count: number;
}

export interface FeedbackSummary {
  agent_id: string | null;
  total: number;
  positive: number;
  negative: number;
  satisfaction_rate: number | null;
  recent_negative_texts: string[];
}

export interface SubmitFeedbackData {
  agent_id?: string | null;
  session_id?: string | null;
  message_index?: number | null;
  rating: "positive" | "negative";
  feedback_text?: string | null;
  agent_response_summary?: string | null;
}

// ============================================================================
// API Functions
// ============================================================================

export async function submitFeedback(data: SubmitFeedbackData): Promise<FeedbackItem> {
  return apiFetch("/api/v1/feedback", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function listFeedback(params?: {
  agent_id?: string;
  rating?: "positive" | "negative";
  limit?: number;
  offset?: number;
}): Promise<FeedbackListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.agent_id) searchParams.set("agent_id", params.agent_id);
  if (params?.rating) searchParams.set("rating", params.rating);
  if (params?.limit) searchParams.set("limit", String(params.limit));
  if (params?.offset) searchParams.set("offset", String(params.offset));
  const qs = searchParams.toString();
  return apiFetch(`/api/v1/feedback${qs ? `?${qs}` : ""}`);
}

export async function getFeedbackSummary(agentId?: string): Promise<FeedbackSummary> {
  const qs = agentId ? `?agent_id=${agentId}` : "";
  return apiFetch(`/api/v1/feedback/summary${qs}`);
}

export async function deleteFeedback(feedbackId: string): Promise<void> {
  return apiFetch(`/api/v1/feedback/${feedbackId}`, {
    method: "DELETE",
  });
}

// ============================================================================
// React Query Hooks
// ============================================================================

export function useSubmitFeedback() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: submitFeedback,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["feedback"] });
      queryClient.invalidateQueries({ queryKey: ["admin"] });
    },
  });
}

export function useFeedbackList(params?: {
  agent_id?: string;
  rating?: "positive" | "negative";
  limit?: number;
  offset?: number;
}) {
  return useQuery({
    queryKey: ["feedback", "list", params],
    queryFn: () => listFeedback(params),
    staleTime: 15_000,
  });
}

export function useFeedbackSummary(agentId?: string) {
  return useQuery({
    queryKey: ["feedback", "summary", agentId],
    queryFn: () => getFeedbackSummary(agentId),
    staleTime: 30_000,
  });
}
