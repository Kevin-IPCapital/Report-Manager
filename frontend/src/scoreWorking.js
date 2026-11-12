// How the weights are used in the final calculations: the rule in plain words, and the working for one client's score, turned into text.
// The figures come from the server (`calculation` in the patches answer), which makes them with the same code that makes the scores; this
// only words them. Pure, so it can be tested.

// The rule, as steps. Every statement here is what scoring.py does.
export const HOW_IT_WORKS = [
  'Each category (for example Cumulative Updates) has a compliance: the share of its patches that are installed.',
  'A type\'s score (Windows Security Patching, Windows Patching, Third Party Patching) is the average of its categories\' compliance, weighted. A category with weight 2 counts twice as much as one with weight 1. It is an average of categories, not of patches, so a category with thousands of patches does not drown out one with a few.',
  'The overall score is the same kind of weighted average of the three type scores, using the type weights.',
  'A weight of 0 leaves that category or type out. A category that lists no patches is left out too. Whatever is left out takes no part: the rest share the weight between them.',
  'A weight you have not set counts as 1, or as the shared default if there is one. Your own weights replace the shared ones for what you see. A saved report uses the shared weights plus its own, so everyone who runs it gets the same figures. The score history behind the trend uses the shared weights.',
  'The previous score is worked out in exactly the same way from the earlier patch inventory.',
];

const pct = (v) => (v == null ? '—' : `${v.toFixed(2)}%`);
const num = (v) => String(Number(v.toFixed(4)));   // a weight such as 0.5 or 3, without trailing zeros

// "(96.00 × 2 + 88.00 × 1) ÷ (2 + 1) = 93.33%", or a sentence when there is one item or none.
export function formula(items, score) {
  const used = items.filter((i) => i.counts);
  if (used.length === 0) return 'Nothing counts, so there is no score.';
  if (used.length === 1) return `Only ${used[0].name} counts, so the score is ${pct(used[0].value)}.`;
  return `(${used.map((i) => `${i.value.toFixed(2)} × ${num(i.weight)}`).join(' + ')}) ÷ (${used.map((i) => num(i.weight)).join(' + ')}) = ${pct(score)}`;
}

const share = (v) => (v == null ? '' : `${v}%`);

// The working for one client's score as rows of text for the page. `calculation` is the server's explanation.
export function workingView(calculation) {
  const types = calculation.types.map((t) => ({
    key: t.key, label: t.label, scoreText: pct(t.score), weightText: num(t.weight), shareText: share(t.share), counts: t.counts,
    reason: t.reason ? `left out: ${t.reason}` : '',
    rows: t.categories.map((c) => ({
      key: String(c.category_id), name: c.category, complianceText: pct(c.compliance), weightText: num(c.weight), shareText: share(c.share), counts: c.counts,
      reason: c.reason ? `left out: ${c.reason}` : '',
    })),
    formula: formula(t.categories.map((c) => ({ name: c.category, value: c.compliance, weight: c.weight, counts: c.counts })), t.score),
  }));
  return {
    types,
    overall: {
      scoreText: pct(calculation.overall.score),
      formula: formula(calculation.types.map((t) => ({ name: t.label, value: t.score, weight: t.weight, counts: t.counts })), calculation.overall.score),
    },
  };
}
