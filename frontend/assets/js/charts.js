// Small accessible SVG trend line. The aria-label carries the values, so screen-reader users get the data too.
const NS = 'http://www.w3.org/2000/svg';
const mk = (t, a) => { const n = document.createElementNS(NS, t); for (const [k, v] of Object.entries(a)) n.setAttribute(k, v); return n; };

export function trend(values, label, unit) {
  const W = 220, H = 52, pad = 6;
  const svg = mk('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': `${label} across ${values.length} visit${values.length === 1 ? '' : 's'}: ${values.join(', ')} ${unit}` });
  if (!values.length) return svg;
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
  const x = (i) => (values.length === 1 ? W / 2 : pad + i * ((W - 2 * pad) / (values.length - 1)));
  const y = (v) => H - pad - ((v - lo) / span) * (H - 2 * pad);
  if (values.length > 1) svg.append(mk('path', { d: values.map((v, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join(' '), fill: 'none', stroke: 'var(--brand)', 'stroke-width': 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
  values.forEach((v, i) => svg.append(mk('circle', { cx: x(i).toFixed(1), cy: y(v).toFixed(1), r: i === values.length - 1 ? 4.5 : 3, fill: i === values.length - 1 ? 'var(--brand)' : 'var(--surface)', stroke: 'var(--brand)', 'stroke-width': 2 })));
  return svg;
}
