import { redirect } from 'next/navigation';

export default function AdminRedirect() {
  redirect('/settings/organization/overview');
}
