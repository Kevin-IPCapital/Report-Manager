// The Overview's client filter: which clients are chosen, and how that becomes a request. Pure, so it can be tested.
// A choice is "client:<id>" (a master client) or "org:<id>" (an N-central customer not linked to a client yet), as the server names them.
const CHOICE = /^(client|org):\d{1,12}$/;
export const MAX_CHOICES = 50;

// Is this a list of choices we could have saved? (Anything else in storage is ignored.)
export const isChoices = (v) => Array.isArray(v) && v.length <= MAX_CHOICES && v.every((k) => typeof k === 'string' && CHOICE.test(k)) && new Set(v).size === v.length;

// The request parameters for a list of choices: {client: '1,2', org: '30'}, leaving out whichever there are none of.
export function scopeParams(choices) {
  const ids = (prefix) => choices.filter((k) => k.startsWith(`${prefix}:`)).map((k) => k.slice(prefix.length + 1));
  const clients = ids('client'), orgs = ids('org');
  return { ...(clients.length ? { client: clients.join(',') } : {}), ...(orgs.length ? { org: orgs.join(',') } : {}) };
}

// Clicking a name in the clients card: adds it, or removes it if it is already chosen.
export const toggleChoice = (choices, key) => (choices.includes(key) ? choices.filter((k) => k !== key) : [...choices, key].slice(0, MAX_CHOICES));

// The Overall Score and the trend are for ONE client: a combined figure for several (or for everyone) means nothing, so with none chosen
// or several chosen the page says this instead of showing one.
export const CHOOSE_ONE = 'Please select a single client';
export const singleChoice = (choices) => (choices.length === 1 ? choices[0] : null);

// Choices that still exist: a remembered client that was renamed, unlinked or removed is dropped.
export const keepKnown = (choices, options) => choices.filter((key) => options.some((o) => o.key === key));

export const labelOf = (key, options) => options.find((o) => o.key === key)?.label ?? key;

// What a click on a measure (a type row) does. A row in a client's own group first narrows the page to that client; choosing the measure
// that is already shown, in the scope it is already shown for, switches it off again.
export function pickType({ choices, type }, key, clientKey) {
  const sameScope = !clientKey || (choices.length === 1 && choices[0] === clientKey);
  if (sameScope) return { choices, type: type === key ? null : key };
  return { choices: [clientKey], type: key };
}
