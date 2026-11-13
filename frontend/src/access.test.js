import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ACCESS_LEVELS, ACCESS_TABS, accessLabel, accessOf, accessRequest, emptyPerson, groupClients, groupRequest, personProblem, personRequest, sameIds, setupNotice } from './access.js';

const ok = { ...emptyPerson(), username: 'thandi', email: 'thandi@acme.test' };

test('a person needs a username and an email, and nothing else', () => {
  assert.equal(personProblem(ok), '');
  assert.equal(personProblem({ ...ok, username: '' }), 'Username is required');
  assert.equal(personProblem({ ...ok, username: '   ' }), 'Username is required');
  assert.equal(personProblem({ ...ok, email: '' }), 'Email is required: the link to choose a password is sent there');
});

test('the username rules are the server\'s', () => {
  assert.equal(personProblem({ ...ok, username: 'has space' }), 'A username can only have letters, numbers and . @ + - _ (up to 150)');
  assert.equal(personProblem({ ...ok, username: 'x'.repeat(151) }), 'A username can only have letters, numbers and . @ + - _ (up to 150)');
  assert.equal(personProblem({ ...ok, username: 'thandi.n@acme-1' }), '');
  assert.equal(personProblem({ ...ok, username: 'THANDI' }, ['thandi']), 'That username is taken');                  // whatever the case
  assert.equal(personProblem({ ...ok, username: ' thandi ' }, ['Thandi']), 'That username is taken');
});

test('the email rules are the server\'s', () => {
  for (const bad of ['thandi', 'a@b', '@acme.test', 'a b@acme.test', 'a'.repeat(250) + '@b.co']) assert.equal(personProblem({ ...ok, email: bad }), 'That does not look like an email address', bad);
  assert.equal(personProblem({ ...ok, email: ' thandi@acme.test ' }), '');
});

test('what is sent to make a person is tidy, has no password, and sends rank and company only when chosen', () => {
  const body = personRequest({ ...ok, username: ' thandi ', first_name: ' Thandi ', client_groups: ['11', '10'] });
  assert.deepEqual(body, { username: 'thandi', email: 'thandi@acme.test', first_name: 'Thandi', last_name: '', client_groups: [11, 10] });
  assert.ok(!('password' in body) && !('is_staff' in body) && !('company' in body));
  assert.deepEqual(personRequest({ ...ok, access: 'admin', company: '3' }), { username: 'thandi', email: 'thandi@acme.test', first_name: '', last_name: '', client_groups: [], is_staff: true, company: 3 });
});

test('a client group that is all clients sends no list, and a new one sends its company when chosen', () => {
  assert.deepEqual(groupRequest({ name: ' Retail ', all_clients: true, clients: ['1', '2'] }), { name: 'Retail', all_clients: true, clients: [] });
  assert.deepEqual(groupRequest({ name: 'Retail', all_clients: false, clients: ['2', '1'] }), { name: 'Retail', all_clients: false, clients: [2, 1] });
  assert.deepEqual(groupRequest({ name: 'Retail', clients: [], company: '2' }), { name: 'Retail', all_clients: false, clients: [], company: 2 });
  assert.deepEqual(groupRequest({ id: 7, name: 'Retail', clients: [1], company: '2' }), { id: 7, name: 'Retail', all_clients: false, clients: [1] });   // an existing group never moves company
});

test('a group\'s clients read as all clients, as names, or as none yet', () => {
  const clients = [{ id: 1, name: 'Acme' }, { id: 2, name: 'Apex' }];
  assert.equal(groupClients({ all_clients: true, clients: [] }, clients), 'All clients');
  assert.equal(groupClients({ all_clients: false, clients: [2, 1] }, clients), 'Apex, Acme');
  assert.equal(groupClients({ all_clients: false, clients: [] }, clients), 'No clients yet');
  assert.equal(groupClients({ all_clients: false, clients: [9] }, clients), 'Client 9');                          // one the viewer cannot see is not hidden or invented
});

test('what to tell the administrator depends on what really happened to the email, and the link is always there', () => {
  const result = (sent) => ({ username: 'thandi', email: 'thandi@acme.test', setup_link: 'http://x/reset?uid=1&token=t', valid_for_minutes: 60, sent });
  const sent = setupNotice(result('sent'), 'Thandi');
  assert.equal(sent.tone, 'good');
  assert.match(sent.text, /We emailed Thandi \(thandi@acme\.test\)/);
  assert.match(sent.text, /60 minutes, and once/);
  const console_ = setupNotice(result('console'), 'Thandi');
  assert.equal(console_.tone, 'warn');
  assert.match(console_.text, /No email was sent: this server only prints emails to its console/);
  assert.match(setupNotice(result('failed'), 'Thandi').text, /could not be sent/);
  assert.match(setupNotice(result('none'), 'Thandi').text, /no email address/);
  for (const kind of ['sent', 'console', 'failed', 'none']) assert.equal(setupNotice(result(kind)).link, 'http://x/reset?uid=1&token=t');
  assert.match(setupNotice(result('console')).text, /Give thandi the link/);                                  // with no name, the username is used
  assert.equal(setupNotice(result('surprise')).tone, 'warn');                                                   // anything unexpected is not claimed as sent
});

test('two lists of IDs are the same whatever their order or type', () => {
  assert.equal(sameIds([1, 2], ['2', '1']), true);
  assert.equal(sameIds([1], [1, 2]), false);
  assert.equal(sameIds(undefined, []), true);
});

test('the page has three tabs, in the order the owner asked for', () => {
  assert.deepEqual(ACCESS_TABS, [['overview', 'Overview'], ['employees', 'Employees'], ['groups', 'Client Groups']]);
});

test('access control has two levels for now, Employee and Admin, and a new person starts as an Employee', () => {
  assert.deepEqual(ACCESS_LEVELS, [['employee', 'Employee'], ['admin', 'Admin']]);
  assert.equal(emptyPerson().access, 'employee');
  assert.ok(!('is_staff' in emptyPerson()));
});

test('a person\'s access is read from what the server says, with the platform owner above both levels', () => {
  assert.equal(accessOf({ is_owner: true, is_staff: true }), 'owner');
  assert.equal(accessOf({ is_owner: false, is_staff: true }), 'admin');
  assert.equal(accessOf({ is_owner: false, is_staff: false }), 'employee');
  assert.deepEqual(['owner', 'admin', 'employee', 'mystery'].map(accessLabel), ['Owner', 'Admin', 'Employee', 'mystery']);
});

test('changing a level sends only whether the person is an administrator', () => {
  assert.deepEqual(accessRequest('admin'), { is_staff: true });
  assert.deepEqual(accessRequest('employee'), { is_staff: false });
  assert.deepEqual(accessRequest('anything else'), { is_staff: false });                    // nothing unexpected makes anyone an administrator
});

test('a new Employee sends no rank, and a new Admin sends it', () => {
  assert.ok(!('is_staff' in personRequest({ ...ok, access: 'employee' })));
  assert.equal(personRequest({ ...ok, access: 'admin' }).is_staff, true);
});
