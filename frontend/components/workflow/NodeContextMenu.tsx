import React, { useEffect, useRef, useState, useCallback } from 'react';
import {
  Command,
  CommandInput,
  CommandList,
  CommandEmpty,
  CommandGroup,
  CommandItem,
} from '@/components/ui/command';
import {
  Play,
  Brain,
  Database,
  Code,
  Globe,
  GitBranch,
  ArrowsClockwise,
  TextT,
  FunnelSimple,
  ChatTeardropText,
  Export,
  MagnifyingGlass,
  TrendUp,
  ShieldCheck,
  BookOpen,
  Robot,
  Plugs,
  Lightning,
  // New node icons
  Timer,
  Equals,
  ListBullets,
  Note,
  FileText,
  Crosshair,
  Scan,
  Bell,
  Link,
  TreeStructure,
  UserCircle,
} from '@phosphor-icons/react';

interface NodeContextMenuProps {
  x: number;
  y: number;
  onAddNode: (type: string) => void;
  onClose: () => void;
}

const NODE_CATEGORIES = [
  {
    name: 'Agents & Skills',
    items: [
      { type: 'agent', label: 'Agent', icon: Robot },
      { type: 'skill', label: 'Skill', icon: Lightning },
      { type: 'connector', label: 'Connector', icon: Plugs },
    ],
  },
  {
    name: 'Financial',
    items: [
      { type: 'data', label: 'Data Retrieval', icon: Database },
      { type: 'analysis', label: 'Analysis', icon: TrendUp },
      { type: 'compliance', label: 'Compliance', icon: ShieldCheck },
      { type: 'research', label: 'Research', icon: BookOpen },
    ],
  },
  {
    name: 'Basic',
    items: [
      { type: 'start', label: 'Start', icon: Play },
      { type: 'llm', label: 'LLM', icon: Brain },
      { type: 'knowledge', label: 'Knowledge Retrieval', icon: MagnifyingGlass },
      { type: 'output', label: 'End', icon: Export },
    ],
  },
  {
    name: 'Logic',
    items: [
      { type: 'ifelse', label: 'IF/ELSE', icon: GitBranch },
      { type: 'iteration', label: 'Iteration', icon: ArrowsClockwise },
      { type: 'classifier', label: 'Question Classifier', icon: ChatTeardropText },
      { type: 'wait', label: 'Wait/Delay', icon: Timer },
      { type: 'subworkflow', label: 'Sub-Workflow', icon: TreeStructure },
      { type: 'human_in_loop', label: 'Human Approval', icon: UserCircle },
    ],
  },
  {
    name: 'Transform',
    items: [
      { type: 'code', label: 'Code', icon: Code },
      { type: 'template', label: 'Template', icon: TextT },
      { type: 'aggregator', label: 'Variable Aggregator', icon: FunnelSimple },
      { type: 'variable_assigner', label: 'Variable Assigner', icon: Equals },
      { type: 'list_operator', label: 'List Operator', icon: ListBullets },
      { type: 'parameter_extractor', label: 'Parameter Extractor', icon: Crosshair },
    ],
  },
  {
    name: 'Data Processing',
    items: [
      { type: 'document_extractor', label: 'Document Extractor', icon: FileText },
      { type: 'ocr', label: 'OCR', icon: Scan },
    ],
  },
  {
    name: 'Utilities',
    items: [
      { type: 'http', label: 'HTTP Request', icon: Globe },
      { type: 'notification', label: 'Notification', icon: Bell },
      { type: 'webhook_trigger', label: 'Webhook Trigger', icon: Link },
      { type: 'notes', label: 'Notes', icon: Note },
    ],
  },
];

export function NodeContextMenu({ x, y, onAddNode, onClose }: NodeContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ x, y });

  const adjustPosition = useCallback(() => {
    if (!menuRef.current) return;
    
    const menuRect = menuRef.current.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    
    let adjustedX = x;
    let adjustedY = y;
    
    if (x + menuRect.width > viewportWidth - 20) {
      adjustedX = viewportWidth - menuRect.width - 20;
    }
    if (y + menuRect.height > viewportHeight - 20) {
      adjustedY = viewportHeight - menuRect.height - 20;
    }
    
    if (adjustedX !== position.x || adjustedY !== position.y) {
      setPosition({ x: adjustedX, y: adjustedY });
    }
  }, [x, y, position]);

  useEffect(() => {
    adjustPosition();
  }, [adjustPosition]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        onClose();
      }
    };
    
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [onClose]);

  return (
    <div
      ref={menuRef}
      className="fixed z-[100] w-64 rounded-lg border bg-popover shadow-xl animate-in fade-in-0 zoom-in-95"
      style={{
        left: position.x,
        top: position.y,
      }}
    >
      <Command className="rounded-lg">
        <CommandInput placeholder="Search nodes..." autoFocus />
        <CommandList className="max-h-[320px] overflow-y-auto">
          <CommandEmpty>No results found.</CommandEmpty>
          {NODE_CATEGORIES.map((category) => (
            <CommandGroup key={category.name} heading={category.name}>
              {category.items.map((item) => (
                <CommandItem
                  key={item.type}
                  onSelect={() => {
                    onAddNode(item.type);
                  }}
                  className="flex items-center gap-2 cursor-pointer"
                >
                  <item.icon className="h-4 w-4 text-muted-foreground" weight="duotone" />
                  <span>{item.label}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          ))}
        </CommandList>
      </Command>
    </div>
  );
}
