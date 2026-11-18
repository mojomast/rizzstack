# Image sources

| File | What it shows | How it was made |
|---|---|---|
| `rizzstack-hero.jpg` | README banner: the rizzstack mark and wordmark over honeycomb art | Background generated with an image model (no text in the art); the mark, wordmark and tagline are the brand SVGs and Geist text composited in HTML and rendered in Chromium |
| `art-verify-gate.jpg` | Section art for the offline demo: tokens passing (mint) or diverted (red) at a scanner | Generated illustration, no text |
| `art-four-layers.jpg` | Section art for the four projects: four stacked hex plates, Orbit on top in honey | Generated illustration, no text |
| `offline-demo-run.png` | `reference/run_demo.py` output plus `acdd-lint` on the run | Real command output from a Workflow checkout (Nov 18 2026), rendered as HTML and captured headless |
| `demo-acceptance-report.png` | `demo-run/ACCEPTANCE.md`, W1 and W4 sections | The run's own file, rendered from Markdown |
| `demo-red-green-evidence.png` | W1 RED and GREEN verifier records and the two rejection records | Real `jq` output over the run's evidence files |
| `hive-incident-replay.png` | The Hive incident recorder at the combined-tree pass | Headless Chromium on the Hive static demo (`?demo=replay`), orbitdesktop `d3a115c`, fixture data |
| `hive-workbench.png` | The Hive operational workbench | Same, `?demo=shift` → Workbench |
| `hive-release-desk.png` | The Hive release desk | Same, `?demo=shift` → Release desk |
| `hive-shift-control.png`, `hive-draft-coverage.png` | Shift control and draft coverage | Earlier captures from the Hive review candidate |

No image shows a live host, token, key, IP, tailnet host or private URL.
