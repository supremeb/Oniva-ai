import { useRef, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Send, Loader2 } from 'lucide-react';

interface ChatInputProps {
  input: string;
  onInputChange: (e: React.ChangeEvent<HTMLTextAreaElement> | React.ChangeEvent<HTMLInputElement>) => void;
  onSubmit: (e: React.FormEvent<HTMLFormElement>) => void;
  isLoading: boolean;
  disabled?: boolean;
}

export function ChatInput({ input, onInputChange, onSubmit, isLoading, disabled = false }: ChatInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'inherit';
      const scrollHeight = textareaRef.current.scrollHeight;
      textareaRef.current.style.height = `${Math.min(scrollHeight, 200)}px`;
    }
  }, [input]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (input.trim() && !isLoading) {
        // Create a synthetic event
        const form = e.currentTarget.form;
        if (form) {
            form.requestSubmit();
        }
      }
    }
  };

  return (
    <form onSubmit={onSubmit} className="max-w-3xl mx-auto relative flex items-end gap-2 bg-background p-2 rounded-lg border focus-within:ring-1 focus-within:ring-ring">
      <Textarea
        ref={textareaRef}
        value={input}
        onChange={onInputChange}
        onKeyDown={handleKeyDown}
        placeholder="Ask the agent..."
        className="min-h-[44px] max-h-[200px] w-full resize-none border-0 focus-visible:ring-0 focus-visible:ring-offset-0 bg-transparent py-3"
        rows={1}
        disabled={disabled}
      />
      <Button
        type="submit"
        size="icon"
        disabled={isLoading || !input.trim() || disabled}
        className="mb-1"
      >
        {isLoading ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Send className="h-4 w-4" />
        )}
        <span className="sr-only">Send</span>
      </Button>
    </form>
  );
}
