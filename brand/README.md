# AXIM Brand Identity — "The AXIS" Mark

## The mark

`axis-mark.svg` is the single source of truth - every other icon asset
in this project is generated from it, never hand-edited separately.

**Construction**: a rounded-square badge (brand blue, `#2452EB` - the
same blue already used throughout the web app's buttons/active-states/
focus-rings, kept for continuity rather than introducing a second
color) containing one solid white faceted dart/wing shape pointing up,
with a chevron-shaped notch cut from its center as negative space. The
cut chevron reads as an embedded upward arrow; the outer wing shape's
two "legs" plus the pointed top subtly rhyme with the letter A (AXIM's
initial) without being a literal lettermark - a deliberate choice per
the brief ("do not use a simple 'A' or generic lettermark").

**Why this construction, not a simpler one**: the first pass (a plain
triangle with a smaller triangular hole) tested legible but generic -
it read as a caution/delta glyph, a motif already common across
trading apps. The faceted wing shape with a true chevron (not just a
smaller triangle) cut into it reads as more deliberately geometric and
"axis/coordinate-marker"-like, and holds up distinctly at 16px, 32px,
and on both light and dark backgrounds (verified via rendered
screenshots at each size before adopting it - not assumed correct from
the code alone).

**Flat, no gradient**: a single fill color plus white, no gradient
requirement - reads correctly with color reduced to two flat tones,
per the brief's "no gradients required for recognizability."

## Files

- `axis-mark.svg` - the master mark, 100x100 viewBox, rounded-square
  container baked in. This is what gets fed into platform icon
  generators (see `axim-desktop/src-tauri/icons/` for the generated
  Tauri set) and is also used directly as the web favicon and sidebar
  mark (SVG scales cleanly, no raster generation needed for the web
  surface).

## Where it's used

- AXIM TradeStation: app icon, installer icon (MSI/NSIS), window icon,
  taskbar icon - generated via `axim-desktop/src-tauri/icons/`.
- Web app: favicon, sidebar logo mark (replacing the old plain "A"
  lettermark), login/bootstrap/password-reset screen branding.
- Loading/connecting state: a subtle animated version of the mark
  (see `web/shell.js`'s loading indicator and the desktop launcher's
  auto-connect screen).

## Extending this later

If a second color, a wordmark lockup treatment, or export sizes beyond
what's already generated are needed, start from `axis-mark.svg` as the
single source of truth rather than approximating it freehand elsewhere
- every derived asset should trace back to this one file so the mark
never quietly drifts into multiple inconsistent versions.
