import { useState } from 'preact/hooks';
import { useAuth } from '../auth/AuthContext';
import { AuthCard, Field, useSubmit } from '../components/AuthCard';

export function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [state, submit] = useSubmit(() => login(username, password));
  return (
    <AuthCard title="Report Manager" intro="Sign in to see this month’s report." state={state} onSubmit={submit} submitLabel="Sign In"
      footer={<><a href="/register">Create An Account</a> · <a href="/forgot">Forgot Password?</a></>}>
      <Field label="Username" value={username} onInput={(e) => setUsername(e.target.value)} autocomplete="username" autofocus />
      <Field label="Password" type="password" value={password} onInput={(e) => setPassword(e.target.value)} autocomplete="current-password" />
    </AuthCard>
  );
}
