import type { MessagePart, SourceItem } from '@/lib/models';
import { compact } from './types';

/**
 * A source part. `sourceType` defaults to url for http(s) links and document
 * otherwise (the legacy client rule). Memory sources keep `sourceType: 'memory'`
 * as legacy persisted them, which the MessagePart union does not list.
 */
export function buildSourcePart(source: {
  title: string;
  url?: string;
  snippet?: string;
  sourceType?: SourceItem['sourceType'];
  citationNumber?: number;
  metadata?: Record<string, unknown>;
}): MessagePart {
  return compact({
    type: 'source' as const,
    sourceType: (source.sourceType ||
      (source.url?.startsWith('http') ? 'url' : 'document')) as 'url' | 'document',
    title: source.title,
    url: source.url,
    description: source.snippet,
    citationNumber: source.citationNumber,
    metadata: source.metadata,
  });
}
