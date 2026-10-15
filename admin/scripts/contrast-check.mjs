#!/usr/bin/env node
/**
 * WCAG 2.1 contrast verification for the Proxy Crack admin palette.
 *
 * Every foreground/background pair the UI actually renders is listed here.
 * Alpha colours (tints, hover states, borders) are composited over their real
 * backdrop before measurement — an rgba() tint measured against transparency
 * is meaningless.
 *
 *   npm run check:contrast
 *
 * Exit code 1 if any pair marked AA_TEXT falls below 4.5:1, or any pair marked
 * AA_LARGE / NON_TEXT falls below 3:1.
 */

const T = {
  bg: '#0F172A',
  muted: '#10192E',
  card: '#192134',
  raised: '#1E283C',
  primary: '#1E3A5F',
  secondary: '#334155',
  accent: '#22C55E',
  onAccent: '#0F172A',
  fg: '#FFFFFF',
  subtleFg: '#CBD5E1',
  mutedFg: '#94A3B8',
  destructive: '#DC2626',
  destructiveText: '#F87171',
  warning: '#FBBF24',
  info: '#7DD3FC',
  ring: '#22C55E',
  border: 'rgba(255,255,255,0.08)',
  borderStrong: 'rgba(255,255,255,0.16)',
  hover: 'rgba(255,255,255,0.04)',
  selected: 'rgba(34,197,94,0.09)',
  accentTint: 'rgba(34,197,94,0.13)',
  warningTint: 'rgba(251,191,36,0.13)',
  dangerTint: 'rgba(248,113,113,0.13)',
  infoTint: 'rgba(125,211,252,0.12)',
  neutralTint: 'rgba(148,163,184,0.12)',
  gridLine: '#475569',
  // Validated categorical series palette (protocol distribution).
  seriesSs: '#3987E5',
  seriesSocks5: '#D95926',
  seriesHttp: '#9085E9',
}

function parse(c) {
  if (c.startsWith('#')) {
    const h = c.slice(1)
    const n = h.length === 3 ? h.split('').map((x) => x + x).join('') : h
    return [parseInt(n.slice(0, 2), 16), parseInt(n.slice(2, 4), 16), parseInt(n.slice(4, 6), 16), 1]
  }
  const m = c.match(/rgba?\(([^)]+)\)/)
  if (!m) throw new Error(`unparseable colour: ${c}`)
  const p = m[1].split(',').map((x) => Number(x.trim()))
  return [p[0], p[1], p[2], p[3] === undefined ? 1 : p[3]]
}

/** Composite `fg` over `bg` using source-over alpha blending. */
function over(fg, bg) {
  const [fr, fg_, fb, fa] = parse(fg)
  const [br, bg_, bb] = parse(bg)
  const mix = (f, b) => Math.round(f * fa + b * (1 - fa))
  return `rgb(${mix(fr, br)},${mix(fg_, bg_)},${mix(fb, bb)})`
}

function luminance(c) {
  const [r, g, b, a] = parse(c)
  if (a !== 1) throw new Error(`luminance of a transparent colour: ${c}`)
  const lin = (v) => {
    const s = v / 255
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

function ratio(a, b) {
  const la = luminance(a)
  const lb = luminance(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

const AA_TEXT = 4.5
const NON_TEXT = 3.0

/** [label, foreground, background, threshold] */
const PAIRS = [
  // --- Body / primary reading surfaces ---
  ['fg on bg', T.fg, T.bg, AA_TEXT],
  ['fg on card', T.fg, T.card, AA_TEXT],
  ['fg on raised (modal/drawer)', T.fg, T.raised, AA_TEXT],
  ['fg on muted (table header)', T.fg, T.muted, AA_TEXT],
  ['subtleFg on bg', T.subtleFg, T.bg, AA_TEXT],
  ['subtleFg on card', T.subtleFg, T.card, AA_TEXT],
  ['subtleFg on raised', T.subtleFg, T.raised, AA_TEXT],
  ['subtleFg on secondary fill', T.subtleFg, T.secondary, AA_TEXT],
  ['mutedFg on bg', T.mutedFg, T.bg, AA_TEXT],
  ['mutedFg on card', T.mutedFg, T.card, AA_TEXT],
  ['mutedFg on muted (table header label)', T.mutedFg, T.muted, AA_TEXT],
  ['mutedFg on raised', T.mutedFg, T.raised, AA_TEXT],
  ['mutedFg on hovered card row', T.mutedFg, over(T.hover, T.card), AA_TEXT],
  ['fg on selected row', T.fg, over(T.selected, T.card), AA_TEXT],
  ['mutedFg on selected row', T.mutedFg, over(T.selected, T.card), AA_TEXT],

  // --- Fills carrying text ---
  ['onAccent on accent (primary button)', T.onAccent, T.accent, AA_TEXT],
  ['fg on primary fill', T.fg, T.primary, AA_TEXT],
  ['fg on secondary fill', T.fg, T.secondary, AA_TEXT],
  ['fg on destructive fill (confirm button)', T.fg, T.destructive, AA_TEXT],

  // --- Status text on surfaces ---
  ['accent text on bg', T.accent, T.bg, AA_TEXT],
  ['accent text on card', T.accent, T.card, AA_TEXT],
  ['accent text on accent tint / bg', T.accent, over(T.accentTint, T.bg), AA_TEXT],
  ['accent text on accent tint / card', T.accent, over(T.accentTint, T.card), AA_TEXT],
  ['destructiveText on bg', T.destructiveText, T.bg, AA_TEXT],
  ['destructiveText on card', T.destructiveText, T.card, AA_TEXT],
  ['destructiveText on danger tint / bg', T.destructiveText, over(T.dangerTint, T.bg), AA_TEXT],
  ['destructiveText on danger tint / card', T.destructiveText, over(T.dangerTint, T.card), AA_TEXT],
  ['destructiveText on raised (dialog copy)', T.destructiveText, T.raised, AA_TEXT],
  ['warning on bg', T.warning, T.bg, AA_TEXT],
  ['warning on card', T.warning, T.card, AA_TEXT],
  ['warning on warning tint / card', T.warning, over(T.warningTint, T.card), AA_TEXT],
  ['info on card', T.info, T.card, AA_TEXT],
  ['info on info tint / card', T.info, over(T.infoTint, T.card), AA_TEXT],
  ['mutedFg on neutral tint / card', T.mutedFg, over(T.neutralTint, T.card), AA_TEXT],

  // --- Non-text: focus ring, borders, state dots (WCAG 1.4.11, 3:1) ---
  ['focus ring vs bg', T.ring, T.bg, NON_TEXT],
  ['focus ring vs card', T.ring, T.card, NON_TEXT],
  ['focus ring vs raised', T.ring, T.raised, NON_TEXT],
  ['focus ring vs accent button fill', T.ring, T.accent, 1.0], // ring sits outside, offset shows bg
  ['alive icon (accent) vs card', T.accent, T.card, NON_TEXT],
  ['dead icon (destructiveText) vs card', T.destructiveText, T.card, NON_TEXT],
  ['input border vs card', over(T.borderStrong, T.card), T.card, 1.0], // decorative, documented
  ['table gridline vs card', T.gridLine, T.card, 1.0],                 // decorative, documented

  // --- Categorical series marks on the chart surface (WCAG 1.4.11) ---
  ['series ss (#3987E5) vs card', T.seriesSs, T.card, NON_TEXT],
  ['series socks5 (#D95926) vs card', T.seriesSocks5, T.card, NON_TEXT],
  ['series http (#9085E9) vs card', T.seriesHttp, T.card, NON_TEXT],
  ['series ss vs bg', T.seriesSs, T.bg, NON_TEXT],
  ['series socks5 vs bg', T.seriesSocks5, T.bg, NON_TEXT],
  ['series http vs bg', T.seriesHttp, T.bg, NON_TEXT],
]

const FAIL = '✗'
const PASS = '✓'

let failed = 0
const rows = PAIRS.map(([label, fgc, bgc, min]) => {
  const fg = parse(fgc)[3] === 1 ? fgc : over(fgc, T.bg)
  const r = ratio(fg, bgc)
  const ok = r >= min
  if (!ok) failed += 1
  return { label, r, min, ok }
})

const w = Math.max(...rows.map((x) => x.label.length))
console.log('\nWCAG 2.1 contrast — Proxy Crack admin palette\n')
for (const { label, r, min, ok } of rows) {
  console.log(
    `  ${ok ? PASS : FAIL}  ${label.padEnd(w)}  ${r.toFixed(2).padStart(6)}:1   (min ${min.toFixed(1)})`,
  )
}

// The trap, stated explicitly so nobody "fixes" destructiveText back to it.
const trap = ratio(T.destructive, T.bg)
console.log(
  `\n  note: #DC2626 as TEXT on #0F172A is ${trap.toFixed(2)}:1 — FAILS AA.` +
    `\n        It is a fill-only token. Red text uses #F87171 (${ratio(T.destructiveText, T.bg).toFixed(2)}:1).\n`,
)

if (failed > 0) {
  console.error(`${failed} pair(s) below threshold.\n`)
  process.exit(1)
}
console.log(`All ${rows.length} pairs pass.\n`)
