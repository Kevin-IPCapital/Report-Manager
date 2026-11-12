import { useEffect, useState } from 'preact/hooks';
import { useLoading } from '../state/LoadingContext';

// Runs an adapter call, tracks loading/error, and drives the global loading overlay.
// Previous data stays in place while refetching, so the overlay blurs it rather than a blank page.
export function useApi(call, deps) {
  const { track } = useLoading();
  const [state, setState] = useState({ data: null, error: null });
  useEffect(() => {
    let live = true;
    const done = track();
    setState((s) => ({ ...s, error: null }));
    call()
      .then((data) => live && setState({ data, error: null }), (error) => live && setState({ data: null, error }))
      .finally(done);
    return () => { live = false; done(); };
  }, deps);
  return state;
}
