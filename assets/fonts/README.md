# Bundled subtitle fonts

The app includes 12 local font families for subtitle rendering and preview.
Each font is derived from Google Fonts at the commit recorded in `sources.json`.
All files are covered by SIL Open Font License 1.1; original copyright notices
and licenses are included in `licenses/`.

Variable fonts were instantiated at a fixed weight. All derived files use the
`AutoDub` family prefix to distinguish them from upstream releases and respect
reserved font names. The app displays the familiar upstream family names.
`sources.json` records upstream URLs, source hashes, output hashes and weights.

- `ttf/`: the exact font files loaded by Pillow previews and FFmpeg renderers.
- `licenses/`: unmodified upstream OFL notices for every family.
- `sources.json`: source commit, attribution and integrity metadata.
- `fetch_fonts.py`: optional developer refresh tool; requires `requests` and
  `fonttools`. Run `python assets/fonts/fetch_fonts.py --revision <recorded-sha>`
  to reproduce the pinned bundle. The application does not run this tool or
  contact Google Fonts during normal use.

All 12 families support Vietnamese. Noto Sans JP is used for Japanese/Chinese
caption entries so the same fallback is available in both render paths.
