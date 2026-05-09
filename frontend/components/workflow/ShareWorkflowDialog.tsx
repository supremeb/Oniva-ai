"use client";

/**
 * ShareWorkflowDialog - Enterprise workflow sharing component
 *
 * Enables sharing workflows with:
 * - Specific users (by email)
 * - Roles (admin, analyst, viewer)
 * - Entire organization
 *
 * Permission levels: view < execute < edit < admin
 */

import * as React from "react";
import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  User,
  UsersThree,
  Buildings,
  Trash,
  Share,
  Copy,
  Check,
  SpinnerGap,
  CaretDown,
  Globe,
  ShieldCheck,
  PencilSimple,
  Play,
  Eye,
} from "@phosphor-icons/react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import {
  listWorkflowPermissions,
  createWorkflowPermission,
  updateWorkflowPermission,
  deleteWorkflowPermission,
  getWorkflowShareSummary,
  WorkflowPermission,
  PermissionLevel,
  GranteeType,
  getPermissionDisplayName,
  getPermissionDescription,
} from "@/lib/api/workflow-permissions";
import { listUsers, User as UserType } from "@/lib/api/users";

interface ShareWorkflowDialogProps {
  workflowId: string;
  workflowName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const PERMISSION_LEVELS: {
  value: PermissionLevel;
  label: string;
  description: string;
  icon: React.ReactNode;
}[] = [
  {
    value: "view",
    label: "View Only",
    description: "Can view workflow details",
    icon: <Eye className="h-4 w-4" />,
  },
  {
    value: "execute",
    label: "Can Execute",
    description: "Can run the workflow",
    icon: <Play className="h-4 w-4" />,
  },
  {
    value: "edit",
    label: "Can Edit",
    description: "Can modify the workflow",
    icon: <PencilSimple className="h-4 w-4" />,
  },
  {
    value: "admin",
    label: "Full Access",
    description: "Full control including sharing",
    icon: <ShieldCheck className="h-4 w-4" />,
  },
];

const ROLE_OPTIONS = [
  { value: "admin", label: "Admins" },
  { value: "analyst", label: "Analysts" },
  { value: "viewer", label: "Viewers" },
];

export function ShareWorkflowDialog({
  workflowId,
  workflowName,
  open,
  onOpenChange,
}: ShareWorkflowDialogProps) {
  const queryClient = useQueryClient();
  const [shareType, setShareType] = useState<"user" | "role" | "organization">(
    "user"
  );
  const [selectedUserId, setSelectedUserId] = useState("");
  const [selectedRole, setSelectedRole] = useState("");
  const [selectedPermission, setSelectedPermission] =
    useState<PermissionLevel>("view");
  const [copied, setCopied] = useState(false);
  const [userSearch, setUserSearch] = useState("");

  // Fetch existing permissions
  const { data: permissions, isLoading: permissionsLoading } = useQuery({
    queryKey: ["workflow-permissions", workflowId],
    queryFn: () => listWorkflowPermissions(workflowId),
    enabled: open,
  });

  // Fetch share summary
  const { data: shareSummary } = useQuery({
    queryKey: ["workflow-share-summary", workflowId],
    queryFn: () => getWorkflowShareSummary(workflowId),
    enabled: open,
  });

  // Fetch users for user picker
  const { data: users } = useQuery({
    queryKey: ["users-list"],
    queryFn: () => listUsers({ limit: 100 }),
    enabled: open && shareType === "user",
  });

  // Create permission mutation
  const createMutation = useMutation({
    mutationFn: (data: {
      grantee_type: GranteeType;
      grantee_id: string;
      permission: PermissionLevel;
    }) => createWorkflowPermission(workflowId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workflow-permissions", workflowId],
      });
      queryClient.invalidateQueries({
        queryKey: ["workflow-share-summary", workflowId],
      });
      // Reset form
      setSelectedUserId("");
      setSelectedRole("");
      setSelectedPermission("view");
    },
  });

  // Update permission mutation
  const updateMutation = useMutation({
    mutationFn: ({
      permissionId,
      permission,
    }: {
      permissionId: string;
      permission: PermissionLevel;
    }) => updateWorkflowPermission(workflowId, permissionId, { permission }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workflow-permissions", workflowId],
      });
    },
  });

  // Delete permission mutation
  const deleteMutation = useMutation({
    mutationFn: (permissionId: string) =>
      deleteWorkflowPermission(workflowId, permissionId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workflow-permissions", workflowId],
      });
      queryClient.invalidateQueries({
        queryKey: ["workflow-share-summary", workflowId],
      });
    },
  });

  const handleShare = useCallback(() => {
    let granteeId = "";

    if (shareType === "user" && selectedUserId) {
      granteeId = selectedUserId;
    } else if (shareType === "role" && selectedRole) {
      granteeId = selectedRole;
    } else if (shareType === "organization") {
      granteeId = "organization";
    } else {
      return; // Invalid state
    }

    createMutation.mutate({
      grantee_type: shareType,
      grantee_id: granteeId,
      permission: selectedPermission,
    });
  }, [shareType, selectedUserId, selectedRole, selectedPermission, createMutation]);

  const handleCopyLink = useCallback(async () => {
    const url = `${window.location.origin}/workflows/${workflowId}`;
    await navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [workflowId]);

  const filteredUsers = users?.users?.filter(
    (u: UserType) =>
      u.name?.toLowerCase().includes(userSearch.toLowerCase()) ||
      u.email?.toLowerCase().includes(userSearch.toLowerCase())
  );

  const canShare =
    (shareType === "user" && selectedUserId) ||
    (shareType === "role" && selectedRole) ||
    shareType === "organization";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[540px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Share className="h-5 w-5" />
            Share Workflow
          </DialogTitle>
          <DialogDescription>
            Share &ldquo;{workflowName}&rdquo; with team members or your organization.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6 py-4">
          {/* Share Type Tabs */}
          <div className="flex gap-2 p-1 bg-muted rounded-lg">
            <button
              onClick={() => setShareType("user")}
              className={cn(
                "flex-1 flex items-center justify-center gap-2 py-2 px-3 rounded-md text-sm font-medium transition-colors",
                shareType === "user"
                  ? "bg-background shadow-sm"
                  : "hover:bg-background/50"
              )}
            >
              <User className="h-4 w-4" />
              User
            </button>
            <button
              onClick={() => setShareType("role")}
              className={cn(
                "flex-1 flex items-center justify-center gap-2 py-2 px-3 rounded-md text-sm font-medium transition-colors",
                shareType === "role"
                  ? "bg-background shadow-sm"
                  : "hover:bg-background/50"
              )}
            >
              <UsersThree className="h-4 w-4" />
              Role
            </button>
            <button
              onClick={() => setShareType("organization")}
              className={cn(
                "flex-1 flex items-center justify-center gap-2 py-2 px-3 rounded-md text-sm font-medium transition-colors",
                shareType === "organization"
                  ? "bg-background shadow-sm"
                  : "hover:bg-background/50"
              )}
            >
              <Buildings className="h-4 w-4" />
              Everyone
            </button>
          </div>

          {/* Share Input */}
          <div className="space-y-4">
            {shareType === "user" && (
              <div className="space-y-2">
                <Label>Select User</Label>
                <Input
                  placeholder="Search users..."
                  value={userSearch}
                  onChange={(e) => setUserSearch(e.target.value)}
                />
                <div className="max-h-40 overflow-y-auto border rounded-md">
                  {filteredUsers?.length === 0 ? (
                    <div className="p-3 text-sm text-muted-foreground text-center">
                      No users found
                    </div>
                  ) : (
                    filteredUsers?.map((u: UserType) => (
                      <button
                        key={u.id}
                        onClick={() => setSelectedUserId(u.id)}
                        className={cn(
                          "w-full flex items-center gap-3 p-3 hover:bg-muted transition-colors text-left",
                          selectedUserId === u.id && "bg-muted"
                        )}
                      >
                        <div className="h-8 w-8 rounded-full bg-primary/10 flex items-center justify-center">
                          <User className="h-4 w-4 text-primary" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="font-medium truncate">
                            {u.name || u.email}
                          </div>
                          {u.name && (
                            <div className="text-xs text-muted-foreground truncate">
                              {u.email}
                            </div>
                          )}
                        </div>
                        {selectedUserId === u.id && (
                          <Check className="h-4 w-4 text-primary" />
                        )}
                      </button>
                    ))
                  )}
                </div>
              </div>
            )}

            {shareType === "role" && (
              <div className="space-y-2">
                <Label>Select Role</Label>
                <Select value={selectedRole} onValueChange={setSelectedRole}>
                  <SelectTrigger>
                    <SelectValue placeholder="Choose a role..." />
                  </SelectTrigger>
                  <SelectContent>
                    {ROLE_OPTIONS.map((role) => (
                      <SelectItem key={role.value} value={role.value}>
                        {role.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  All users with this role will have access.
                </p>
              </div>
            )}

            {shareType === "organization" && (
              <div className="p-4 border rounded-lg bg-muted/50">
                <div className="flex items-center gap-3">
                  <Globe className="h-8 w-8 text-muted-foreground" />
                  <div>
                    <div className="font-medium">Organization-wide access</div>
                    <div className="text-sm text-muted-foreground">
                      Everyone in your organization will be able to access this
                      workflow.
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Permission Level */}
            <div className="space-y-2">
              <Label>Permission Level</Label>
              <Select
                value={selectedPermission}
                onValueChange={(v) => setSelectedPermission(v as PermissionLevel)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PERMISSION_LEVELS.map((level) => (
                    <SelectItem key={level.value} value={level.value}>
                      <div className="flex items-center gap-2">
                        {level.icon}
                        <span>{level.label}</span>
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                {
                  PERMISSION_LEVELS.find((l) => l.value === selectedPermission)
                    ?.description
                }
              </p>
            </div>

            <Button
              onClick={handleShare}
              disabled={!canShare || createMutation.isPending}
              className="w-full"
            >
              {createMutation.isPending ? (
                <>
                  <SpinnerGap className="h-4 w-4 animate-spin mr-2" />
                  Sharing...
                </>
              ) : (
                <>
                  <Share className="h-4 w-4 mr-2" />
                  Share
                </>
              )}
            </Button>
          </div>

          <Separator />

          {/* Current Shares */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <Label>People with access</Label>
              {shareSummary && (
                <Badge variant="secondary">
                  {shareSummary.share_count} share
                  {shareSummary.share_count !== 1 ? "s" : ""}
                </Badge>
              )}
            </div>

            {permissionsLoading ? (
              <div className="flex items-center justify-center py-6">
                <SpinnerGap className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : permissions?.length === 0 ? (
              <div className="text-center py-6 text-muted-foreground">
                <Share className="h-8 w-8 mx-auto mb-2 opacity-50" />
                <p className="text-sm">Not shared with anyone yet</p>
              </div>
            ) : (
              <div className="space-y-2 max-h-60 overflow-y-auto">
                {permissions?.map((perm) => (
                  <PermissionRow
                    key={perm.id}
                    permission={perm}
                    onUpdate={(level) =>
                      updateMutation.mutate({
                        permissionId: perm.id,
                        permission: level,
                      })
                    }
                    onDelete={() => deleteMutation.mutate(perm.id)}
                    isUpdating={updateMutation.isPending}
                    isDeleting={deleteMutation.isPending}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        <DialogFooter className="flex-row justify-between sm:justify-between">
          <Button variant="outline" onClick={handleCopyLink}>
            {copied ? (
              <>
                <Check className="h-4 w-4 mr-2" />
                Copied!
              </>
            ) : (
              <>
                <Copy className="h-4 w-4 mr-2" />
                Copy Link
              </>
            )}
          </Button>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// --- Permission Row Component ---

interface PermissionRowProps {
  permission: WorkflowPermission;
  onUpdate: (level: PermissionLevel) => void;
  onDelete: () => void;
  isUpdating: boolean;
  isDeleting: boolean;
}

function PermissionRow({
  permission,
  onUpdate,
  onDelete,
  isUpdating,
  isDeleting,
}: PermissionRowProps) {
  const getIcon = () => {
    switch (permission.grantee_type) {
      case "user":
        return <User className="h-4 w-4" />;
      case "role":
        return <UsersThree className="h-4 w-4" />;
      case "organization":
        return <Buildings className="h-4 w-4" />;
      default:
        return <User className="h-4 w-4" />;
    }
  };

  const getName = () => {
    if (permission.grantee_name) return permission.grantee_name;
    if (permission.grantee_type === "role")
      return `${permission.grantee_id}s`.replace(/^./, (c) => c.toUpperCase());
    if (permission.grantee_type === "organization") return "Everyone";
    return permission.grantee_id;
  };

  return (
    <div
      className={cn(
        "flex items-center gap-3 p-3 border rounded-lg",
        permission.is_expired && "opacity-50"
      )}
    >
      <div className="h-9 w-9 rounded-full bg-primary/10 flex items-center justify-center">
        {getIcon()}
      </div>
      <div className="flex-1 min-w-0">
        <div className="font-medium truncate">{getName()}</div>
        <div className="text-xs text-muted-foreground">
          {permission.grantee_type === "user" && permission.grantee_email && (
            <span>{permission.grantee_email}</span>
          )}
          {permission.grantee_type === "role" && <span>Role</span>}
          {permission.grantee_type === "organization" && (
            <span>Organization</span>
          )}
          {permission.is_expired && (
            <Badge variant="destructive" className="ml-2 text-xs">
              Expired
            </Badge>
          )}
        </div>
      </div>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="sm" className="gap-1" disabled={isUpdating}>
            {getPermissionDisplayName(permission.permission as PermissionLevel)}
            <CaretDown className="h-3 w-3" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {PERMISSION_LEVELS.map((level) => (
            <DropdownMenuItem
              key={level.value}
              onClick={() => onUpdate(level.value)}
              className={cn(
                "flex items-center gap-2",
                permission.permission === level.value && "bg-muted"
              )}
            >
              {level.icon}
              <div>
                <div className="font-medium">{level.label}</div>
                <div className="text-xs text-muted-foreground">
                  {level.description}
                </div>
              </div>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      <Button
        variant="ghost"
        size="icon"
        onClick={onDelete}
        disabled={isDeleting}
        className="text-destructive hover:text-destructive"
      >
        <Trash className="h-4 w-4" />
      </Button>
    </div>
  );
}

export default ShareWorkflowDialog;
