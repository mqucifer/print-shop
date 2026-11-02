# The rounds

The local model works in rounds on the bench, and every round is recorded (mqucifer/print-shop#17, M18 in mqucifer/project-print `ROADMAP.md`, ADR 0010 there). It closes the loop [the bench](bench.md) left open: intake, make or change, read the bench's answer, change again. Limits start high and are lowered from evidence, there's no budget on thinking, and every failure has a named ending.

```
scripts/rounds.py start NAME --request FILE [--reference FILE]...
scripts/rounds.py resume NAME [--note FILE] [--from WHO] [--reopen-needs]
scripts/rounds.py resume NAME --approve --by WHO [--note FILE]
scripts/rounds.py resume NAME --send-back --by WHO --note FILE [--reopen-needs]
scripts/rounds.py status NAME
scripts/rounds.py stop NAME
scripts/rounds.py summary NAME
scripts/rounds.py read-back NAME [--ledger FILE]
scripts/rounds.py after-action NAME
scripts/rounds.py lessons [RUN] [--ledger FILE]
scripts/rounds.py lessons accept|edit|reject|retire|promote ID --by WHO ...
scripts/rounds.py lessons score RUN ID --score used|not_used|in_the_way --by WHO [--calls N,N]
scripts/rounds.py lessons load FILE --by WHO
```

It needs the bench's image (`scripts/bench.py build`), and LiteLLM on :4000 with print-shop's key from `~/.config/print-shop/.env`. It uses `crew-code-think`, and probes nothing else. **A run names its code,** so `start` and `resume` refuse a checkout with uncommitted changes. As in `cad_eval`, run from a checkout nothing edits until it ends: `git worktree add --detach ../run-<commit> <commit>`. One session runs under a root at a time (`.session.lock`). Runs live in `~/print-shop/design-runs/`, off this public repository, and `start` refuses a folder inside it.

## A run

**Intake.** From the request and any reference files, the model reads what it's given and asks what it needs, as an interview. `questions` writes them to a file and the process exits. A person relays them, writes the answers to the file it names, and runs `resume`. `needs` ends intake. Each need says how it's checked, by a bench command and its limit, or by the Sponsor's eye. A quote from the Sponsor is held to their words. Writing and running are open throughout, and nothing gates code on a plan. `needs` with `try: true` reads each bench check against the files as they stand, and settles nothing. Each reading reaches the ledger's `tries`. A need's `source` says where it came from: `request`, `answer` or `note`, each with a quote held to that source's words, so what a person relayed in a note isn't marked `derived`.

**Rounds.** The model makes and changes the part, and calls `done` when it believes the needs are met. The harness then runs the script again and each need's check itself, so the model's word and a tool's success don't count:
- The clean run has a fresh container, over a plain copy of `work/` with no links, nothing a run left, and all geometry outside `references/` taken away.
- Each bench need then runs in another fresh container, where no model code has run. `params` runs the script by its nature, on a scratch copy, so its reading is as good as its own second check.
- `DISAGREE` and `NO ANSWER` never pass, whatever a need says.
- An eye need passes on a pick, and on nothing else. A stand-in's passes it too, so a run can be proved with no Sponsor there, and the reading says who it was.
- **A pick holds only for what it saw.** Each file on its sheet is held by a hash of what it contains, and the clean run has to leave the same, with some of the `part` among them. Otherwise the need fails, saying which file changed, and it's asked again. A STEP file's header names the second it was written, so its hash starts at its `DATA` section, or every clean run would count as a change. A pick of `none` takes back any pick before it, and a pick from before this holds for nothing.
- A `part` a clean run didn't leave isn't met, whatever the needs read.

Needs that fail go back in `done`'s reply, with each reading against its limit and the last round's, and the next round starts from what's there: the part, the script and `record/ROUNDS.md`. The conversation carries on, with its reasoning.

**The needs are settled once.** They're what the work is checked against, so the model can't change them. `resume --reopen-needs` lets it, when a person decides a need's check is wrong, and so does `--send-back --reopen-needs`: on `cup-1` the model tried to restate its checks after a send-back, was refused, and ran the missing ones as plain checks, so the approval rested on checks that weren't in its needs.

**Checks are held in the harness, not in a note** (mqucifer/print-shop#26). On `cup-1` a lesson about checking, given to the model in its request, held until a check stood in its way, and then the check was changed. So these are refusals:
- **A need can't pass on `FAIL`,** as it can't on `DISAGREE`. On `cup-1` one passed `capture` on FAIL.
- **A need whose check fails on the files as they stand can't be dropped, or changed to a check that passes, without a reason** (`why_changed`, by the old need's id). The harness reads the old check, and the new one, against the files first. A change to a check that fails too needs none. Each swap is in `need_swaps`, and shown once, at the next ask or stop for approval, with both checks, both readings and the reason.
- **Every file of the `part` passes `thin`** at the nozzle's width before the run stops for approval, whatever the needs say. Two panes the Sponsor found too thin in Orca passed every need, as no need ran `thin` on a pane. `DISAGREE` or `NO ANSWER` there is said at the stop and in the README, and taken as neither.
- **Files printed together can't take the same space.** `done` names `groups`, the ways the part prints, each with its own files, and a file of `part` in no group prints with every group, as panes do. With no `groups`, the whole `part` is one. A pair in one group that shares more than 0.5 mm³, by Manifold and again by OCCT's `common`, isn't met. `clearance` across 16 files ran past 600 s, so this is a helper that asks only that (`bench/overlap.py`).

Each of these is in the ledger's `standing` table, by file and its hash, and a round where every need passed and these didn't has `met` 0.

`done` also asks what kind of change the round was (`fix_shape`) and for the model's closing words (`closing`), so neither column is empty. Both are in its own words, kept as such, and decide nothing.

**What's the part.** `done` names the files that are the part (`part: ["stand.step"]`), as a need can name a test piece beside it. Each entry is one row of the sheet, and files seen together are one entry: `{"name": "wide", "files": ["base.step", "lid.step"]}`. `final-sheet.png` draws those alone, so two outlines in one `part` aren't drawn on top of each other. It's the model's word and it decides nothing else. A sheet's `files` are what the Sponsor takes for the part too, and `test_pieces` on `ask` shows others, see-through in grey and marked.

**Approval is a stop.** The Sponsor's definition of done (2026-11-02): design rounds, their approval, a refinement pass whose findings go in the ledger, then the learn loop. `met` means every need passed its check, and every file the harness's own, and doesn't mean approved. When they do, the run stops instead of ending, and `done` can't end it. `approvals/n/` has the sheet of the final part, drawn in the `looks` `done` gave, and `stop.md`, with each reading, `thin` on each file, any need swapped since the last stop, and the model's closing words, and `what_next` prints those words too. The model is shown the same sheet.

**The delivered result is `review/`,** written by the harness at each stop for approval from its records, into `approvals/n/review/` and copied to the run's `review/`. One folder a group, lettered (`A-two-parts`, `B-one-frame-with-pause`), holds its own files under their names and every shared file, each named by its number, its label and its colour from `looks` (`pane_01-cup-blue.step`). Its README gives the size, which files take the same space and so are never loaded together, a table of the shared files, each requirement's reading against its limit and the files its check read, `thin` on each file, and what wasn't checked: files no need's check named, eye needs that passed on a pick, `thin`'s unsure answers and the swaps. The model's closing words are under their own heading. Nothing in it is sliced or printed.
- `resume --approve --by WHO` ends the run as `approved`, with no model call, and then the model writes its after-action review (below). The run is approved whatever the review does.
- `resume --send-back --by WHO --note FILE` goes on as a new round, with the note. A plain `resume` is refused here.
- Anyone else is `--by "stand-in: <who>"`, as at a pick. `decisions` holds which, and who.
- A send-back after approval is by hand, with the same `--send-back`. It becomes automatic only once there are more mechanical checks or agreements ahead of time. The refinement pass is still to come; the delivered result is `review/`, below.

**Stopping for the Sponsor.** Only `ask`, where form is at stake, in two calls. `ask` names the variants and `render` draws the sheet, which goes to the model and not yet to the Sponsor. It can add close-ups (`close`), cut-throughs (`cuts`: `z=3`, or `x=50,y=20:40` for a pane in its pocket) and each file's colour and label (`looks`). Then `send_ask` sends it with `says`, what the model says the sheet shows: on both real runs it described the design it meant, not what a bench picture showed. A file changed in between is refused, and a new `ask` replaces the drawing. Whatever the question says, the harness reads each settled need's check and `thin` against the sheet's files as they stand, and any that fail go beside the question in `question.md`, with what the bench says it drew and any need swapped since the last stop. On `cup-1` a question said a pane was split where `capture` read one. The process then exits. A person relays it, writes `pick.json`, and runs `resume`. A pick with no reason is refused, since the reason is what's learned from. After `needs`, `questions` is refused.

**A pick is about the look.** A build fault found at an ask goes in `"fix_first"` in `pick.json`, apart from the pick: it reaches the model as its own message, is `picks.fix_first`, and is an intervention of its own kind. On `cup-1` the Sponsor was asked to answer `none` for one, which mixed two answers. The pick still holds only for the files it saw, so the fix means asking again.

**A pick says who made it,** so someone else's can't pass for the Sponsor's taste in later training data. `pick.json` is `{"pick": "A", "reason": "...", "by": "sponsor"}`, with `"pick": "none"` if nothing's right, and `"fix_first": "..."` if there's a fault to fix. Any other key is refused. Anyone else is `"by": "stand-in: a Claude builder"`, naming who. Anything else, or nothing, is refused. Text says "the Sponsor picked" only when they did: what the model's told, the reading, and `record/ROUNDS.md`. The ledger has it in `picks.picked_by`, and `summary.json` in `picked_by`. An empty `picked_by` says nothing of who it was.

| Ending | When | Whose |
|---|---|---|
| `approved` | A person said yes at the stop for approval | a person |
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

`questions`, `ask` and `met` aren't endings: the run waits, and `met` is what it rests in until a person decides. A run from before approval was a stop that says `met` stays ended, and `resume` refuses it. **`resume` goes on after any other,** from the very message it stopped at, with `--note` adding a message, and `--from` saying who the note is from. A crash loses at most the last call's answer, and that call is made again. A conversation started from the record keeps the notes as well.

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
| `interventions` | Every note, answer, pick and send-back relayed to the model: when, at which call, who it's from, its text. The largest cause of change in a run |
| `timeline` | Each start, stop, resume, compaction and restart, in order. A compaction has its size before and after, estimated and measured, and each process its harness commit, where `runs.harness_commit` holds only the first |
| `calls` | Each call's seconds, tokens, finish and share of repeated reasoning, with its round, its phase (how many times a person had sent something) and its process |
| `phases`, `round_costs` (views) | Calls, seconds and tokens, split by what a person sent or by round |
| `runaways` | A call that hit the limit, was cut, or whose reasoning repeats, for review. See below |
| `tries` | Each reading from a `try` |
| `approvals`, `decisions` | Each stop for approval, with its sheet's hash, the part's hashes, the closing words and the delivered folder's groups and files (`review`), and each yes or send-back, and who |
| `standing` | Each check the harness ran itself, whatever the needs say: `thin` on each file at `done`, and the needs' checks and `thin` at an ask, each with its file's hash. `unsure` is the bench with no answer to rely on |
| `need_swaps` | Each need changed or dropped while its check failed on the files, with the old check and the new, both readings, the model's reason, and where it was shown |

`picks.seen` holds each variant's files, its test pieces and its sheet, by hash, `picks.said_of_sheet_model_said` what the model said the sheet shows, in its own words, and `picks.fix_first` a fault sent back apart from the pick. `rounds` gains `part_rows`, `closing_model_said` and `judged_files`, what the clean run left by hash.

| `after_actions` | Each try at a run's after-action review: which approval, its calls, the commit, and how it ended (`reviewed`, `not_begun`, or the ending that stopped it) |
| `lessons` | Each lesson: the run it came from, who wrote it (`model`, or a person, signed `reviewer: <who>` or as at a pick), the fix's shape in its writer's words, its kind, its text, its evidence (calls and readings, by the ledger's keys), its status, what a lesson about checking `becomes`, and what it `became` once promoted, and where |
| `lesson_steps` | Each change to a lesson, who made it and why. An edit keeps the words it replaced |
| `run_lessons` | Each lesson a run was given, how (`start`, or by hand in the request before this), and the words it was given in |
| `lesson_scores` | Whether a run used each lesson it was given (`used`, `not_used`, `in_the_way`), with the calls that show it, by the model at its review or by a reviewer |

Looking a lesson up when a reading fails isn't here yet.

**Written from the events.** `records.py` takes a run's events one at a time, live or from the file, and a second reading adds nothing, so a table doesn't depend on when it was read, and a process that was killed catches up when it's next loaded. `read-back` does it for a run that ended, and only reads its folder. A run from before these tables reads back with less: a note's sender is only "the people running this", its time is its first call's, and who made a pick comes from `picks.picked_by`, never from the message's words, since a stand-in's pick was once told to the model as the Sponsor's. Nothing is backfilled where it wasn't recorded: `fix_shape`, closing words, chunk times, lost arguments, and a need's `source`.

**The ledger only gains.** Tables and columns are added on open, and existing rows stay. An older checkout may still be running against it, so nothing is added to a table it inserts into by position (`needs`, `readings`).

### Runaways are evidence

The Sponsor: "When we get these run-away calls or repeating itself constantly its almost ALWAYS has been some issue with its context or tool or skill not working well. So to me these are not throwaways - its the obvious spot to look for fixes first." **Nothing here cuts a call or limits thinking.** Crew's whitespace cut is still the only cut.

- **A tool call's arguments are kept as they arrive,** in `streams/n.jsonl`, on the disk before the next piece comes. It stays if they didn't all come whole. `calls.stream` has the chunks' times, the longest gap and how far the arguments got.
- **It can't keep what never arrives.** On one real run the arguments of a call cut off at the limit never arrived, and the likely cause is the server holding them back until they're complete, which hasn't been probed. If so, `tokens_unaccounted` on the row, and `tool_args_chars` of 0, are what show it.
- **A row in `runaways`** is a call with a `finish` of `length` or `runaway`, or whose reasoning repeats earlier text past `Limits.review_repeat_share` (0.5, recorded on the run). Repetition counts sentences of 25 characters or more that match an earlier one word for word, by characters, over calls with at least 2,000 characters of such sentences. Paraphrase isn't counted, so it's a floor.
- **`doing`** is what it was calling, with any path, and how its text and reasoning ended. **`lacked`** is the evidence code can give: the skills read and not read, the compactions so far, the last bench answer, the last refusals and the needs failing. What it really lacked is a reviewer's call, which `review_note` is for.

## Lessons

The first of the learn loop in code (mqucifer/print-shop#28, M18's "learning logic", ADR 0010's decision 5 as changed on 2026-11-02). Before this, the operator wrote lessons into a run's analysis and copied them into the next run's request by hand. On `cup-1` four were given that way. The two about how to build were followed. The one about checking held until a check got in the way, and then the check was changed.

**The model writes its run's lessons, after the approval, while it still has its context.** `resume --approve` records the decision and ends the run as `approved`, then makes one more turn on the same session. `after_action.py` holds it, apart from `review.py`, which writes the folder a person prints from: the two never share a name, in code, in what the model's told, or in the ledger.
- The model is told the part is approved, with the person's note, and asked for its after-action review. The message lists the lessons it was given at the start, and the run call by call from the ledger: the tools each call made, each bench answer and its verdict, what a person sent before it, and each round's readings where the round ended. A lesson names its calls from that.
- Only `read` and `after_action` work in it: the files stay as approved.
- `after_action` takes `lessons`, each with its fix's shape, its kind (`build`, `check` or `context`), its text, its calls and any readings, and `scores`, one for each lesson it was given: `used`, `not_used` or `in_the_way`, with the calls that show it. Every call must be one the ledger has of this run, every reading one it read, and every lesson given scored once, or nothing is kept and it's told what's wrong. Each lesson it writes is a `candidate`.
- **The run stays approved, whatever happens.** A review that can't begin, with no key or no LiteLLM, is a row of `after_actions` with `not_begun`, and so is one that ends any other way. `rounds.py after-action NAME` tries again, and a reviewer can enter lessons by hand. Its calls have a cap of their own (`Limits.after_action_calls`, 30), only so a review that never calls `after_action` ends by name. Nothing cuts a call or limits thinking. The timeline has the review as its own segment, and the approval is an intervention of its own kind, `approved`.

**A reviewer checks each against the ledger.** For now that's the operator, who signs `--by "reviewer: <who>"`: checking lessons is the reviewer's own job, not standing in for the Sponsor. `sponsor` and `stand-in: <who>` are taken too. Picks and decisions don't take `reviewer:`.
- `lessons RUN` lists what the run wrote, each with what the ledger holds at each call and reading it names, then what it was given and how each was scored. `lessons` alone lists every lesson.
- `accept`, `edit` (`--text FILE`, `--kind`, `--shape`) or `reject` (`--why`), each a row of `lesson_steps`.
- **A lesson about checking is never given as a note.** It's accepted only with what it `--becomes`, `refusal` or `bench_check`, and built in the harness. Edited into one, it says the same. `promote ID --as refusal --where owner/repo#n` records it once built.
- `lessons load FILE --by "reviewer: <who>"` takes a reviewer's steps from a file, in order, by the same rules: `{"steps": [{"do": "write", ...}, {"do": "accept", ...}, ...]}`, as `lessons.load` says. It's all checked before anything is written, and refused whole if a lesson is already there. It's how lessons are entered by hand, and how the operator enters the first rows.

**The next run starts with what was kept.** `start` puts every kept lesson about how to build (`accepted`, `edited`, `given`, `held` or `broken`, and one promoted to a note) into the first message, after the request and its files, under `## Lessons from earlier runs`, each with its number, the run it came from and who wrote it. `lessons.md` in the run's folder holds them as given, so a conversation started again from the record has the same, and `run_lessons` has a row for each. For now every kept lesson goes to every run; matching by part comes with more lessons. Context lessons are for the people running it, and aren't given.

**Scoring and retiring.** A run's latest score moves a lesson it was given: `used` is `held`, `in_the_way` is `broken`, and `not_used` leaves it. A reviewer can score again (`lessons score RUN ID`), and the latest counts. A lesson given twice and not used, or that got in the way, is listed for the reviewer under "To retire", and `retire ID --why` takes it out. Nothing retires one by code.

**Its statuses:** `candidate`, then `accepted`, `edited` or `rejected`, then `given`, then `held` or `broken`, then `promoted` or `retired`.

**The first rows** are the lessons of `stainedglass-1`'s and `cup-1`'s analyses, written by the operator, with `cup-1`'s scores. Their words come from the Sponsor's runs, so they're a file off this repository, loaded with `lessons load` after this merges.

**Not yet:** looking a lesson up when a reading fails, runs with and without a lesson to measure its gain, promotion into skills by code, and the crew's lessons (M16).

## A run's folder

| Path | Holds |
|---|---|
| `request.md` | As it came |
| `work/` | `/work` in each container, and the only thing model-written code can touch. `references/` is what was given, `record/` what the harness keeps for the model to read, and nothing is ever read back from it |
| `events.jsonl` | Every call, with its reasoning, and every tool call. `transcript.rebuild(events, n)` gives call `n`'s exact request, across stops and compaction. It's what could later train an adapter (M14) |
| `views/` | Each picture the model was shown |
| `state.json`, `needs.json`, `needs/` | Where it stands, and the needs |
| `questions/n.md`, `n.answers.md` | Each stop at intake |
| `asks/n/` | `question.md`, `sheet.png`, `variants/`, `test-pieces/` if it showed any, `asked.json` with the looks, the model's words on the sheet and the standing checks, and `pick.json` |
| `rounds/nnn/` | `judged/` (what the clean run left), `readings/` (each need's answer, in text and numbers), `standing/` and `standing.json` (`thin` on each file, and which files take the same space), `run.txt`, `judgement.json`, `diff.patch` |
| `approvals/n/` | `sheet.png`, `stop.md`, `closing.md` and `review/`, for the person who decides |
| `review/` | The latest stop's folder to print from: one folder a group, and its README |
| `streams/n.jsonl` | A call's tool-call arguments as they arrived, if they didn't all come whole |
| `lessons.md` | The lessons it was given at the start, as given |
| `after-action-fault-n.txt` | A fault of ours in the after-action review, its trace |
| `final-sheet.png` | The latest `part` to pass, one row each, for the people who see the result. It decides nothing |
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
- **The pick wasn't held to the final part.** An eye need passed on the pick, and nothing compared what passed at `done` with the variant the Sponsor picked. Here the two read the same volume by the bench, and that comparison was by hand. mqucifer/print-shop#24 holds a pick to its files.
- **A design made before `needs` is round 0.** `bench_calls` holds its readings with round 0, and `rounds` starts only at `needs`, so here one "round" follows 32 calls of real design work.

## Tests

`uv run pytest -q tests/test_rounds.py tests/test_records.py tests/test_delivered.py` needs no Docker. `test_records.py` reads back a run made up in its old shape. `uv run pytest -q tests/test_rounds_in_image.py`, under the `bench` mark, has a scripted fake model drive a real run in real containers, with no model call: every tool, every ending, a resume after `ask`, a failing need going back, and a `done` the checks refuse not ending the run. The part is made up in the test, of plain boxes.

`tests/test_lessons.py` needs no Docker: the after-action review on a run put at its stop, each refusal, the reviewer's steps, giving at the start, scoring and retiring, a file of steps, and the command line with no key. `tests/test_lessons_in_image.py`, under `bench` in the `bench` job's half, goes the whole way in real containers: a run met by the harness's checks, approved, its review, the reviewer's step by the command line, and the next run starting with the lesson, using it and scoring it.

`tests/test_rounds_cup_local.py` (`-m bench_local`) reads `cup-1` back where the Mac holds it, and skips elsewhere: its request, references, needs and each round's scripts, with every name read from the run's own folder, so nothing of it is in this repository. `needs` refuses the version that passed on FAIL, round 1 doesn't stop for approval as `thin` fails two panes, and round 2's `review/` holds the same files, by what they hold, under the same names as the folder made by hand. Its run is kept in `~/print-shop/proofs/m18-truth/`, with `figures.json`.
