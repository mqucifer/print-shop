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
