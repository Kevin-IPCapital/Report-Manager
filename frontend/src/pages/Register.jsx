import { useState } from 'preact/hooks';
import { useAuth } from '../auth/AuthContext';
import { AuthCard, Field, useSubmit } from '../components/AuthCard';

export function Register() {
  const { register } = useAuth();
  const [f, setF] = useState({ username: '', email: '', password: '', confirm: '' });
  const set = (key) => (e) => setF({ ...f, [key]: e.target.value });
  const [state, submit] = useSubmit(async () => {
    if (f.password !== f.confirm) throw new Error('The passwords do not match');
    await register(f.username, f.email, f.password);
  });
  return (
    <AuthCard title="Create Account" intro="Choose a username and a password of at least 8 characters." state={state} onSubmit={submit}
      submitLabel="Create Account" footer={<>Already registered? <a href="/">Sign In</a></>}>
      <Field label="Username" value={f.username} onInput={set('username')} autocomplete="username" autofocus />
      <Field label="Email" type="email" value={f.email} onInput={set('email')} autocomplete="email" />
      <Field label="Password" type="password" value={f.password} onInput={set('password')} autocomplete="new-password" />
      <Field label="Confirm Password" type="password" value={f.confirm} onInput={set('confirm')} autocomplete="new-password" />
    </AuthCard>
  );
}
