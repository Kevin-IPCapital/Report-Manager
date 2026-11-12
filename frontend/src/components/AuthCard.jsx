import { useState } from 'preact/hooks';

export const Field = ({ label, ...props }) => <label>{label}<input {...props} /></label>;

// Runs `action` on submit and tracks busy / error / success message. `action` may return a message.
export function useSubmit(action) {
  const [state, setState] = useState({ busy: false, error: '', message: '' });
  const submit = async (e) => {
    e.preventDefault();
    setState({ busy: true, error: '', message: '' });
    try { setState({ busy: false, error: '', message: (await action()) || '' }); }
    catch (err) { setState({ busy: false, error: err.message, message: '' }); }
  };
  return [state, submit];
}

export function AuthCard({ title, intro, state, onSubmit, submitLabel, children, footer }) {
  return (
    <div class="login">
      <form onSubmit={onSubmit}>
        <h1>{title}</h1>
        {intro && <p class="note">{intro}</p>}
        {children}
        {state.error && <p class="note error" role="alert">{state.error}</p>}
        {state.message && <p class="note" role="status">{state.message}</p>}
        <button class="primary" disabled={state.busy}>{state.busy ? 'Please wait…' : submitLabel}</button>
        {footer && <p class="auth-links">{footer}</p>}
      </form>
    </div>
  );
}
