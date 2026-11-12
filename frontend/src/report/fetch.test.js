import assert from 'node:assert/strict';
import { test } from 'node:test';
import { validateTemplate } from './config.js';
import { collect } from './fetch.js';

const config = (blocks, extra = {}) => validateTemplate({ version: 2, blocks, ...extra });
const dates = { from: '2026-10-01', to: '2026-10-31' };
const patchTypes = { types: [
  { key: 'windows-security', label: 'Windows Security', categories: [{ category_id: 'g1' }, { category_id: null }] },
  { key: 'third-party', label: 'Third Party', categories: [{ category_id: 'g9' }] }] };

function fake() {
  const calls = [];
  const getSection = async (name, params = {}) => {
    calls.push([name, params]);
    if (name === 'patches') return patchTypes;
    if (name === 'patch-entries') return { entries: { rows: [{ title: `${params.type}/${params.category}` }] } };
    return { name };
  };
  return { calls, getSection };
}

test('ten ticket blocks cost one ticket request, and so do repeated health and backup blocks', async () => {
  const { calls, getSection } = fake();
  const blocks = [...Array(10).fill({ type: 'tickets' }), { type: 'server-health' }, { type: 'server-health' }, { type: 'backups' }, { type: 'backups' }];
  const data = await collect(config(blocks), dates, getSection);
  assert.deepEqual(calls.map((c) => c[0]), ['tickets', 'server-health', 'backups']);
  assert.deepEqual(calls[0][1], dates);
  assert.equal(Object.keys(data).length, 14);
});

test('patch blocks are fetched once per client scope, with a client or org parameter', async () => {
  const { calls, getSection } = fake();
  const blocks = [{ type: 'patches' }, { type: 'patches' }, { type: 'patches', filters: { patchScope: 'client:3' } }, { type: 'patches', filters: { patchScope: 'org:10' } }, { type: 'patches', filters: { patchScope: 'client:3' } }];
  await collect(config(blocks), dates, getSection);
  assert.deepEqual(calls.map((c) => c[1]), [{}, { client: '3' }, { org: '10' }]);
});

test('a report with its own weights sends them, and one without sends none', async () => {
  const withWeights = fake();
  await collect(config([{ type: 'patches' }], { weights: { 'third-party': 2 } }), dates, withWeights.getSection);
  assert.deepEqual(withWeights.calls[0][1], { weights: '{"third-party":2}' });
  const without = fake();
  await collect(config([{ type: 'patches' }]), dates, without.getSection);
  assert.deepEqual(without.calls[0][1], {});
});

test('a malformed scope is ignored rather than sent', async () => {
  const { calls, getSection } = fake();
  await collect(config([{ type: 'patches', filters: { patchScope: 'client:3; drop table' } }]), dates, getSection);
  assert.deepEqual(calls[0][1], {});
});

test('patch details fetch every category of the chosen type, and only that type', async () => {
  const { calls, getSection } = fake();
  const data = await collect(config([{ type: 'patch-details', filters: { patchType: 'windows-security', patchScope: 'org:10' } }]), dates, getSection);
  assert.deepEqual(calls.filter((c) => c[0] === 'patch-entries').map((c) => c[1]), [
    { type: 'windows-security', category: 'g1', org: '10' }, { type: 'windows-security', category: null, org: '10' }]);
  assert.deepEqual(data.b1.details.map((d) => [d.title, d.patch_type]), [['windows-security/g1', 'Windows Security'], ['windows-security/null', 'Windows Security']]);
});

test('patch details and patch compliance for the same scope share one patches request', async () => {
  const { calls, getSection } = fake();
  await collect(config([{ type: 'patches' }, { type: 'patch-details' }]), dates, getSection);
  assert.equal(calls.filter((c) => c[0] === 'patches').length, 1);
  assert.equal(calls.filter((c) => c[0] === 'patch-entries').length, 3);
});

test('a failed request stops the run with its error', async () => {
  const getSection = async () => { throw new Error('Server unavailable'); };
  await assert.rejects(() => collect(config([{ type: 'tickets' }]), dates, getSection), /Server unavailable/);
});

test('patch details with several patch types fetch the categories of each of them only', async () => {
  const { calls, getSection } = fake();
  await collect(config([{ type: 'patch-details', filters: { patchType: ['windows-security', 'third-party'] } }]), dates, getSection);
  assert.deepEqual(calls.filter((c) => c[0] === 'patch-entries').map((c) => c[1].type + '/' + c[1].category), ['windows-security/g1', 'windows-security/null', 'third-party/g9']);
});
