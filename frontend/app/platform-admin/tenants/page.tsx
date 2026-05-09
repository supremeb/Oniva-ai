'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Users, MagnifyingGlass } from '@phosphor-icons/react';
import { listTenants, type TenantSummary } from '@/lib/api/platform-admin';

/**
 * Platform Admin — Tenant List page.
 * Shows all tenants with search, status, and feature override count.
 */
export default function TenantsListPage() {
  const [tenants, setTenants] = useState<TenantSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listTenants()
      .then(setTenants)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const filtered = tenants.filter(
    (t) =>
      t.name.toLowerCase().includes(search.toLowerCase()) ||
      t.slug.toLowerCase().includes(search.toLowerCase())
  );

  if (loading) {
    return <div className="text-muted-foreground">Loading tenants...</div>;
  }

  if (error) {
    return <div className="text-destructive">Error: {error}</div>;
  }

  return (
    <div className="max-w-4xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-xl font-semibold flex items-center gap-2">
            <Users className="h-5 w-5" />
            Tenants
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            {tenants.length} organizations
          </p>
        </div>
      </div>

      {/* Search */}
      <div className="relative mb-4">
        <MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <input
          type="text"
          placeholder="Search tenants..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full pl-9 pr-4 py-2 text-sm border border-border rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
      </div>

      {/* Tenant Table */}
      <div className="border border-border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left px-4 py-3 font-medium text-muted-foreground">Name</th>
              <th className="text-left px-4 py-3 font-medium text-muted-foreground">Slug</th>
              <th className="text-center px-4 py-3 font-medium text-muted-foreground">Status</th>
              <th className="text-center px-4 py-3 font-medium text-muted-foreground">Feature Overrides</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {filtered.map((tenant) => (
              <tr key={tenant.id} className="hover:bg-muted/30 transition-colors">
                <td className="px-4 py-3">
                  <Link
                    href={`/platform-admin/tenants/${tenant.id}`}
                    className="font-medium text-foreground hover:text-primary transition-colors"
                  >
                    {tenant.name}
                  </Link>
                </td>
                <td className="px-4 py-3 text-muted-foreground font-mono text-xs">
                  {tenant.slug}
                </td>
                <td className="px-4 py-3 text-center">
                  <span
                    className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                      tenant.is_active
                        ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400'
                        : 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400'
                    }`}
                  >
                    {tenant.is_active ? 'Active' : 'Inactive'}
                  </span>
                </td>
                <td className="px-4 py-3 text-center text-muted-foreground">
                  {tenant.feature_count > 0 ? (
                    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-primary/10 text-primary">
                      {tenant.feature_count}
                    </span>
                  ) : (
                    <span className="text-xs">defaults</span>
                  )}
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-muted-foreground">
                  No tenants found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
