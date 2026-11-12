// The weight boxes for each patch type and its categories. Shared by the Patches page (your own and the shared weights) and by
// the report editor (a report's own weights). A blank box means "use the default"; the grey number shows what that default is.
// Keys must match the backend's: a patch type ('third-party') or '<type>:<category id>'.
export const categoryKey = (type, id) => `${type}:${id ?? ''}`;

export function WeightFields({ types, draft, setDraft, inherited = {} }) {
  const input = (key, label) => (
    <label class="field" key={key}>{label}
      <input type="number" min="0" step="0.1" value={draft[key] ?? ''} placeholder={String(inherited[key] ?? 1)}
        onInput={(e) => setDraft({ ...draft, [key]: e.target.value })} />
    </label>
  );
  return types.map((t) => (
    <fieldset class="group" key={t.key}>
      <legend>{t.label}</legend>
      <div class="weights">
        {input(t.key, 'Type Weight')}
        {t.categories.map((c) => input(categoryKey(t.key, c.category_id), c.category))}
      </div>
    </fieldset>
  ));
}
