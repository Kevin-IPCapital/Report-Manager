const W = 640, H = 250, PAD = { l: 48, r: 16, t: 14, b: 36 };

// Line or bar chart from the backend's { heading, type, x, y }. The line draws itself in; hover a point for its value.
export function TrendChart({ heading, type = 'line', x = [], y = [], showHeading = true }) {   // showHeading false when a card above already has the title
  if (!y.length) return <p class="note">No trend data yet.</p>;
  const lo = Math.min(...y), hi = Math.max(...y), span = hi - lo || 1;
  const min = type === 'bar' ? Math.min(0, lo) : lo - span * 0.2, max = hi + span * 0.2;
  const iw = W - PAD.l - PAD.r, ih = H - PAD.t - PAD.b;
  const px = (i) => PAD.l + (type === 'bar' ? ((i + 0.5) / y.length) * iw : y.length === 1 ? iw / 2 : (i / (y.length - 1)) * iw);
  const py = (v) => PAD.t + ih - ((v - min) / (max - min)) * ih;
  const ticks = Array.from({ length: 5 }, (_, i) => min + ((max - min) * i) / 4);
  const step = Math.ceil(y.length / 8);
  const path = y.map((v, i) => `${i ? 'L' : 'M'}${px(i).toFixed(1)} ${py(v).toFixed(1)}`).join(' ');
  return (
    <figure class="trend">
      {showHeading && <h2>{heading}</h2>}
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${heading}: from ${y[0]} to ${y[y.length - 1]}`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={py(t)} y2={py(t)} class="trend-grid" />
            <text x={PAD.l - 8} y={py(t)} class="trend-tick" text-anchor="end" dominant-baseline="middle">{t.toFixed(1)}</text>
          </g>
        ))}
        {x.map((label, i) => i % step === 0 && (
          <text key={label} x={px(i)} y={H - 12} class="trend-tick" text-anchor="middle">{label}</text>
        ))}
        {type === 'bar'
          ? y.map((v, i) => <rect key={i} x={px(i) - (iw / y.length) * 0.35} width={(iw / y.length) * 0.7} y={Math.min(py(v), py(0 > min ? 0 : min))}
              height={Math.abs(py(v) - py(0 > min ? 0 : min))} class="trend-bar"><title>{`${x[i]}: ${v}`}</title></rect>)
          : <>
              <path d={path} pathLength="1" class="trend-line" />
              {y.map((v, i) => <circle key={i} cx={px(i)} cy={py(v)} r="4" class="trend-dot"><title>{`${x[i]}: ${v}`}</title></circle>)}
            </>}
      </svg>
    </figure>
  );
}
