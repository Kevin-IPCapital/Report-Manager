import { Patches } from './pages/Patches';
import { ServerHealth } from './pages/ServerHealth';
import { Backups } from './pages/Backups';
import { Tickets } from './pages/Tickets';
import { TechnicianTickets } from './pages/TechnicianTickets';
import { Access } from './pages/Access';
import { Clients } from './pages/Clients';
import { ClientResolution } from './pages/ClientResolution';
import { Settings } from './pages/Settings';
import { CollectionHealth } from './pages/CollectionHealth';
import { Reports } from './pages/Reports';

// Add a page = add a line. `group` is the heading it sits under in the sidebar.
export const routes = [
  { group: 'Reports', path: '/', title: 'Overview', component: Patches },
  { group: 'Reports', path: '/server-health', title: 'Server Health', component: ServerHealth },
  { group: 'Reports', path: '/backups', title: 'Backups', component: Backups },
  { group: 'Reports', path: '/tickets', title: 'Client Tickets', component: Tickets },
  { group: 'Reports', path: '/technicians', title: 'Technician Tickets', component: TechnicianTickets },
  { group: 'Reports', path: '/reports', title: 'Reports', component: Reports },
  { group: 'Manage', path: '/collection-health', title: 'Data Collection', component: CollectionHealth, staffOnly: true },
  { group: 'Manage', path: '/clients', title: 'Clients', component: Clients, staffOnly: true },
  { group: 'Manage', path: '/clients/resolution', title: 'Client Resolution', component: ClientResolution, staffOnly: true },
  { group: 'Manage', path: '/access', title: 'Access', component: Access, staffOnly: true },
  { group: 'Preferences', path: '/settings', title: 'Settings', component: Settings },
];
export const navItems = routes;
