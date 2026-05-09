'use client';

import { useState } from 'react';
import { usePendingUsers, useAddPendingUser, useDeletePendingUser } from '@/hooks/useUsers';
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
import { Trash2, Plus, UserPlus, Loader2, Clock } from 'lucide-react';
import { toast } from 'sonner';
import { type PendingUser } from '@/lib/api/users';
import { parseTimestamp } from '@/lib/utils';

const ROLE_COLORS: Record<string, string> = {
  admin: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
  member: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  viewer: 'bg-gray-100 text-gray-700 dark:bg-gray-900/30 dark:text-gray-400',
};

export function PendingUserList() {
  const { data, isLoading, error } = usePendingUsers();
  const addPendingUser = useAddPendingUser();
  const deletePendingUser = useDeletePendingUser();

  const [showAddModal, setShowAddModal] = useState(false);
  const [newEmail, setNewEmail] = useState('');
  const [newRole, setNewRole] = useState('member');

  const pendingUsers = data?.pending_users || [];

  const handleAdd = async () => {
    if (!newEmail) return;

    try {
      await addPendingUser.mutateAsync({ email: newEmail, role: newRole });
      toast('User invited', { description: `${newEmail} can now sign up with the ${newRole} role.` });
      setShowAddModal(false);
      setNewEmail('');
      setNewRole('member');
    } catch (err) {
      toast.error('Error', { description: err instanceof Error ? err.message : 'Failed to add user.' });
    }
  };

  const handleDelete = async (id: string, email: string) => {
    try {
      await deletePendingUser.mutateAsync(id);
      toast('Invitation removed', { description: `${email} can no longer sign up.` });
    } catch (err) {
      toast.error('Error', { description: 'Failed to remove invitation.' });
    }
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
      <div className="text-destructive p-4">Error loading pending users.</div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <p className="text-sm text-muted-foreground">
          {pendingUsers.length} pending invitation{pendingUsers.length !== 1 ? 's' : ''}
        </p>
        <Button onClick={() => setShowAddModal(true)}>
          <Plus className="h-4 w-4 mr-2" />
          Invite User
        </Button>
      </div>

      {pendingUsers.length === 0 ? (
        <div className="text-center p-8 border border-dashed rounded-lg text-muted-foreground">
          <UserPlus className="h-8 w-8 mx-auto mb-2 opacity-50" />
          <p>No pending invitations</p>
          <p className="text-xs mt-1">Invite users to let them sign up for your organization</p>
        </div>
      ) : (
        <div className="space-y-2">
          {pendingUsers.map((user) => (
            <Card key={user.id}>
              <CardContent className="flex items-center justify-between p-4">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-muted rounded-full">
                    <Clock className="h-4 w-4 text-muted-foreground" />
                  </div>
                  <div>
                    <p className="font-medium">{user.email}</p>
                    <p className="text-xs text-muted-foreground">
                      Invited {parseTimestamp(user.created_at).toLocaleDateString()}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <Badge className={ROLE_COLORS[user.role] || ''}>
                    {user.role}
                  </Badge>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => handleDelete(user.id, user.email)}
                    disabled={deletePendingUser.isPending}
                    className="text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Add User Modal */}
      <Dialog open={showAddModal} onOpenChange={setShowAddModal}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Invite User</DialogTitle>
            <DialogDescription>
              Add an email address to allow someone to sign up for your organization.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="email">Email Address</Label>
              <Input
                id="email"
                type="email"
                placeholder="user@company.com"
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="role">Role</Label>
              <Select value={newRole} onValueChange={setNewRole}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a role" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="admin">Admin - Manage users & settings</SelectItem>
                  <SelectItem value="member">Member - Create & edit workflows</SelectItem>
                  <SelectItem value="viewer">Viewer - Read-only access</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                This role will be assigned when the user completes signup.
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowAddModal(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleAdd}
              disabled={!newEmail || addPendingUser.isPending}
            >
              {addPendingUser.isPending ? 'Inviting...' : 'Send Invitation'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
