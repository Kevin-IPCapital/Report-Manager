import assert from 'node:assert/strict';
import { test } from 'node:test';
import { TERM_KEYS, define, hint } from './glossary.js';

// Labels with a meaning of their own on one page, as [label, page scope].
const SCOPED = [['Devices', 'backups'], ['Servers', 'backups'], ['Endpoints', 'servers'], ['Servers', 'servers'], ['SentinelOne Connected', 'servers'], ['SentinelOne Disconnected', 'servers'], ['N-central Connected', 'servers'], ['N-central Disconnected', 'servers'], ['Infected', 'servers'], ['Out Of Date', 'servers']];

// Every label a person can hover in the app. If you add a tile, column or chart, add it here and to the glossary.
const SHOWN_IN_THE_APP = [
  'Ticket Count', 'Open', 'Closed', 'Flagged Tickets', 'SLA Missed', 'Avg Resolution Time', 'Avg Response Time',
  'Ticket', 'Client', 'Type', 'Priority', 'Status', 'Issue Type', 'Sub Type', 'Technician', 'Response Time', 'Resolution Time', 'SLA Met', 'Response Met',
  'Tickets Created Per Day', 'Tickets Created Per Week (Weeks Start On Monday)', 'Data As Of', 'SentinelOne Status', 'N-central Status', 'Performance', 'Patches', 'Data Collected', 'Last Added', 'Next Due', 'Patches Up To Date',
  'Ticket By Client', 'Ticket By Priority', 'Ticket By Type', 'Ticket By Issue Type', 'Ticket By Sub Type', 'Ticket By Technician',
  'Overall Score', 'Current Score', 'Previous Score', 'Windows Security Patching', 'Windows Patching', 'Third Party Patching',
  'Category', 'Installed', 'Missing', 'Total', 'Compliance', 'Device', 'KB', 'Score Weights',
  'OK', 'Warning', 'Critical', 'Red Flags', 'Used Storage', 'Microsoft 365', 'CPU', 'RAM', 'Disk', 'Health', 'Device Type', 'Customer', 'Last Success',
  'Days Since Success', 'Retention (Days)', 'Cloud Storage', 'LSV', 'Immutable', 'Backups By Health', 'Backups By Customer', 'Backups By Device Type', 'Red Flags By Category',
  'Network Status', 'Data Age', 'Machine Type', 'OS', 'Agent Version', 'Mitigation Mode', 'Up To Date', 'Clean',
];
const NO_DEFINITION_NEEDED = ['Ticket']; // a plain identifier

test('every hoverable label has a definition', () => {
  const missing = SHOWN_IN_THE_APP.filter((t) => !NO_DEFINITION_NEEDED.includes(t) && !define(t));
  assert.deepEqual(missing, []);
});

test('definitions are short, sentences, and not placeholders', () => {
  for (const key of TERM_KEYS) {
    const text = define(key);
    assert.ok(text.length >= 20 && text.length <= 260, `${key}: ${text.length} characters`);
    assert.match(text, /[.)]$/, `${key} should end like a sentence`);
    assert.doesNotMatch(text, /TODO|TBD|lorem/i, key);
  }
});

test('lookup ignores case and surrounding spaces, and unknown terms have none', () => {
  assert.equal(define('  FLAGGED tickets '), define('flagged tickets'));
  assert.equal(define('no such thing'), undefined);
  assert.equal(define(null), undefined);
});

test('hint gives a title and a marker class only when there is a definition', () => {
  assert.deepEqual(Object.keys(hint('Flagged Tickets', 'stat-label')).sort(), ['class', 'title']);
  assert.equal(hint('Flagged Tickets', 'stat-label').class, 'stat-label has-tip');
  assert.deepEqual(hint('nothing', 'stat-label'), { class: 'stat-label' });
  assert.deepEqual(hint('nothing'), {});
});

test('definitions do not claim anything about how the sources are configured', () => {
  for (const key of ['priority', 'type', 'status', 'issue type', 'sub type']) assert.match(define(key), /Autotask/, key);
});

test('labels with a page of their own have a definition on that page', () => {
  assert.deepEqual(SCOPED.filter(([term, scope]) => !define(term, scope)), []);
});

test('the same label means different things on different pages, and an unscoped lookup does not pick either', () => {
  assert.match(define('Servers', 'backups'), /Cove/);
  assert.match(define('Servers', 'servers'), /SentinelOne/);
  assert.notEqual(define('Servers', 'backups'), define('Servers', 'servers'));
  assert.equal(define('Servers'), undefined);
  assert.equal(define('Devices'), undefined);
});

test('a scope with no special definition falls back to the general one', () => {
  assert.equal(define('Flagged Tickets', 'servers'), define('Flagged Tickets'));
  assert.equal(hint('Servers', 'stat-label', 'servers').title, define('Servers', 'servers'));
});
