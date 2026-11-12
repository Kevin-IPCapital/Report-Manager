import { useState } from 'preact/hooks';
import { api } from '../api/adapter';
import { AuthCard, Field, useSubmit } from '../components/AuthCard';

export function ForgotPassword() {
  const [identifier, setIdentifier] = useState('');
  const [state, submit] = useSubmit(async () => (await api.forgotPassword(identifier)).message);
  return (
    <AuthCard title="Forgot Password" intro="Enter your username or email and we will send you a reset link." state={state} onSubmit={submit}
      submitLabel="Send Reset Link" footer={<a href="/">Back To Sign In</a>}>
      <Field label="Username Or Email" value={identifier} onInput={(e) => setIdentifier(e.target.value)} autofocus />
    </AuthCard>
  );
}
