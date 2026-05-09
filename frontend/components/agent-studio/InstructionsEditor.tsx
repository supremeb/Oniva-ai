'use client';

import { useCallback, useEffect, useRef, forwardRef, useImperativeHandle } from 'react';
import { useEditor, EditorContent, ReactNodeViewRenderer } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Mention from '@tiptap/extension-mention';
import Placeholder from '@tiptap/extension-placeholder';
import { cn } from '@/lib/utils';
import { MentionChip } from './MentionChip';
import { searchMentions, type MentionItem } from '@/lib/api/mentions';
import type { SuggestionProps, SuggestionKeyDownProps } from '@tiptap/suggestion';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface MentionReference {
  entity_type: string;
  entity_id: string;
  display_name: string;
}

export interface InstructionsEditorHandle {
  getJSON: () => Record<string, unknown>;
  getText: () => string;
  getMentions: () => MentionReference[];
}

interface InstructionsEditorProps {
  /** Current content — plain text or Tiptap JSON string. */
  content: string;
  /** Format of the content: plain_text renders as a text paragraph. */
  instructionFormat: 'plain_text' | 'tiptap_json';
  /** Called when content changes. Receives Tiptap JSON string. */
  onChange: (json: string, plainText: string) => void;
  /** Called when mentions change. */
  onMentionsChange?: (mentions: MentionReference[]) => void;
  /** Additional class names for the editor wrapper. */
  className?: string;
  /** Whether the editor is read-only. */
  readOnly?: boolean;
  /** Display variant: 'document' for full-height canvas, 'form' for original compact style. */
  variant?: 'form' | 'document';
}

// ---------------------------------------------------------------------------
// Mention suggestion plugin
// ---------------------------------------------------------------------------

function createSuggestion() {
  return {
    char: '@',
    allowSpaces: true,

    items: async ({ query }: { query: string }): Promise<MentionItem[]> => {
      if (!query || query.length < 1) return [];
      try {
        const response = await searchMentions(query);
        return response.results;
      } catch {
        return [];
      }
    },

    render: () => {
      let popup: HTMLDivElement | null = null;
      let currentItems: MentionItem[] = [];
      let selectedIndex = 0;
      let commandFn: ((item: MentionItem) => void) | null = null;

      const updatePopup = () => {
        if (!popup) return;
        popup.innerHTML = '';

        if (currentItems.length === 0) {
          const empty = document.createElement('div');
          empty.className = 'px-3 py-2 text-xs text-muted-foreground';
          empty.textContent = 'No results found';
          popup.appendChild(empty);
          return;
        }

        let currentType = '';
        currentItems.forEach((item, index) => {
          // Group header
          if (item.entity_type !== currentType) {
            currentType = item.entity_type;
            const header = document.createElement('div');
            header.className = 'px-3 py-1 text-[10px] uppercase tracking-wider text-muted-foreground font-medium';
            header.textContent = {
              skill: 'Skills',
              connector: 'Connectors',
              agent: 'Agents',
              knowledge_base: 'Knowledge Bases',
            }[item.entity_type] || item.entity_type;
            popup!.appendChild(header);
          }

          const el = document.createElement('button');
          el.className = cn(
            'w-full text-left px-3 py-1.5 text-sm flex items-center gap-2 hover:bg-accent transition-colors',
            index === selectedIndex ? 'bg-accent' : '',
          );
          el.innerHTML = `
            <span class="font-medium truncate">${item.display_name}</span>
            ${item.description ? `<span class="text-xs text-muted-foreground truncate ml-auto">${item.description}</span>` : ''}
          `;
          el.addEventListener('mouseenter', () => {
            selectedIndex = index;
            updatePopup();
          });
          el.addEventListener('click', (e) => {
            e.preventDefault();
            commandFn?.(item);
          });
          popup!.appendChild(el);
        });
      };

      return {
        onStart: (props: SuggestionProps<MentionItem>) => {
          popup = document.createElement('div');
          popup.className =
            'z-50 min-w-[200px] max-w-[320px] max-h-[240px] overflow-y-auto rounded-lg border bg-popover text-popover-foreground shadow-md py-1';
          popup.style.position = 'absolute';

          // Position near cursor
          const { view } = props.editor;
          const coords = view.coordsAtPos(props.range.from);
          popup.style.left = `${coords.left}px`;
          popup.style.top = `${coords.bottom + 4}px`;

          document.body.appendChild(popup);
          commandFn = (item: MentionItem) => {
            props.command({
              id: item.entity_id,
              label: item.display_name,
              entityType: item.entity_type,
              entityId: item.entity_id,
              displayName: item.display_name,
              description: item.description || '',
              enabled: item.enabled,
              deleted: item.deleted,
            });
          };
          currentItems = props.items;
          selectedIndex = 0;
          updatePopup();
        },

        onUpdate: (props: SuggestionProps<MentionItem>) => {
          currentItems = props.items;
          selectedIndex = 0;

          if (popup) {
            const { view } = props.editor;
            const coords = view.coordsAtPos(props.range.from);
            popup.style.left = `${coords.left}px`;
            popup.style.top = `${coords.bottom + 4}px`;
          }

          commandFn = (item: MentionItem) => {
            props.command({
              id: item.entity_id,
              label: item.display_name,
              entityType: item.entity_type,
              entityId: item.entity_id,
              displayName: item.display_name,
              description: item.description || '',
              enabled: item.enabled,
              deleted: item.deleted,
            });
          };
          updatePopup();
        },

        onKeyDown: ({ event }: SuggestionKeyDownProps) => {
          if (event.key === 'ArrowDown') {
            selectedIndex = (selectedIndex + 1) % currentItems.length;
            updatePopup();
            return true;
          }
          if (event.key === 'ArrowUp') {
            selectedIndex = (selectedIndex - 1 + currentItems.length) % currentItems.length;
            updatePopup();
            return true;
          }
          if (event.key === 'Enter') {
            const item = currentItems[selectedIndex];
            if (item) commandFn?.(item);
            return true;
          }
          if (event.key === 'Escape') {
            popup?.remove();
            popup = null;
            return true;
          }
          return false;
        },

        onExit: () => {
          popup?.remove();
          popup = null;
        },
      };
    },
  };
}

// ---------------------------------------------------------------------------
// Custom Mention Extension
// ---------------------------------------------------------------------------

const CustomMention = Mention.extend({
  addAttributes() {
    return {
      id: { default: null },
      label: { default: null },
      entityType: { default: 'skill' },
      entityId: { default: '' },
      displayName: { default: '' },
      description: { default: '' },
      enabled: { default: true },
      deleted: { default: false },
    };
  },

  addNodeView() {
    return ReactNodeViewRenderer(MentionChip);
  },
});

// ---------------------------------------------------------------------------
// Editor Component
// ---------------------------------------------------------------------------

export const InstructionsEditor = forwardRef<InstructionsEditorHandle, InstructionsEditorProps>(
  function InstructionsEditor(
    { content, instructionFormat, onChange, onMentionsChange, className, readOnly = false, variant = 'document' },
    ref,
  ) {
    const isDocument = variant === 'document';
    const isInitialized = useRef(false);

    // Build initial content from props
    const getInitialContent = useCallback(() => {
      if (content) {
        // Try parsing as TipTap JSON — either explicitly marked or auto-detected
        if (instructionFormat === 'tiptap_json' || content.startsWith('{"type":"doc"')) {
          try {
            const parsed = JSON.parse(content);
            if (parsed && parsed.type === 'doc') return parsed;
          } catch {
            // Not valid JSON — fall through to plain text
          }
        }
        // Plain text: wrap in a doc with a paragraph
        return {
          type: 'doc',
          content: content.split('\n').map((line) => ({
            type: 'paragraph',
            content: line ? [{ type: 'text', text: line }] : [],
          })),
        };
      }
      return undefined;
    }, [content, instructionFormat]);

    const editor = useEditor({
      extensions: [
        StarterKit,
        Placeholder.configure({
          placeholder: 'Write agent instructions... Use @ to mention skills, connectors, or agents.',
        }),
        CustomMention.configure({
          HTMLAttributes: {
            class: 'mention-chip',
          },
          suggestion: createSuggestion(),
        }),
      ],
      content: getInitialContent(),
      editable: !readOnly,
      onUpdate: ({ editor }) => {
        const json = JSON.stringify(editor.getJSON());
        const text = editor.getText();
        onChange(json, text);

        // Extract mentions
        const mentions = extractMentions(editor.getJSON());
        onMentionsChange?.(mentions);
      },
      // Prevent hydration mismatch
      immediatelyRender: false,
    });

    // Expose imperative methods
    useImperativeHandle(ref, () => ({
      getJSON: () => editor?.getJSON() ?? {},
      getText: () => editor?.getText() ?? '',
      getMentions: () => (editor ? extractMentions(editor.getJSON()) : []),
    }));

    // Sync external content changes to TipTap (e.g. when agent loads after initial render)
    useEffect(() => {
      if (!editor) return;
      // Skip if editor is focused (user is typing)
      if (editor.isFocused) return;

      const currentText = editor.getText().trim();
      // Only update if editor is empty and new content arrived
      if (!currentText && content) {
        let newContent;
        // Try parsing as TipTap JSON — either explicitly marked or auto-detected
        if (instructionFormat === 'tiptap_json' || content.startsWith('{"type":"doc"')) {
          try {
            const parsed = JSON.parse(content);
            if (parsed && parsed.type === 'doc') newContent = parsed;
          } catch {
            // Not valid JSON — fall through to plain text
          }
        }
        if (!newContent) {
          newContent = {
            type: 'doc',
            content: content.split('\n').map((line: string) => ({
              type: 'paragraph',
              content: line ? [{ type: 'text', text: line }] : [],
            })),
          };
        }
        editor.commands.setContent(newContent, { emitUpdate: false });
      }
    }, [editor, content, instructionFormat]);

    return (
      <div className={cn(
        'instructions-editor',
        isDocument && 'h-full flex flex-col min-h-0',
        className,
      )}>
        {/* Minimal toolbar */}
        {editor && !readOnly && (
          <div className={cn(
            'flex items-center gap-0.5 px-3 py-1.5 border-b border-border/60 shrink-0',
            isDocument && 'px-6',
          )}>
            <ToolbarButton
              onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
              active={editor.isActive('heading', { level: 2 })}
              title="Heading"
            >
              H2
            </ToolbarButton>
            <ToolbarButton
              onClick={() => editor.chain().focus().toggleBold().run()}
              active={editor.isActive('bold')}
              title="Bold"
            >
              <strong>B</strong>
            </ToolbarButton>
            <ToolbarButton
              onClick={() => editor.chain().focus().toggleItalic().run()}
              active={editor.isActive('italic')}
              title="Italic"
            >
              <em>I</em>
            </ToolbarButton>
            <span className="w-px h-3.5 bg-border/60 mx-1.5" />
            <ToolbarButton
              onClick={() => editor.chain().focus().toggleBulletList().run()}
              active={editor.isActive('bulletList')}
              title="Bullet List"
            >
              •
            </ToolbarButton>
            <ToolbarButton
              onClick={() => editor.chain().focus().toggleOrderedList().run()}
              active={editor.isActive('orderedList')}
              title="Numbered List"
            >
              1.
            </ToolbarButton>
            <ToolbarButton
              onClick={() => editor.chain().focus().toggleCodeBlock().run()}
              active={editor.isActive('codeBlock')}
              title="Code Block"
            >
              {'</>'}
            </ToolbarButton>
          </div>
        )}
        <EditorContent
          editor={editor}
          className={cn(
            'prose dark:prose-invert max-w-none prose-p:leading-relaxed prose-li:leading-relaxed prose-headings:tracking-tight',
            isDocument
              ? 'flex-1 min-h-0 overflow-y-auto px-6 py-4 prose-base [&_.tiptap]:outline-none [&_.tiptap]:border-none [&_.tiptap]:shadow-none [&_.tiptap]:min-h-full [&_.ProseMirror]:outline-none [&_.ProseMirror]:border-none [&_.ProseMirror]:shadow-none'
              : 'prose-sm min-h-[200px] px-3 py-2 [&_.tiptap]:outline-none [&_.tiptap]:border-none [&_.tiptap]:shadow-none [&_.tiptap]:min-h-[180px]',
            '[&_.tiptap_p.is-editor-empty:first-child::before]:text-muted-foreground/50',
            '[&_.tiptap_p.is-editor-empty:first-child::before]:content-[attr(data-placeholder)]',
            '[&_.tiptap_p.is-editor-empty:first-child::before]:float-left',
            '[&_.tiptap_p.is-editor-empty:first-child::before]:h-0',
            '[&_.tiptap_p.is-editor-empty:first-child::before]:pointer-events-none',
          )}
        />
      </div>
    );
  },
);

// ---------------------------------------------------------------------------
// Toolbar Button
// ---------------------------------------------------------------------------

function ToolbarButton({
  children,
  onClick,
  active,
  title,
}: {
  children: React.ReactNode;
  onClick: () => void;
  active: boolean;
  title: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={cn(
        'px-2 py-1 text-xs rounded-md transition-colors leading-none',
        active
          ? 'bg-accent text-accent-foreground font-medium'
          : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground',
      )}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Extract all mention references from Tiptap JSON.
 */
export function extractMentions(doc: Record<string, unknown>): MentionReference[] {
  const mentions: MentionReference[] = [];

  function walk(node: Record<string, unknown>) {
    if (node.type === 'mention' && node.attrs) {
      const attrs = node.attrs as Record<string, unknown>;
      mentions.push({
        entity_type: (attrs.entityType as string) || 'skill',
        entity_id: (attrs.entityId as string) || (attrs.id as string) || '',
        display_name: (attrs.displayName as string) || (attrs.label as string) || '',
      });
    }
    if (Array.isArray(node.content)) {
      for (const child of node.content) {
        walk(child as Record<string, unknown>);
      }
    }
  }

  walk(doc);
  return mentions;
}
