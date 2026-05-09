'use client';

import { useState, useCallback, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Badge } from '@/components/ui/badge';
import { Spinner } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';

interface PublishDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPublish: (versionTag: string, description: string) => Promise<void>;
  suggestedVersions: {
    patch: string;
    minor: string;
    major: string;
  };
  currentVersion?: string | null;
  isPublishing?: boolean;
}

type ChangeType = 'patch' | 'minor' | 'major' | 'custom';

export function PublishDialog({
  open,
  onOpenChange,
  onPublish,
  suggestedVersions,
  currentVersion,
  isPublishing = false,
}: PublishDialogProps) {
  const [changeType, setChangeType] = useState<ChangeType>('minor');
  const [customVersion, setCustomVersion] = useState('');
  const [description, setDescription] = useState('');
  const [error, setError] = useState<string | null>(null);

  // Reset state when dialog opens
  useEffect(() => {
    if (open) {
      setChangeType('minor');
      setCustomVersion('');
      setDescription('');
      setError(null);
    }
  }, [open]);

  const getSelectedVersion = useCallback(() => {
    if (changeType === 'custom') {
      return customVersion;
    }
    return suggestedVersions[changeType];
  }, [changeType, customVersion, suggestedVersions]);

  const handlePublish = async () => {
    const version = getSelectedVersion();
    if (!version || version.trim() === '') {
      setError('Please enter a valid version number');
      return;
    }

    // Validate semantic version format
    const semverRegex = /^\d+\.\d+\.\d+$/;
    if (!semverRegex.test(version)) {
      setError('Version must be in MAJOR.MINOR.PATCH format (e.g., 1.0.0)');
      return;
    }

    setError(null);
    try {
      await onPublish(version, description);
      onOpenChange(false);
    } catch (err: unknown) {
      const errorMessage = (err as { message?: string })?.message || 'Failed to publish';
      setError(errorMessage);
    }
  };

  const changeTypeOptions = [
    {
      value: 'patch' as const,
      label: 'Patch',
      version: suggestedVersions.patch,
      description: 'Bug fixes, minor tweaks',
    },
    {
      value: 'minor' as const,
      label: 'Minor',
      version: suggestedVersions.minor,
      description: 'New features, backward compatible',
      recommended: true,
    },
    {
      value: 'major' as const,
      label: 'Major',
      version: suggestedVersions.major,
      description: 'Breaking changes, significant restructure',
    },
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>Publish Workflow</DialogTitle>
          <DialogDescription>
            {currentVersion ? (
              <>Current version: <strong>v{currentVersion}</strong></>
            ) : (
              'This will be the first published version'
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6 py-4">
          {/* Change Type Selection */}
          <div className="space-y-3">
            <Label className="text-sm font-medium">
              What kind of changes did you make?
            </Label>
            <RadioGroup
              value={changeType}
              onValueChange={(v: string) => setChangeType(v as ChangeType)}
              className="space-y-2"
            >
              {changeTypeOptions.map((option) => (
                <label
                  key={option.value}
                  className={cn(
                    'flex items-center justify-between rounded-lg border p-3 cursor-pointer transition-colors',
                    changeType === option.value
                      ? 'border-primary bg-primary/5'
                      : 'border-border hover:border-primary/50 hover:bg-muted/50'
                  )}
                >
                  <div className="flex items-center gap-3">
                    <RadioGroupItem value={option.value} />
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{option.label}</span>
                        <code className="text-xs bg-muted px-1.5 py-0.5 rounded">
                          v{option.version}
                        </code>
                        {option.recommended && (
                          <Badge variant="secondary" className="text-[10px] h-4">
                            Recommended
                          </Badge>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {option.description}
                      </p>
                    </div>
                  </div>
                </label>
              ))}

              {/* Custom version option */}
              <label
                className={cn(
                  'flex items-center justify-between rounded-lg border p-3 cursor-pointer transition-colors',
                  changeType === 'custom'
                    ? 'border-primary bg-primary/5'
                    : 'border-border hover:border-primary/50 hover:bg-muted/50'
                )}
              >
                <div className="flex items-center gap-3 flex-1">
                  <RadioGroupItem value="custom" />
                  <div className="flex-1">
                    <span className="font-medium">Custom version</span>
                    {changeType === 'custom' && (
                      <div className="mt-2">
                        <Input
                          value={customVersion}
                          onChange={(e) => setCustomVersion(e.target.value)}
                          placeholder="e.g., 1.5.0"
                          className="h-8 text-sm"
                          onClick={(e) => e.stopPropagation()}
                        />
                      </div>
                    )}
                  </div>
                </div>
              </label>
            </RadioGroup>
          </div>

          {/* Version Notes */}
          <div className="space-y-2">
            <Label htmlFor="description" className="text-sm font-medium">
              Version notes (optional)
            </Label>
            <Textarea
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Describe what changed in this version..."
              className="min-h-[80px] resize-none"
            />
          </div>

          {/* Error Message */}
          {error && (
            <div className="text-sm text-destructive bg-destructive/10 px-3 py-2 rounded-md">
              {error}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isPublishing}
          >
            Cancel
          </Button>
          <Button
            onClick={handlePublish}
            disabled={isPublishing || (changeType === 'custom' && !customVersion)}
          >
            {isPublishing ? (
              <>
                <Spinner className="h-4 w-4 mr-2 animate-spin" />
                Publishing...
              </>
            ) : (
              <>Publish v{getSelectedVersion()}</>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
