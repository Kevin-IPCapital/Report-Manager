import assert from 'node:assert/strict';
import { test } from 'node:test';
import { isTemplate, reportActions, splitReports } from './lists.js';

const report = (extra) => ({ id: 1, name: 'R', shared: false, mine: false, ...extra });
const admin = { is_staff: true }, person = { is_staff: false };

test('shared reports are templates and the rest are personal', () => {
  assert.equal(isTemplate(report({ shared: true })), true);
  assert.equal(isTemplate(report({ shared: false })), false);
  assert.equal(isTemplate(report({ shared: undefined })), false);
});

test('the list is split into personal reports and templates, keeping the order', () => {
  const list = [report({ id: 1 }), report({ id: 2, shared: true }), report({ id: 3 }), report({ id: 4, shared: true, mine: true })];
  const { mine, templates } = splitReports(list);
  assert.deepEqual(mine.map((r) => r.id), [1, 3]);
  assert.deepEqual(templates.map((r) => r.id), [2, 4]);
  assert.deepEqual(splitReports([]), { mine: [], templates: [] });
});

test('anyone can generate, copy and export what they can see', () => {
  for (const user of [person, admin, null]) {
    const actions = reportActions(report({ shared: true }), user);
    assert.deepEqual([actions.generate, actions.copy, actions.export], [true, true, true]);
  }
});

test('a person changes their own reports and cannot change a template someone else made', () => {
  assert.deepEqual([reportActions(report({ mine: true }), person).edit, reportActions(report({ mine: true }), person).remove], [true, true]);
  const others = reportActions(report({ shared: true, mine: false }), person);
  assert.deepEqual([others.edit, others.remove], [false, false]);
});

test('an administrator can change any template, and so can its creator', () => {
  assert.equal(reportActions(report({ shared: true, mine: false }), admin).edit, true);
  assert.equal(reportActions(report({ shared: true, mine: true }), admin).edit, true);
});

test('someone who is not signed in as anyone gets no rights to change anything', () => {
  assert.equal(reportActions(report({ shared: true }), undefined).edit, false);
  assert.equal(reportActions(report({ shared: true }), null).remove, false);
});

test('copying a template says it goes to your own reports', () => {
  assert.equal(reportActions(report({ shared: true }), person).copyLabel, 'Copy To My Reports');
  assert.equal(reportActions(report({ shared: false, mine: true }), person).copyLabel, 'Copy');
});
