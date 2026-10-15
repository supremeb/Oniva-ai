import type { MessagePart } from '@/lib/models';
import { compact } from './types';

/** A file part from a backend `part` event / tool result fields. */
export function buildFilePart(data: {
  filename?: string;
  size?: number;
  fileType?: string;
  downloadUrl?: string;
  content?: string;
  toolCallId?: string;
}): MessagePart {
  return compact({
    type: 'file' as const,
    filename: data.filename || 'untitled',
    size: data.size,
    fileType: data.fileType,
    downloadUrl: data.downloadUrl,
    content: data.content,
    toolCallId: data.toolCallId,
  }) as MessagePart;
}
