---
paths:
  - "/scripts/serve_test_catalog.py"
  - "tests/catalog_fixtures/**/*"
  - "/tests/test_catalog_fixtures.py"
---

# Local test catalog

`scripts/serve_test_catalog.py` builds and serves a small catalog at `http://127.0.0.1:8765/` for trying installs
by hand. Run `uv run python scripts/serve_test_catalog.py`, then set **Preferences > Installing > Catalog address**
to that URL.
- `--version 2` republishes Test Game as version 2 (to try Update)
- `--add-launch-path` republishes "Test Game Without Launch Path" with its launch path
- `--throttle KBPS` sends game files slowly (time to cancel a download)
- `--build-only` builds the catalog and stops; `--port N` and `--build-dir` change the port and build folder

Sources: `tests/catalog_fixtures/entries/*.json` (catalog schema v2; each game's `extras` is computed from every
entry whose `extends` list names it, as catalog-index does) and `tests/catalog_fixtures/games/`. They include a
tar.gz game, prices, links, a warning, a first-run download size, a required extra (Test Extra Level) and an
optional extra shared by Test Game With Extra and Test Game Tar (Test Shared Extra Level), and Test Story, a
two-page web page game that opens in the browser (launch path `index.html`). Output goes to
`tests/catalog_fixtures/_build/` (git-ignored). Every download address points at the local server.
