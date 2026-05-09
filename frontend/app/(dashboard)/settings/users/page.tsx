import { redirect } from 'next/navigation';

export default function UsersRedirect() {
  redirect('/settings/organization/users');
}
