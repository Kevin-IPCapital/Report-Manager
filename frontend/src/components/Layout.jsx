import { Fragment } from 'preact';
import { useLocation } from 'preact-iso';
import { useAuth } from '../auth/AuthContext';
import { useSettings } from '../state/SettingsContext';
import { useLoading } from '../state/LoadingContext';
import { navItems } from '../routes';
import { titleCase } from '../text';
import { LoadingOverlay } from './ui';

export function Layout({ children }) {
  const { path } = useLocation();
  const { user, logout } = useAuth();
  const { navCollapsed, setNavCollapsed } = useSettings();
  const { loading } = useLoading();

  return (
    <div class={`shell ${navCollapsed ? 'collapsed' : ''}`}>
      <nav class="sidebar" aria-label="Main">
        <div class="sidebar-head">
          <button class="icon-btn" aria-expanded={!navCollapsed}
            aria-label={navCollapsed ? 'Show navigation' : 'Hide navigation'} onClick={() => setNavCollapsed(!navCollapsed)}>☰</button>
          <span class="brand-lockup"><span class="brand-mark" aria-hidden="true"><span class="brand-mark-red">i</span><span class="brand-mark-blue">P</span></span><span class="brand-copy"><strong>CAPITAL</strong><small>DELIVERING EXCELLENCE</small></span></span>
        </div>
        {navItems.filter((n) => !n.staffOnly || user?.is_staff).map((n, i, items) => (
          <Fragment key={n.path}>
            {n.group !== items[i - 1]?.group && <div class="nav-label">{titleCase(n.group)}</div>}
            <a href={n.path} class={path === n.path ? 'active' : ''} aria-current={path === n.path ? 'page' : undefined}>{titleCase(n.title)}</a>
          </Fragment>
        ))}
        <div class="spacer" />
        <div class="sidebar-footer"><span class="footer-dot" aria-hidden="true"/><span class="user">{user.name}</span></div>
        <button class="link" onClick={logout}>Sign out</button>
      </nav>
      <div class="viewport">
        <div class="content">
          <main aria-busy={loading}>
            {user?.scoped && <p class="note" role="status">Your account can see only the clients you have been given access to{user.company ? ` at ${user.company.name}` : ''}, so some figures and clients are not shown. Ask an administrator if one is missing.</p>}
            {children}
          </main>
          {loading && <LoadingOverlay />}
        </div>
      </div>
    </div>
  );
}
