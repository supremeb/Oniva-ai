'use client';

import { UserList } from '@/components/settings/UserList';
import { PendingUserList } from '@/components/settings/PendingUserList';

export default function UsersPage() {
  return (
    <div className="p-6 lg:p-8 max-w-5xl">
      <div className="mb-8">
        <h1 className="text-page-title">User Management</h1>
        <p className="text-page-description mt-1">
          Manage users and permissions for your organization
        </p>
      </div>

      <div className="space-y-8">
        <section>
          <h2 className="text-overline mb-4">Pending Invitations</h2>
          <PendingUserList />
        </section>

        <section>
          <h2 className="text-overline mb-4">Users</h2>
          <UserList />
        </section>
      </div>
    </div>
  );
}
