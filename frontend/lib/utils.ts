import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
import { parseISO } from "date-fns"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Parse an ISO 8601 timestamp string to a Date object.
 * Uses parseISO from date-fns which properly handles UTC timestamps.
 * This should be used instead of `new Date(string)` for API timestamps.
 * @param timestamp - ISO 8601 formatted timestamp string
 * @returns Date object
 */
export function parseTimestamp(timestamp: string): Date {
  return parseISO(timestamp)
}

/**
 * Format a decimal value as a percentage string.
 * @param value - A value between 0 and 1 (e.g., 0.85 for 85%)
 * @returns Formatted percentage string (e.g., "85.0%") or "N/A" if null/undefined
 */
export function formatAccuracy(value: number | undefined | null): string {
  if (value === undefined || value === null) return 'N/A';
  return `${(value * 100).toFixed(1)}%`;
}

