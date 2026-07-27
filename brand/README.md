# AXIM Brand Identity — V2 (locked)

## The mark

The approved AXIM V2 identity is the image assets in this directory,
supplied directly by the brand owner. **These are the source of truth.
Do not redraw, vectorize, recolor, or resize the artwork itself** - the
custom X's upper-right segment intentionally stops short of the center
crossing, and that detail only exists correctly in the provided files.

Every other icon asset in this project (desktop app icons, web favicon,
sidebar/login/wizard marks) is generated or copied directly from these
files, never hand-approximated elsewhere.

## Files

- `axim-icon-white-on-blue.png` - primary app icon (Institutional Blue
  background, white mark). Used for: web favicon, sidebar/login/wizard/
  splash marks, desktop app icon source.
- `axim-icon-white-on-black.png` - dark-surface variant.
- `axim-icon-black-on-white.png` - light-surface variant.
- `axim-wordmark-black-transparent.png` / `axim-wordmark-white-transparent.png`
  - the "AXIM" wordmark (custom artwork, not a typeset font).
- `AXIM-brand-notes.txt` - the approved color palette and locked-X rule,
  verbatim from the brand owner.

## Colors (AXIM-brand-notes.txt)

- Institutional Blue: `#4A64C7`
- AXIM Black: `#0E1116`
- White: `#FFFFFF`
- Light Neutral: `#F4F6F8`
- Graphite: `#2B313A`
- Signal Orange (functional only - live execution/urgent alert, never
  decorative, never in the logo): `#F59E0B`
- Success Green (semantic only): `#22C55E`
- Risk Red (semantic only): `#EF4444`

These are wired into `web/theme.css` as `--brand`/`--blue` (Institutional
Blue), `--bg`/`--text` (Black/White/Light Neutral), `--green`, `--red`,
`--yellow` (Signal Orange). No purple/violet remains anywhere in the
palette.

## Where it's used

- Web app: favicon (`web/assets/brand/`, referenced from every page's
  `<head>`), sidebar logo mark, login/bootstrap/password-reset screen
  branding, empty-panel mark (`.empty-panel-mark`, faded via opacity),
  loading mark (`.axim-loading-mark`).
- Desktop app (`axim-desktop/`): splash screen mark, and the full
  platform icon set in `axim-desktop/src-tauri/icons/` (Windows/macOS/
  iOS/Android + Windows Store tiles) - regenerated from
  `axim-icon-white-on-blue.png` via `tauri icon` (upscaled to 1024x1024
  first since the source is 130x130; a real 1024px+ master export from
  the brand owner would sharpen the largest generated sizes).

## Extending this later

If a second color variant, additional export sizes, or a true vector
version becomes available, it must come from the brand owner - do not
approximate the artwork freehand. Regenerate the desktop icon set with
`npx tauri icon <source>` from inside `axim-desktop/` whenever the
source image changes, rather than hand-editing individual generated
files.

## Revision history

- **v1, "The Facet"**: an in-house diamond-with-a-cut mark
  (`axis-mark.svg`), designed internally. Retired 2026-07-26 per the
  AXIM V2 final build directive - the approved brand assets are now the
  only source of truth. The old mark's files are no longer in this
  directory; see git history if needed.
- **v2, "The Locked X"** (current): the provided custom X mark, upper-
  right segment intentionally unconnected. Supplied as raster assets by
  the brand owner, used directly per the brand-lock rule.
