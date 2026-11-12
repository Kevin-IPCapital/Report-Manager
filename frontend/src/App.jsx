import { useEffect } from 'preact/hooks';
import { LocationProvider, Router, Route, useLocation } from 'preact-iso';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { SettingsProvider } from './state/SettingsContext';
import { LoadingProvider } from './state/LoadingContext';
import { DateRangeProvider } from './state/DateRangeContext';
import { SavedRangesProvider } from './state/SavedRangesContext';
import { Layout } from './components/Layout';
import { Login } from './pages/Login';
import { Register } from './pages/Register';
import { ForgotPassword } from './pages/ForgotPassword';
import { ResetPassword } from './pages/ResetPassword';
import { routes } from './routes';

const NotFound = () => <p class="note">Page not found.</p>;

// Signed-in users who land on an account page (for example straight after registering) are sent to the Overview.
function GoHome() {
  const { route } = useLocation();
  useEffect(() => { route('/', true); }, []);
  return null;
}

function Shell() {
  const { user, ready } = useAuth();
  if (!ready) return null;
  if (!user) {
    return (
      <Router>
        <Route path="/register" component={Register} />
        <Route path="/forgot" component={ForgotPassword} />
        <Route path="/reset" component={ResetPassword} />
        <Route default component={Login} />
      </Router>
    );
  }
  // The date range and the date ranges a person saves are remembered for each person, so they sit inside the signed-in area and start afresh for whoever signs in.
  return (
    <DateRangeProvider key={user.username}>
      <SavedRangesProvider>
        <Layout>
          <Router>
            {routes.filter((r) => !r.staffOnly || user?.is_staff).map((r) => <Route key={r.path} path={r.path} component={r.component} title={r.title} />)}
            <Route path="/register" component={GoHome} />
            <Route path="/forgot" component={GoHome} />
            <Route path="/reset" component={GoHome} />
            <Route default component={NotFound} />
          </Router>
        </Layout>
      </SavedRangesProvider>
    </DateRangeProvider>
  );
}

export const App = () => (
  <SettingsProvider>
    <LoadingProvider>
      <AuthProvider>
        <LocationProvider><Shell /></LocationProvider>
      </AuthProvider>
    </LoadingProvider>
  </SettingsProvider>
);
