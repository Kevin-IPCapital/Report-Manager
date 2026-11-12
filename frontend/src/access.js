// The Access page's logic, pure so it can be tested: checking a new person before it is sent (the same rules the server applies, so a mistake shows at once and
// the server still has the last word), what to put in a request, how a client group reads, and what to tell the administrator once a person has been made.
export const USERNAME = /^[\w.@+-]{1,150}$/;
export const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export const emptyPerson = () => ({ username: '', first_name: '', last_name: '', email: '', client_groups: [], is_staff: false, company: '' });

// A sentence saying what is wrong with a new person, or '' when it can be sent. `taken` are the usernames that already exist.
export function personProblem(draft, taken = []) {
  const username = (draft.username ?? '').trim();
  if (!username) return 'Username is required';
  if (!USERNAME.test(username)) return 'A username can only have letters, numbers and . @ + - _ (up to 150)';
  if (taken.some((t) => String(t).toLowerCase() === username.toLowerCase())) return 'That username is taken';
  const email = (draft.email ?? '').trim();
  if (!email) return 'Email is required: the link to choose a password is sent there';
  if (!EMAIL.test(email) || email.length > 254) return 'That does not look like an email address';
  return '';
}

// What is sent to make a person. The company is only sent when one was chosen (the platform owner chooses; an administrator's is their own), and being an
// administrator only when asked for. No password is ever part of it.
export function personRequest(draft) {
  const body = {
    username: draft.username.trim(), email: draft.email.trim(), first_name: (draft.first_name ?? '').trim(), last_name: (draft.last_name ?? '').trim(),
    client_groups: (draft.client_groups ?? []).map(Number),
  };
  if (draft.is_staff) body.is_staff = true;
  if (draft.company !== '' && draft.company != null) body.company = Number(draft.company);
  return body;
}

// A client group as a request: a group that is "all clients" sends no list.
export const groupRequest = (draft) => ({
  ...(draft.id ? { id: draft.id } : {}), name: draft.name.trim(), all_clients: !!draft.all_clients,
  clients: draft.all_clients ? [] : (draft.clients ?? []).map(Number), ...(draft.company !== '' && draft.company != null && !draft.id ? { company: Number(draft.company) } : {}),
});

// How a group's clients read in the table: "All clients", the names, or "No clients yet".
export function groupClients(group, clients) {
  if (group.all_clients) return 'All clients';
  if (!group.clients.length) return 'No clients yet';
  const names = new Map(clients.map((c) => [c.id, c.name]));
  return group.clients.map((id) => names.get(id) ?? `Client ${id}`).join(', ');
}

// What to tell the administrator after a person was made or given a fresh link. `sent` is what the server says happened to the email:
// sent (really sent), console (the server only prints emails, so nothing was sent), failed, or none (no email address). The link is always
// shown once, because it is how the person gets in and the administrator may have to pass it on.
export function setupNotice(result, name = '') {
  const who = name || result.username;
  const minutes = `The link works for ${result.valid_for_minutes} minutes, and once.`;
  const lines = {
    sent: `We emailed ${who} (${result.email}) a link to choose their password. ${minutes}`,
    console: `No email was sent: this server only prints emails to its console. Give ${who} the link below yourself. ${minutes}`,
    failed: `The email to ${result.email} could not be sent. Give ${who} the link below yourself. ${minutes}`,
    none: `${who} has no email address, so nothing was sent. Give them the link below yourself. ${minutes}`,
  };
  return { tone: result.sent === 'sent' ? 'good' : 'warn', text: lines[result.sent] ?? lines.failed, link: result.setup_link };
}

// The one thing on a person's row that changed, for the Save button: whether the chosen groups differ from the saved ones.
export const sameIds = (a, b) => JSON.stringify([...(a ?? [])].map(String).sort()) === JSON.stringify([...(b ?? [])].map(String).sort());
