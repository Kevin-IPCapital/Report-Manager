import { DIM, TicketExplorer } from '../components/TicketExplorer';

const DIMENSIONS = [DIM.technician, DIM.priority, DIM.client, DIM.type, DIM.subtype];

// The same tickets as the client page, led by who they are assigned to. The technician chart and filter appear only when the
// source collects assignments; until then the page says so and everything else still works.
export function TechnicianTickets({ title }) {
  return <TicketExplorer title={title} dimensions={DIMENSIONS} persistKey="technicians" />;
}
