# project-template

The files every crew-built project starts with (mqucifer/crew ADR 0030). A new
repository is created from this template by `scripts/new-project.sh` in the
crew's repository, and `crew onboard` proposes any of these files a repository
is missing, so older projects catch up when the template changes.

| File | What it does |
|---|---|
| `.github/workflows/checks.yml` | Runs the setup and checks the project's design names (`.crew/project.yaml`) on every pull request, in the design's image, as the crew's sandbox does. Once the project's skeleton makes it green, branch protection requires its `checks` job |
| `.github/crew/run_checks.py` | What `checks.yml` runs. Before the design is merged it has nothing to check and passes |
| `.github/crew/requirements.txt` | Its one dependency, PyYAML, locked with hashes by `uv pip compile --generate-hashes` |
| `.github/workflows/board.yml` | The crew's board automation: card moves no role performs, and an issue a person opens put on the board (a Goal in Inbox (Goals)) |
| `.github/ISSUE_TEMPLATE/goal-template.md` | The Sponsor's form for a new Goal. It arrives labelled `needs:human`, so the crew leaves it alone until the Sponsor swaps that for `goal` |
| `.gitignore` | OS files, caches, and the crew sandbox's own directories |

Nothing here depends on the project's language: that comes from its design. Stories
don't change these files; the template does, by pull request.

## Before the crew takes it over
This repository is a Claude-built alpha, tracked on the Crew roadmap board (https://github.com/orgs/mqucifer/projects/2). Its board workflow (`.github/workflows/board.yml`) runs only by hand, so its issues stay off the crew's delivery board. **At takeover, restore its `issues` and `pull_request` triggers,** as the comment in that file says.

## print-shop
The print project's phase 1 (mqucifer/project-print `ROADMAP.md`, M13): one job from an STL to a print, behind a human approval in Home Assistant, and a ledger of each print the agent starts.

The job is ported from the prototype, the local folder `3d-print-agent` (no remote), at its commit `1ef5f06`. `CONTRACTS.md` came with it and is the spec. Comments in the code that cite `AGENTS.md`, `PROJECT.md`, `CODE_REVIEW.md` or `BACKLOG.md` mean the prototype's files, which stay there: they hold the machine's addresses, and this repository is public.

- **The job:** an STL, then orientation (a model through LiteLLM :4000 proposes, a validator disposes), Orca slicing, the approval gate in Home Assistant, and the print. `scripts/print_job.py PART.stl` runs one; it arms the printer only when its own command line sets `PRINT_AGENT_ALLOW_HARDWARE=1`. The first deliberate print is `docs/runbook-first-print.md`. Its corrections and outcomes become lessons a reviewer keeps for a later part of the same shape (`docs/print-lessons.md`, `scripts/print_lessons.py`).
- **The ledger** (`print_shop.ledger`, at `~/print-shop/ledger.sqlite`) has one row per print the agent starts, written before the printer is told to start: the STL's hash, the orientation and who chose it, the slicer's settings, the printer and filament, the times, and the outcome with a symptom. When a print ends, Home Assistant asks how it came out (`print_shop.orchestrator.outcome`). A print with no answer stays open, and the printer's "completed" is never taken as success. `scripts/print_job.py --list` shows it.
- **Settings** live on the Mac in `~/.config/print-shop/.env` (mode 600), read by `print_shop.env.load_env`, never sourced and never committed. `scripts/check_addresses.py` fails on any address in a tracked file, and on the Mac on any of those values.
- **The printer is never armed by a test.** `tests/conftest.py` refuses an armed connection for the whole session, whatever the environment says.
- **The CAD bench** (M18, `docs/bench.md`): `scripts/bench.py <command>` over files in the current directory, in a container with no network. It answers in plain text and numbers, with a second check by a different method on each answer: clearance, panes from openings, whether a pane is held by shape, thin features, the pause layer, renders with close-ups, read-back and the parameter check. It never touches the printer.
- **The rounds** (M18, `docs/rounds.md`): `scripts/rounds.py start NAME --request FILE` puts the local model to work on the bench: intake, rounds the harness checks itself, stops for the Sponsor's eye and for approval once every need passes, the harness's own checks on what's printed and shown, the folder to print from, a ledger written by code from the first round, and lessons: the model's after-action review once a run is approved, checked by a reviewer, and given to the next run at its start. Runs live in `~/print-shop/design-runs/`, off this repository.
- **Checks:** `uv sync`, then `uv run ruff check .`, `uv run mypy src tests scripts`, `uv run pytest -q -m "not slicer and not llm and not mqtt and not ha and not bench and not bench_local"` and `uv run python scripts/check_addresses.py`. Drop `not slicer` on the Mac to slice with the real Orca. The tests marked `mqtt`, `llm` and `ha` reach live services, so they're skipped unless their run opts in (mqucifer/print-shop#115): `PRINT_SHOP_TEST_BROKER=host[:port]` names a test broker, and they publish only under their own run's `3dprint-test-<run>` prefix, never the live device's; `PRINT_SHOP_TEST_LLM=1` and `PRINT_SHOP_TEST_HA=1` for the others. Under the markers, `tests/conftest.py` refuses a broker connect or a LiteLLM call that isn't opted in. The bench's own: `uv run python scripts/bench.py build` once, then `uv run pytest -q -m bench`. CI runs both from `.github/workflows/ci.yml` until onboarding writes `.crew/project.yaml`: it builds the image once, from GitHub's cache on a pull request and from nothing on main, and runs the bench tests in four parts at once, three at a time in each (`--shard I/4 -n 3`; `scripts/check_shards.py` proves the parts are the whole).
