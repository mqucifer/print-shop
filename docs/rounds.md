# The rounds

The local model works in rounds on the bench, and every round is recorded (mqucifer/print-shop#17, M18 in mqucifer/project-print `ROADMAP.md`, ADR 0010 there). It closes the loop [the bench](bench.md) left open: intake, make or change, read the bench's answer, change again. Limits start high and are lowered from evidence, there's no budget on thinking, and every failure has a named ending.

```
scripts/rounds.py start NAME --request FILE [--reference FILE]...
scripts/rounds.py resume NAME [--note FILE] [--reopen-needs]
scripts/rounds.py status NAME
scripts/rounds.py stop NAME
scripts/rounds.py summary NAME
```

It needs the bench's image (`scripts/bench.py build`), and LiteLLM on :4000 with print-shop's key from `~/.config/print-shop/.env`. It uses `crew-code-think`, and probes nothing else. **A run names its code,** so `start` and `resume` refuse a checkout with uncommitted changes. As in `cad_eval`, run from a checkout nothing edits until it ends: `git worktree add --detach ../run-<commit> <commit>`. One session runs under a root at a time (`.session.lock`). Runs live in `~/print-shop/design-runs/`, off this public repository, and `start` refuses a folder inside it.

## A run

**Intake.** From the request and any reference files, the model reads what it's given and asks what it needs, as an interview. `questions` writes them to a file and the process exits. A person relays them, writes the answers to the file it names, and runs `resume`. `needs` ends intake. Each need says how it's checked, by a bench command and its limit, or by the Sponsor's eye. A quote from the Sponsor is held to their words. Writing and running are open throughout, and nothing gates code on a plan. `needs` with `try: true` reads each bench check against the files as they stand, and settles nothing.

**Rounds.** The model makes and changes the part, and calls `done` when it believes the needs are met. The harness then runs the script again and each need's check itself, so the model's word and a tool's success don't count:
- The clean run has a fresh container, over a plain copy of `work/` with no links, nothing a run left, and all geometry outside `references/` taken away.
- Each bench need then runs in another fresh container, where no model code has run. `params` runs the script by its nature, on a scratch copy, so its reading is as good as its own second check.
- `DISAGREE` and `NO ANSWER` never pass, whatever a need says.
- An eye need passes on a pick, and on nothing else. A stand-in's passes it too, so a run can be proved with no Sponsor there, and the reading says who it was.
- A `part` a clean run didn't leave isn't met, whatever the needs read.

Needs that fail go back in `done`'s reply, with each reading against its limit and the last round's, and the next round starts from what's there: the part, the script and `record/ROUNDS.md`. The conversation carries on, with its reasoning.

**The needs are settled once.** They're what the work is checked against, so the model can't change them. `resume --reopen-needs` lets it, when a person decides a need's check is wrong.

**What's the part.** `done` names the files that are the part (`part: ["stand.step"]`), as a need can name a test piece beside it. `final-sheet.png` draws those alone. It's the model's word and it decides nothing else. A sheet's `files` are what the Sponsor takes for the part too, and `test_pieces` on `ask` shows others, see-through in grey and marked.

**Stopping for the Sponsor.** Only `ask`, where form is at stake: the model names variants, `render` makes a sheet, and the process exits. It can add close-ups to each row (`close`), for what's too fine to judge whole. A person relays it, writes `pick.json`, and runs `resume`. A pick with no reason is refused, since the reason is what's learned from. After `needs`, `questions` is refused.

**A pick says who made it,** so someone else's can't pass for the Sponsor's taste in later training data. `pick.json` is `{"pick": "A", "reason": "...", "by": "sponsor"}`, with `"pick": "none"` if nothing's right. Anyone else is `"by": "stand-in: a Claude builder"`, naming who. Anything else, or nothing, is refused. Text says "the Sponsor picked" only when they did: what the model's told, the reading, and `record/ROUNDS.md`. The ledger has it in `picks.picked_by`, and `summary.json` in `picked_by`. An empty `picked_by` says nothing of who it was.

| Ending | When | Whose |
|---|---|---|
| `met` | Every need passed in the harness's own check | |
| `blocked` | The model said what's missing | the model's reason, read by hand: a missing tool may be what it names |
| `no_tool_call` | That many replies in a row with no tool call (`quiet`) | the model |
| `stuck` | That many rounds refused with nothing in `work/` changed (`stuck_rounds`) | the model |
| `call_cap`, `round_cap`, `model_time` | A limit, with no verdict on the design (`calls`, `rounds`, `model_seconds`) | the budget |
| `context_full` | Still over `compact_at` after compacting, after `restarts` new conversations | the budget |
| `work_full`, `disk_floor` | `work/` over `work_gb`, or Docker's disk under its 5 GB floor | the budget |
| `call_failed` | A call failed past `call_retries`, or at once if it can't be retried | infrastructure |
| `container_failed` | Docker didn't answer | infrastructure |
| `harness_fault` | A bug of ours, its trace in `harness-fault.txt` | the harness |
| `stopped` | The `stop` file or a signal | a person |

`questions` and `ask` aren't endings: the run waits. **`resume` goes on after any of them but `met`,** from the very message it stopped at, with `--note` adding a message. A crash loses at most the last call's answer, and that call is made again.

A conversation that won't fit even compacted starts again from the record, keeping the request, the answers, the needs and the files. Its reasoning is what's lost, and `restarts` counts them.

Limits are in `session.Limits`, recorded on the run's row. The model isn't told them, so a run says what a lower one would have done.

## The ledger

`~/print-shop/design-runs/ledger.sqlite`, separate from the print ledger. Code writes it, from the first round. The model's account of itself isn't a source for any of it, but for one column marked as such.

| Table | Holds |
|---|---|
| `runs` | What it ran on, read rather than typed: the commit, the skills' hash, the image's id, the alias and the limits. How it ended, its calls and tokens |
| `needs` | Each version of the needs, and where each came from |
| `rounds` | The starting state (each file's hash, the needs failing), the change (the script's diff), the clean run, the files `done` named as the `part`, and `fix_shape_model_said`, in the model's own words, with the vocabulary settled later. It decides nothing |
| `readings` | Each need's check at each round's end: the verdict, the number, its limit, and how it ran |
| `bench_calls` | Each bench answer the model asked for between those: calls to the first clean bench pass come from these (ADR 0010's fourth signal) |
| `picks` | The question, the variants, the sheet, and the pick with its reason and who made it (`picked_by`) |
| `questions` | Intake's questions and answers |
| `changes` (a view) | Each need's reading before and after each round, with the diff |

Looking lessons up isn't here.

## A run's folder

| Path | Holds |
|---|---|
| `request.md` | As it came |
| `work/` | `/work` in each container, and the only thing model-written code can touch. `references/` is what was given, `record/` what the harness keeps for the model to read, and nothing is ever read back from it |
| `events.jsonl` | Every call, with its reasoning, and every tool call. `transcript.rebuild(events, n)` gives call `n`'s exact request, across stops and compaction. It's what could later train an adapter (M14) |
| `views/` | Each picture the model was shown |
| `state.json`, `needs.json`, `needs/` | Where it stands, and the needs |
| `questions/n.md`, `n.answers.md` | Each stop at intake |
| `asks/n/` | `question.md`, `sheet.png`, `variants/`, `test-pieces/` if it showed any, and `pick.json` |
| `rounds/nnn/` | `judged/` (what the clean run left), `readings/` (each need's answer, in text and numbers), `run.txt`, `judgement.json`, `diff.patch` |
| `final-sheet.png` | The `part` as it passed, for the people who see the result. It decides nothing |
| `summary.json` | A run's figures, from `scripts/rounds.py summary`, read from its events |

## Where each part came from

The session is ported from mqucifer/model-performance's `cad_eval` at 22ea8b7, not imported, and nothing here imports crew. A shared core is extracted only at a second or third real use.

| Here | From | Changed |
|---|---|---|
| `session.py` | `session.py`, the base arm | It outlives its stops, every tool call in a turn gets a reply, a call is retried, and `restore` reads it back |
| `tools.py` | `tools.py`: `write`, `edit`, `read`, `run`, `lookup`, `docs`, `skill` | Adds `bench`, and `read` shows pictures. `edit` keeps crew's `replace_text` rules |
| `paths.py` | `inside`, `open_inside`, `copy_plain` | Unchanged in what they refuse |
| `send.py`, `transcript.py` | `send.py`, `common/litellm.py`, `transcript.py` | `snapshot` events |
| `needs.py` | The idea of `plan.py`: a quote held to the source | Needs, not a plan |
| `judge.py` | `final_case` | A bench command per need, not hidden checks |
| `bench/search.py`, `bench/hint.py`, `bench/image/*_index.py` | The same files | Started by `host.helper`, not on stdin |
| `skills/cadquery`, `skills/references` | `skills/` at 98b3c5b, byte for byte, held by a hash and a test on the Mac | `skills/bench` is new. Where they say `inspect` or `measure`, the system prompt says what those are here |

**Lessons carried, and how:**
- **A link made in a container resolves on the host.** Each host-side open walks from the directory's fd with `O_NOFOLLOW`, and takes only a regular file. Tests make the links in a real container.
- **A process left by a run could race the harness, or plant a `python` on its PATH.** Each command here is its own container, gone when it ends, with `/work` on no PATH and `python -I` for every harness command. So there's no reaper, and a test holds that a `setsid` daemon doesn't outlive its run.
- **Runaway calls aren't a client timeout.** Calls are streamed, and crew's whitespace cut is the only cut.
- **Compaction lost the script once.** Each file's latest full text stays whole, and only a whole `read` counts. Here the latest `bench` and `done` replies stay too, and what the Sponsor said is never touched.
- **No address in the records.** LiteLLM's api-base header is dropped.

**Not ported:** `plan` (a gate on one-round evidence, and the needs say what's wanted), `measure`, `inspect` and `view` (the bench answers those, with second checks), the hidden checks and the arms. The per-call comparison of serving with infra's record (`identity.py`) is left out: a call's `x-litellm-*` headers are kept, for comparing later.

**New limits, found building this:**
- A script that prints without end would have filled the Mac's memory and Docker's disk through its log. Only the end of a command's output is kept, and containers have no log.
- `/work` is a folder on the Mac with no quota. A file is capped at 2 GiB, and `work_gb` ends a run.

## The first real run

On 2026-11-01, `crew-code-think` through LiteLLM, on a made-up LED tealight stand, with a Claude builder standing in for the Sponsor at the pick. Commit e8ab52a, image `sha256:c950cbb83857`, with a crew tick running as it started, so no timing claims. It went from intake to `met` in round 1: 37 calls, 2,918 s of model time. Its figures are in `~/print-shop/design-runs/tealight-1/summary.json`, and its readings in the ledger. One run on one small part, so it sets no limit either way. mqucifer/print-shop#17 has what it did and what it lacked. Fixed from it:
- **`run` lost what a stopped script had printed,** since Python buffers its output into a pipe. A 600 s hang came back with nothing. Containers now run unbuffered.
- **A need's check couldn't be tried before it was settled,** so the model guessed a number's name. `try: true` reads them.

mqucifer/print-shop#19 then took what else it lacked: close-ups, sheets of the part alone, and a pick that says who made it. **Its pick is a stand-in's, a Claude builder's, and isn't the Sponsor's taste.** Its records were corrected on the Mac: `picks.picked_by`, the reading's `why`, `pick.json`, `state.json`, `judgement.json` and `ROUNDS.md`. `events.jsonl` is left as it was sent, since it's what the model was told and every request rebuilds from it, and a last `correction` event says so. Anything reading it for training reads that event.

**Known limits it showed:**
- **A need is only as honest as what it's checked against.** Here the fit's checked against a tealight the model drew from the request's numbers. That's right on this part, but no code holds a fixture to the request.
- **The pick isn't held to the final part.** An eye need passes on the pick, and nothing compares what passes at `done` with the variant the Sponsor picked. Here the two read the same volume by the bench, and that comparison was by hand.
- **A design made before `needs` is round 0.** `bench_calls` holds its readings with round 0, and `rounds` starts only at `needs`, so here one "round" follows 32 calls of real design work.

## Tests

`uv run pytest -q tests/test_rounds.py` needs no Docker. `uv run pytest -q tests/test_rounds_in_image.py`, under the `bench` mark, has a scripted fake model drive a real run in real containers, with no model call: every tool, every ending, a resume after `ask`, a failing need going back, and a `done` the checks refuse not ending the run. The part is made up in the test, of plain boxes.
