'use client';

import { useState } from 'react';
import {
  useUsers,
  useChangeUserRole,
  useDeactivateUser,
  useReactivateUser,
  useAdminResetPassword,
} from '@/hooks/useUsers';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  User as UserIcon,
  MoreVertical,
  Shield,
  KeyRound,
  UserX,
  UserCheck,
  Loader2,
  Search,
} from 'lucide-react';
import { toast } from 'sonner';
import { type User } from '@/lib/api/users';
import { useAuthStore } from '@/lib/stores/auth';

const ROLE_COLORS: Record<string, string> = {
  owner: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  admin: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
  member: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  viewer: 'bg-gray-100 text-gray-700 dark:bg-gray-900/30 dark:text-gray-400',
};

const ROLE_LABELS: Record<string, string> = {
  owner: 'Owner',
  admin: 'Admin',
  member: 'Member',
  viewer: 'Viewer',
};

export function UserList() {
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<string>('all');

  const { data, isLoading, error } = useUsers({
    search: search || undefined,
    role: roleFilter !== 'all' ? roleFilter : undefined,
    is_active: statusFilter === 'all' ? undefined : statusFilter === 'active',
  });

  const changeUserRole = useChangeUserRole();
  const deactivateUser = useDeactivateUser();
  const reactivateUser = useReactivateUser();
  const adminResetPassword = useAdminResetPassword();
  const currentUser = useAuthStore((state) => state.user);

  // Modal states
  const [roleModalUser, setRoleModalUser] = useState<User | null>(null);
  const [selectedRole, setSelectedRole] = useState<string>('');
  const [resetModalUser, setResetModalUser] = useState<User | null>(null);
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  const users = data?.users || [];

  const handleChangeRole = async () => {
    if (!roleModalUser || !selectedRole) return;

    try {
      await changeUserRole.mutateAsync({
        userId: roleModalUser.id,
        role: selectedRole,
      });
      toast('Role updated', { description: `${roleModalUser.email} is now a ${ROLE_LABELS[selectedRole]}.` });
      setRoleModalUser(null);
    } catch (err) {
      toast.error('Error', { description: err instanceof Error ? err.message : 'Failed to change role.' });
    }
  };

  const handleDeactivate = async (user: User) => {
    try {
      await deactivateUser.mutateAsync(user.id);
      toast('User deactivated', { description: `${user.email} has been deactivated.` });
    } catch (err) {
      toast.error('Error', { description: err instanceof Error ? err.message : 'Failed to deactivate user.' });
    }
  };

  const handleReactivate = async (user: User) => {
    try {
      await reactivateUser.mutateAsync(user.id);
      toast('User reactivated', { description: `${user.email} has been reactivated.` });
    } catch (err) {
      toast.error('Error', { description: err instanceof Error ? err.message : 'Failed to reactivate user.' });
    }
  };

  const handleResetPassword = async () => {
    if (!resetModalUser || !newPassword) return;

    if (newPassword !== confirmPassword) {
      toast.error('Error', { description: 'Passwords do not match.' });
      return;
    }

    if (newPassword.length < 8) {
      toast.error('Error', { description: 'Password must be at least 8 characters.' });
      return;
    }

    try {
      await adminResetPassword.mutateAsync({
        userId: resetModalUser.id,
        newPassword,
      });
      toast('Password reset', { description: `Password has been reset for ${resetModalUser.email}.` });
      setResetModalUser(null);
      setNewPassword('');
      setConfirmPassword('');
    } catch (err) {
      toast.error('Error', { description: err instanceof Error ? err.message : 'Failed to reset password.' });
    }
  };

  const openRoleModal = (user: User) => {
    setRoleModalUser(user);
    setSelectedRole(user.role);
  };

  if (isLoading) {
    return (
      <div className="flex justify-center p-8">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-destructive p-4">Error loading users.</div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search by name or email..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>
        <Select value={roleFilter} onValueChange={setRoleFilter}>
          <SelectTrigger className="w-[140px]">
            <SelectValue placeholder="All roles" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All roles</SelectItem>
            <SelectItem value="owner">Owner</SelectItem>
            <SelectItem value="admin">Admin</SelectItem>
            <SelectItem value="member">Member</SelectItem>
            <SelectItem value="viewer">Viewer</SelectItem>
          </SelectContent>
        </Select>
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="w-[140px]">
            <SelectValue placeholder="All status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All status</SelectItem>
            <SelectItem value="active">Active</SelectItem>
            <SelectItem value="inactive">Inactive</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* User Count */}
      <p className="text-body-muted">
        {data?.total || 0} user{(data?.total || 0) !== 1 ? 's' : ''}
      </p>

      {/* User List */}
      {users.length === 0 ? (
        <div className="text-center p-8 border border-dashed rounded-lg text-muted-foreground">
          <UserIcon className="h-8 w-8 mx-auto mb-2 opacity-50" />
          <p>No users found</p>
        </div>
      ) : (
        <div className="space-y-2">
          {users.map((user) => {
            const isCurrentUser = currentUser?.id === user.id;
            const canManage = !isCurrentUser && user.role !== 'owner';

            return (
              <Card key={user.id} className={!user.is_active ? 'opacity-60' : ''}>
                <CardContent className="flex items-center justify-between p-4">
                  <div className="flex items-center gap-3">
                    <div className="p-2 bg-muted rounded-full">
                      <UserIcon className="h-4 w-4 text-muted-foreground" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <p className="font-medium">
                          {user.name || user.email}
                        </p>
                        {isCurrentUser && (
                          <Badge variant="outline" className="text-xs">
                            You
                          </Badge>
                        )}
                        {!user.is_active && (
                          <Badge variant="secondary" className="text-xs">
                            Inactive
                          </Badge>
                        )}
                      </div>
                      <p className="text-body-muted">
                        {user.email}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <Badge className={ROLE_COLORS[user.role] || ''}>
                      {ROLE_LABELS[user.role] || user.role}
                    </Badge>

                    {canManage && (
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon">
                            <MoreVertical className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => openRoleModal(user)}>
                            <Shield className="h-4 w-4 mr-2" />
                            Change Role
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onClick={() => setResetModalUser(user)}
                          >
                            <KeyRound className="h-4 w-4 mr-2" />
                            Reset Password
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          {user.is_active ? (
                            <DropdownMenuItem
                              onClick={() => handleDeactivate(user)}
                              className="text-destructive"
                            >
                              <UserX className="h-4 w-4 mr-2" />
                              Deactivate
                            </DropdownMenuItem>
                          ) : (
                            <DropdownMenuItem
                              onClick={() => handleReactivate(user)}
                            >
                              <UserCheck className="h-4 w-4 mr-2" />
                              Reactivate
                            </DropdownMenuItem>
                          )}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    )}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {/* Change Role Modal */}
      <Dialog open={!!roleModalUser} onOpenChange={() => setRoleModalUser(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Change Role</DialogTitle>
            <DialogDescription>
              Update the role for {roleModalUser?.email}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label>Role</Label>
              <Select value={selectedRole} onValueChange={setSelectedRole}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="admin">
                    Admin - Manage users & settings
                  </SelectItem>
                  <SelectItem value="member">
                    Member - Create & edit workflows
                  </SelectItem>
                  <SelectItem value="viewer">
                    Viewer - Read-only access
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRoleModalUser(null)}>
              Cancel
            </Button>
            <Button
              onClick={handleChangeRole}
              disabled={changeUserRole.isPending || selectedRole === roleModalUser?.role}
            >
              {changeUserRole.isPending ? 'Updating...' : 'Update Role'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reset Password Modal */}
      <Dialog open={!!resetModalUser} onOpenChange={() => {
        setResetModalUser(null);
        setNewPassword('');
        setConfirmPassword('');
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reset Password</DialogTitle>
            <DialogDescription>
              Set a new password for {resetModalUser?.email}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="new-password">New Password</Label>
              <Input
                id="new-password"
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="Enter new password"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirm-new-password">Confirm Password</Label>
              <Input
                id="confirm-new-password"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Confirm new password"
              />
              {confirmPassword && newPassword !== confirmPassword && (
                <p className="text-xs text-destructive">Passwords do not match</p>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setResetModalUser(null);
                setNewPassword('');
                setConfirmPassword('');
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={handleResetPassword}
              disabled={
                adminResetPassword.isPending ||
                !newPassword ||
                newPassword !== confirmPassword
              }
            >
              {adminResetPassword.isPending ? 'Resetting...' : 'Reset Password'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
