import {
  Robot,
  ChatCircle,
  Database,
  ChartLineUp,
  ShieldCheck,
  BookOpenText,
  IdentificationCard,
  Warning,
  FileText,
} from '@phosphor-icons/react';
import type { Icon } from '@phosphor-icons/react';

const ICON_MAP: Record<string, Icon> = {
  Robot,
  ChatCircle,
  Database,
  ChartLineUp,
  ShieldCheck,
  BookOpenText,
  IdentificationCard,
  Warning,
  FileText,
};

/**
 * Resolve an icon name string (e.g. "Database") to its Phosphor icon component.
 * Returns Robot as the default fallback.
 */
export function getAgentIcon(name: string | undefined | null): Icon {
  if (!name) return Robot;
  return ICON_MAP[name] ?? Robot;
}
