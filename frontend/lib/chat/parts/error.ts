import type { MessagePart } from '@/lib/models';
import { compact } from './types';

export function buildErrorPart(opts: {
  id?: string;
  message: string;
  title?: string;
  details?: string;
  retryable?: boolean;
}): MessagePart {
  return compact({ type: 'error' as const, ...opts });
}
