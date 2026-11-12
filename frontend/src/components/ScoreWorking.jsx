import { CHOOSE_ONE } from '../overview';
import { HOW_IT_WORKS, workingView } from '../scoreWorking';

// How the weights are used in the final calculations: the rule in plain words, and, when one client is chosen, the actual working for that
// client's scores, with its real numbers. The figures come from the server, which makes them with the same code that makes the scores.
export function ScoreWorking({ calculation, clientName }) {
  const view = calculation ? workingView(calculation) : null;
  return (
    <details class="score-working" open>
      <summary>How is the score calculated?</summary>
      <ol>{HOW_IT_WORKS.map((step) => <li key={step}>{step}</li>)}</ol>
      {!view && <p class="note choose-one" role="status">{CHOOSE_ONE}, above, to see the working for its score.</p>}
      {view && (
        <>
          <h3>The working for {clientName}</h3>
          <p class="note">These are the weights in effect for you now. Change them above and save, and this updates.</p>
          {view.types.map((t) => (
            <section class="working-type" key={t.key}>
              <h4>{t.label}: {t.scoreText}{t.reason && <span class="muted"> ({t.reason})</span>}</h4>
              {t.rows.length === 0 ? <p class="note">No patches are listed for this type.</p> : (
                <div class="table-wrap">
                  <table class="working">
                    <thead><tr><th>Category</th><th class="num">Compliance</th><th class="num">Weight</th><th class="num">Share of this score</th></tr></thead>
                    <tbody>
                      {t.rows.map((r) => (
                        <tr key={r.key} class={r.counts ? '' : 'left-out'}>
                          <td>{r.name}{r.reason && <span class="muted"> ({r.reason})</span>}</td>
                          <td class="num">{r.complianceText}</td><td class="num">{r.weightText}</td><td class="num">{r.shareText || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p class="formula">{t.formula}</p>
            </section>
          ))}
          <section class="working-type">
            <h4>Overall score: {view.overall.scoreText}</h4>
            <div class="table-wrap">
              <table class="working">
                <thead><tr><th>Type</th><th class="num">Score</th><th class="num">Weight</th><th class="num">Share of the overall score</th></tr></thead>
                <tbody>
                  {view.types.map((t) => (
                    <tr key={t.key} class={t.counts ? '' : 'left-out'}>
                      <td>{t.label}{t.reason && <span class="muted"> ({t.reason})</span>}</td>
                      <td class="num">{t.scoreText}</td><td class="num">{t.weightText}</td><td class="num">{t.shareText || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p class="formula">{view.overall.formula}</p>
            <p class="note">The type scores are used before they are rounded to two places, so adding up the rounded figures shown can differ in the last digit.</p>
          </section>
        </>
      )}
    </details>
  );
}
