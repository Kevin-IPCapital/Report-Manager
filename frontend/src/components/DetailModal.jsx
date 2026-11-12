import { Fragment } from 'preact';
import { useEffect, useRef } from 'preact/hooks';
import { hint } from '../glossary';
import { cell } from './ui';

// One [label, rendered value] pair for the facts grid; `type` is a column type (see `cell`).
export const fact = (row, label, key, type) => [label, row[key] == null ? '—' : cell({ type }, row[key])];

// Card over a blurred viewport. Closes on Escape, the × button or a click outside the card.
export function DetailModal({ kicker, title, banner, facts, blocks = [], onClose }) {
  const closeButton = useRef(null);
  useEffect(() => {
    closeButton.current?.focus();
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div class="modal-backdrop" onClick={onClose}>
      <article class="modal-card" role="dialog" aria-modal="true" aria-labelledby="detail-title" onClick={(e) => e.stopPropagation()}>
        <header>
          <div>
            {kicker && <span class="note">{kicker}</span>}
            <h2 id="detail-title">{title}</h2>
          </div>
          <button class="icon-btn" ref={closeButton} aria-label="Close" onClick={onClose}>×</button>
        </header>
        {banner && <p class="modal-flag">{banner}</p>}
        <dl class="facts">{facts.map(([label, value]) => <div key={label}><dt {...hint(label)}>{label}</dt><dd>{value}</dd></div>)}</dl>
        {blocks.map(([label, text]) => (
          <Fragment key={label}><h3>{label}</h3><p>{text || '—'}</p></Fragment>
        ))}
      </article>
    </div>
  );
}
