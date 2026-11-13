// The Clients table's "Linked Customers" column: the names of the customers a client is linked to, and not their IDs (owner, 12 Nov 2026). The IDs are still on the
// edit form, where they are needed to make a link. Pure, so it can be tested.

// Each customer's name once, in the order of the links (the same name in two systems is shown once). A link whose name could not be read, because its source system
// was unavailable or the customer is gone, says which system it is and that the name is not available: it never shows an ID and never makes a name up.
export function linkedCustomers(sources, labels = {}) {
  const names = (sources ?? []).map((s) => s.customer_name || `${labels[s.source] ?? s.source}: name not available`);
  return [...new Set(names)].join(' · ');
}
