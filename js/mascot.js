/* global m */
// The cat mascot, drawn as SVG (same cats as Clawback), with a random coat per visit.

const INK = '#3b2a1a';
const WHITE = '#fbf7f0';
const PINK = '#f7b6c2';

// Each coat: base colour, ear colours, eye colour, and extra shapes (clipped to the head).
// Shapes are [tag, attrs] so they stay plain data.
export const COATS = {
  orange: {
    name: 'rode kater',
    base: '#f2a541', ears: ['#f2a541', '#f2a541'], eyes: '#7cb342',
    patches: [['path', { d: 'M48 24 l4 12 M60 22 v13 M72 24 l-4 12', stroke: '#c46f1f', 'stroke-width': 4, 'stroke-linecap': 'round' }]],
  },
  orangeWhite: {
    name: 'rood-witte kat',
    base: '#f2a541', ears: ['#f2a541', '#f2a541'], eyes: '#e0a526',
    patches: [
      ['path', { d: 'M60 34 L46 70 L74 70 Z', fill: WHITE }],
      ['ellipse', { cx: 60, cy: 80, rx: 26, ry: 18, fill: WHITE }],
    ],
  },
  tuxedo: {
    name: 'tuxedo-kat',
    base: '#262626', ears: ['#262626', '#262626'], eyes: '#9ccc65',
    whiskers: '#9a9a9a',
    patches: [
      ['path', { d: 'M60 40 L50 70 L70 70 Z', fill: WHITE }],
      ['ellipse', { cx: 60, cy: 82, rx: 24, ry: 17, fill: WHITE }],
    ],
  },
  calico: {
    name: 'lapjeskat',
    base: WHITE, ears: ['#e98a2c', '#2b2b2b'], eyes: '#e0a526',
    patches: [
      ['ellipse', { cx: 28, cy: 38, rx: 32, ry: 26, fill: '#e98a2c' }],
      ['ellipse', { cx: 94, cy: 42, rx: 26, ry: 22, fill: '#2b2b2b' }],
      ['ellipse', { cx: 86, cy: 98, rx: 16, ry: 10, fill: '#e98a2c' }],
    ],
  },
  tortie: {
    name: 'schildpadkat',
    base: '#3a2a22', ears: ['#3a2a22', '#d9822b'], eyes: '#e0a526',
    whiskers: '#9a8a80', mouth: '#f0dccb',
    patches: [
      ['ellipse', { cx: 34, cy: 44, rx: 16, ry: 12, fill: '#d9822b' }],
      ['ellipse', { cx: 84, cy: 30, rx: 14, ry: 9, fill: '#d9822b' }],
      ['ellipse', { cx: 82, cy: 80, rx: 13, ry: 9, fill: '#b5651d' }],
      ['ellipse', { cx: 60, cy: 76, rx: 9, ry: 7, fill: '#8a5a3a' }],
    ],
  },
  grey: {
    name: 'grijze kat',
    base: '#8f99a3', ears: ['#8f99a3', '#8f99a3'], eyes: '#e0a526',
    patches: [
      ['path', { d: 'M48 24 l4 12 M60 22 v13 M72 24 l-4 12', stroke: '#5f6973', 'stroke-width': 4, 'stroke-linecap': 'round' }],
      ['ellipse', { cx: 60, cy: 82, rx: 20, ry: 14, fill: '#d9dee3' }],
    ],
  },
};

export const randomCoat = () => {
  const keys = Object.keys(COATS);
  return keys[Math.floor(Math.random() * keys.length)];
};

export function CatFace(coatKey = 'orange') {
  const c = COATS[coatKey] || COATS.orange;
  const clip = `cat-head-${coatKey}`;
  const ear = (d, fill) => m('path', { d, fill, stroke: INK, 'stroke-width': 4, 'stroke-linejoin': 'round' });
  const eye = (cx) => [
    m('ellipse', { cx, cy: 57, rx: 6.5, ry: 8.5, fill: c.eyes, stroke: INK, 'stroke-width': 1.5 }),
    m('ellipse', { cx, cy: 57, rx: 2.6, ry: 6.5, fill: INK }),
    m('circle', { cx: cx + 2, cy: 53.5, r: 2, fill: '#fff' }),
  ];
  return m('svg.cat-face', { viewBox: '0 0 120 104', role: 'img', 'aria-label': `Mascotte: ${c.name}` }, [
    m('defs', m('clipPath', { id: clip }, m('ellipse', { cx: 60, cy: 60, rx: 48, ry: 40 }))),
    ear('M16 44 L20 4 L50 26 Z', c.ears[0]),
    ear('M104 44 L100 4 L70 26 Z', c.ears[1]),
    m('path', { d: 'M25 32 L26 15 L40 26 Z', fill: PINK }),
    m('path', { d: 'M95 32 L94 15 L80 26 Z', fill: PINK }),
    m('ellipse', { cx: 60, cy: 60, rx: 48, ry: 40, fill: c.base }),
    m('g', { 'clip-path': `url(#${clip})` }, c.patches.map(([tag, attrs]) => m(tag, { fill: 'none', ...attrs }))),
    m('ellipse', { cx: 60, cy: 60, rx: 48, ry: 40, fill: 'none', stroke: INK, 'stroke-width': 4 }),
    eye(42),
    eye(78),
    m('ellipse', { cx: 29, cy: 73, rx: 7, ry: 4, fill: PINK, opacity: 0.85 }),
    m('ellipse', { cx: 91, cy: 73, rx: 7, ry: 4, fill: PINK, opacity: 0.85 }),
    m('path', { d: 'M55 68 h10 l-5 6 z', fill: '#e46a7e', stroke: INK, 'stroke-width': 2, 'stroke-linejoin': 'round' }),
    m('path', { d: 'M60 74 q-5 7 -11 3 M60 74 q5 7 11 3', fill: 'none', stroke: c.mouth || INK, 'stroke-width': 3, 'stroke-linecap': 'round' }),
    m('path', { d: 'M6 62 L30 66 M6 72 L30 70 M114 62 L90 66 M114 72 L90 70', stroke: c.whiskers || INK, 'stroke-width': 2.5, 'stroke-linecap': 'round' }),
  ]);
}

export function Mascot(text, coatKey) {
  return m('div.mascot', [CatFace(coatKey), m('p.bubble', text)]);
}
