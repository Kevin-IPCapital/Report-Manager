import { DIM, TicketExplorer } from '../components/TicketExplorer';

const DIMENSIONS = [DIM.client, DIM.priority, DIM.type, DIM.issue, DIM.subtype];

// Tickets by client. One request returns every ticket for the period; see TicketExplorer.
export function Tickets({ title }) {
  return <TicketExplorer title={title} dimensions={DIMENSIONS} persistKey="tickets" />;
}
