import { useState } from 'preact/hooks';
import { useLocation } from 'preact-iso';
import { api } from '../api/adapter';
import { AuthCard, Field, useSubmit } from '../components/AuthCard';

// Opened from the emailed link: /reset?uid=...&token=...
export function ResetPassword() {
  const { query } = useLocation();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [state, submit] = useSubmit(async () => {
    if (password !== confirm) throw new Error('The passwords do not match');
    return (await api.resetPassword(query.uid, query.token, password)).message;
  });
  return (
    <AuthCard title="Reset Password" intro="Choose a new password of at least 8 characters." state={state} onSubmit={submit}
      submitLabel="Set New Password" footer={<a href="/">Back To Sign In</a>}>
      <Field label="New Password" type="password" value={password} onInput={(e) => setPassword(e.target.value)} autocomplete="new-password" autofocus />
      <Field label="Confirm Password" type="password" value={confirm} onInput={(e) => setConfirm(e.target.value)} autocomplete="new-password" />
    </AuthCard>
  );
}
