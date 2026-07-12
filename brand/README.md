# AXIM Brand Identity — The Facet Mark

## The mark

`axis-mark.svg` is the single source of truth - every other icon asset
in this project is generated from it, never hand-edited separately.
(Filename kept from an earlier design iteration - see "Revision
history" below - not worth the churn of renaming every derived asset
and reference for a cosmetic mismatch.)

**Construction**: a rounded-square badge (brand blue, `#2452EB` - the
same blue already used throughout the web app's buttons/active-states/
focus-rings, kept for continuity rather than introducing a second
color) containing one solid white diamond (a square rotated 45°),
asymmetric: a smaller diamond is cut from its upper-right region as
negative space, exposing the badge's own blue through the cut. The
result reads as a single precision-cut facet - not a gem outline, not
a letter, not an arrow - just one bold geometric event happening inside
the badge. Deliberately asymmetric (the cut is off-center, not a
mirrored notch) so it doesn't settle into a symmetric "badge/seal"
cliché or read as a compass rose.

**Why this construction, not the earlier one**: an earlier revision
(described below) used a wing/dart shape with a chevron cut that, on
review of the actual rendered result rather than the design rationale
alone, reads unmistakably as a capital letter A - directly violating
the brief's "do not use a simple 'A' or generic lettermark." A diamond
has no letterform reading in any orientation, connotes precision and
value (faceted-gem framing) without literally drawing a gem, and the
asymmetric cut gives it a distinctive, non-generic silhouette that
still holds up at 32px and 48px (verified via rendered screenshots at
each size before adopting it, same discipline as before - not assumed
correct from the code alone).

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

- AXIM Trader: app icon, installer icon (MSI/NSIS), window icon,
  taskbar icon - generated via `axim-desktop/src-tauri/icons/`.
- Web app: favicon, sidebar logo mark (replacing the old plain "A"
  lettermark), login/bootstrap/password-reset screen branding.
- Loading/connecting state: the desktop launcher (`axim-desktop/src/
  index.html`/`main.js`) shows the mark pulsing specifically while
  actually trying to connect, static otherwise. The web app has the
  same pulse as a reusable component (`web/theme.css`'s
  `.axim-loading-mark`) for full-panel/full-page loading moments -
  deliberately not retrofitted into the app's existing small inline
  "Loading..." text labels, which are too small/transient to warrant a
  visual anchor without adding noise instead of clarity.
- Empty states: a muted version of the mark (`.empty-panel` in
  `web/theme.css`) on genuine "nothing here yet" panels - Mission
  Control's Recent Activity/Recent Sessions, Trading Sessions' "No
  active sessions." Deliberately a separate class from the existing
  `.empty` (reused ~80 places across this app for loading-row
  placeholders and error messages too, not just true empty states, in
  both full panels and single table cells - a blanket change there
  would look broken in a table cell and wrong in tone on an error
  message). Apply `.empty-panel` the same way elsewhere as more genuine
  empty states are found, rather than sweeping every `.empty` instance
  at once.

## Extending this later

If a second color, a wordmark lockup treatment, or export sizes beyond
what's already generated are needed, start from `axis-mark.svg` as the
single source of truth rather than approximating it freehand elsewhere
- every derived asset should trace back to this one file so the mark
never quietly drifts into multiple inconsistent versions.

## Revision history

- **v1, "The AXIS"**: a faceted wing/dart shape with a chevron cut,
  intended to read as an abstract upward arrow. Superseded - the
  rendered result reads as a capital letter A, which the brief
  explicitly rules out. Kept only in git history, not in this
  directory.
- **v2, "The Facet"** (current): the asymmetric diamond-with-a-cut
  described above. No letterform reading in any orientation.
