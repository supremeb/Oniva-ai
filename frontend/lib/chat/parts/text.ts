import type { MessagePart } from '@/lib/models';
import { compact } from './types';

/**
 * Strip raw LLM function/tool call markup that leaks into text content.
 * Handles multiple formats:
 *   - XML: <function_calls>...<invoke>...</invoke>...</function_calls>
 *   - DSML: < | DSML | function_calls>...< | DSML | invoke>...</ DSML...>
 *   - Broken: andfunction_calls>, invfunction_calls>, partial tags
 *   - JSON tool calls: {"function_call": ...} blocks after function_calls
 */
export function stripFunctionCallMarkup(text: string): string {
  if (!text) return text;
  let cleaned = text;

  // 1. Complete XML blocks: <function_calls>...</function_calls> and variants
  cleaned = cleaned.replace(/<\s*\|?\s*(?:DSML\s*\|?\s*)?function_calls\s*>[\s\S]*?<\s*\/?\s*\|?\s*(?:DSML\s*\|?\s*)?(?:inv)?function_calls\s*>/gi, '');

  // 2. Partial/unclosed function_calls block at end of string
  cleaned = cleaned.replace(/<\s*\|?\s*(?:DSML\s*\|?\s*)?function_calls\s*>[\s\S]*$/gi, '');

  // 3. Any tag containing "function_calls" (catches all variants)
  cleaned = cleaned.replace(/<\/?[^>]*function_calls[^>]*>/gi, '');

  // 4. DSML invoke/parameter tags
  cleaned = cleaned.replace(/<\s*\|?\s*(?:DSML\s*\|?\s*)?\/?\s*(?:invoke|parameter)[^>]*>/gi, '');

  // 5. Standard invoke tags (including <functioninvoke> merged variant from DeepSeek)
  cleaned = cleaned.replace(/<\/?(?:function)?(?:invoke|antml:invoke|antml:parameter)[^>]*>/gi, '');

  // 6. Broken fragments: "andfunction_calls>", "invfunction_calls>", "function_cinvoke..."
  cleaned = cleaned.replace(/(?:and|inv)function_calls\s*>/gi, '');
  cleaned = cleaned.replace(/function_c\w*invoke[^>]*>/gi, '');

  // 7. Stray </parameter or "</parameter patterns (with or without closing >)
  cleaned = cleaned.replace(/"?\s*<\/?\s*parameter\b[^>]*>?/gi, '');

  // 8. JSON tool call blocks that follow function_calls pattern
  //    e.g., { "query": "...", "num_results": 5 } appearing as leaked text
  //    Only strip if preceded by function_calls context (within same line fragment)
  cleaned = cleaned.replace(/function_calls>\s*\{[^}]*\}/gi, '');

  return cleaned.trim();
}

/** A response text part. `isStreaming` is omitted when not given. */
export function buildTextPart(content: string, isStreaming?: boolean): MessagePart {
  return compact({ type: 'text' as const, content, isStreaming });
}

/** A reasoning accordion part. */
export function buildThinkingPart(opts: {
  content: string;
  isThinking: boolean;
  showElapsed?: boolean;
}): MessagePart {
  return compact({
    type: 'thinking' as const,
    variant: 'accordion' as const,
    thinkingContent: opts.content,
    isThinking: opts.isThinking,
    showElapsed: opts.showElapsed,
  });
}
