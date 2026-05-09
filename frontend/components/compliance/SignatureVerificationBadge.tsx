'use client';

import { type FC } from 'react';
import { CheckCircle, XCircle, Shield, Clock } from '@phosphor-icons/react';

interface SignatureVerificationBadgeProps {
  status?: 'valid' | 'invalid' | 'pending' | 'none';
  className?: string;
  showLabel?: boolean;
}

export const SignatureVerificationBadge: FC<SignatureVerificationBadgeProps> = ({
  status = 'none',
  className = '',
  showLabel = true,
}) => {
  const configs = {
    valid: {
      icon: CheckCircle,
      label: 'Verified',
      className: 'bg-muted text-foreground border-border',
      iconClassName: 'text-foreground',
    },
    invalid: {
      icon: XCircle,
      label: 'Invalid',
      className: 'bg-muted text-muted-foreground border-border',
      iconClassName: 'text-muted-foreground',
    },
    pending: {
      icon: Clock,
      label: 'Pending',
      className: 'bg-muted text-muted-foreground border-border',
      iconClassName: 'text-muted-foreground',
    },
    none: {
      icon: Shield,
      label: 'Unsigned',
      className: 'bg-muted text-muted-foreground border-border',
      iconClassName: 'text-muted-foreground',
    },
  };

  const config = configs[status];
  const Icon = config.icon;

  return (
    <div
      className={`
        inline-flex items-center gap-1.5 px-2 py-0.5
        rounded-md border
        transition-colors duration-200
        ${config.className}
        ${className}
      `}
    >
      <Icon weight="duotone" className={`h-3.5 w-3.5 ${config.iconClassName}`} />
      {showLabel && (
        <span className="text-[10px] font-medium tracking-wider uppercase">
          {config.label}
        </span>
      )}
    </div>
  );
};
