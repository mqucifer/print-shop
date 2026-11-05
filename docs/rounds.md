# The rounds

The local model works in rounds on the bench, and every round is recorded (mqucifer/print-shop#17, M18 in mqucifer/project-print `ROADMAP.md`, ADR 0010 there). It closes the loop [the bench](bench.md) left open: intake, make or change, read the bench's answer, change again. Limits start high and are lowered from evidence, there's no budget on thinking, and every failure has a named ending.

```
scripts/rounds.py start NAME --request FILE [--reference FILE]...
scripts/rounds.py resume NAME [--note FILE] [--from WHO] [--reopen-needs]
scripts/rounds.py resume NAME --approve --by WHO [--note FILE]
scripts/rounds.py resume NAME --send-back --by WHO --note FILE [--reopen-needs]
scripts/rounds.py resume NAME --close --by WHO --note FILE
scripts/rounds.py status NAME
scripts/rounds.py stop NAME
scripts/rounds.py summary NAME
scripts/rounds.py read-back NAME [--ledger FILE]
scripts/rounds.py after-action NAME
scripts/rounds.py lessons [RUN] [--ledger FILE]
scripts/rounds.py lessons accept|edit|reject|retire|promote ID --by WHO ...
scripts/rounds.py lessons score RUN ID --score used|not_used|in_the_way --by WHO [--calls N,N] [--route R]
scripts/rounds.py lessons load FILE --by WHO
scripts/rounds.py refine NAME --nozzle MM --filament [COLOUR=]NAME...
scripts/rounds.py refined NAME
scripts/rounds.py proposal NAME ID --approve|--reject --by WHO [--note FILE]
```

It needs the bench's image (`scripts/bench.py build`), and LiteLLM on :4000 with print-shop's key from `~/.config/print-shop/.env`. It uses `crew-code-think`, and probes nothing else. **A run's record says what reasoning effort its alias sends** (`runs.reasoning_effort`, and `reasoning_effort` in `summary.json`), read at `start` from LiteLLM's own `/model/info` for this key: the alias's `litellm_params.reasoning_effort`, or its `extra_body`'s, or in `extra_body.chat_template_kwargs`. It's no model call, and SGLang isn't reached. If LiteLLM won't say, the record says `unknown` and why (`reasoning_effort_from`). Nothing else is kept from the answer, which can name a server's address. The effort stays as it is (the Sponsor, 2026-11-03): it's recorded, not changed. **A run names its code,** so `start` and `resume` refuse a checkout with uncommitted changes. As in `cad_eval`, run from a checkout nothing edits until it ends: `git worktree add --detach ../run-<commit> <commit>`. One session runs under a root at a time (`.session.lock`). Runs live in `~/print-shop/design-runs/`, off this public repository, and `start` refuses a folder inside it.

## A run

**Intake.** From the request and any reference files, the model reads what it's given and asks what it needs, as an interview. `questions` writes them to a file and the process exits. A person relays them, writes the answers to the file it names, and runs `resume`. Before its needs, it writes down how the job is built with `breakdown` (below). `needs` ends intake. Each need says how it's checked, by a bench command and its limit, or by the Sponsor's eye. A quote from the Sponsor is held to their words. Writing and running are open throughout. `needs` with `try: true` reads each bench check against the files as they stand, and settles nothing, so it needs no breakdown. Each reading reaches the ledger's `tries`. A need's `source` says where it came from: `request`, `answer` or `note`, each with a quote held to that source's words, so what a person relayed in a note isn't marked `derived`.

**The breakdown comes first** (mqucifer/print-shop#36). In all three stained-glass runs the model read the reference for what's in it, not how it's built: on `suncatcher-2` it drew each ring as one loop where short bars cut them, and the frame came apart into three bodies. So `needs` is refused until `breakdown` has named the parts, what holds or connects each to the rest, and what the look rests on. It's held to its shape, not its words: each part named once, each join naming parts that are there, and with more than one part, every part in a join, since a part nothing holds is the one that comes apart. It's general to any part: a bracket's plate, arm and brace, or a lamp's shade and socket ring. It's kept in `breakdowns/n.json`, `breakdown.md` and `work/record/breakdown.md`, and the ledger's `breakdowns`. The model can send it again whenever it finds it wrong, and the plan stop shows the latest.

**Rounds.** The model makes and changes the part, and calls `done` when it believes the needs are met. The harness then runs the script again and each need's check itself, so the model's word and a tool's success don't count:
- The clean run has a fresh container, over a plain copy of `work/` with no links, nothing a run left, and all geometry outside `references/` taken away.
- Each bench need then runs in another fresh container, where no model code has run. `params` runs the script by its nature, on a scratch copy, so its reading is as good as its own second check.
- `DISAGREE` and `NO ANSWER` never pass, whatever a need says.
- An eye need passes on a pick, and on nothing else. A stand-in's passes it too, so a run can be proved with no Sponsor there, and the reading says who it was.
- **A pick holds only for what it saw.** Each file on its sheet is held by a hash of what it contains, and the clean run has to leave the same, with some of the `part` among them. Otherwise the need fails, saying which file changed, and it's asked again. A STEP file's header names the second it was written, so its hash starts at its `DATA` section, or every clean run would count as a change. A pick of `none` takes back any pick before it, and a pick from before this holds for nothing.
- A `part` a clean run didn't leave isn't met, whatever the needs read.

Needs that fail go back in `done`'s reply, with each reading against its limit and the last round's, and the next round starts from what's there: the part, the script and `record/ROUNDS.md`. The conversation carries on, with its reasoning.

## The request's form

A request is free text, as it always was, with up to four headings of its own (`request.py`). Each line under them is one line of the request, `- ` or `1. ` before it or not, and a line that goes on indented is joined to the one above.

| Heading | Its lines | Held how |
|---|---|---|
| `## Requirements` | What the part must do, ids `R1`, `R2` | Each is covered by a need that names it in `covers`, or `needs` is refused naming the lines left over, with their words |
| `## Non-negotiables` | What mustn't change, ids `N1`, `N2` | Each is covered by a need marked `fixed`, whose quote is from that line. A need that covers one is fixed. Where the line holds a figure, the need says in `figure` how it's read, and where something changes it, which: a lip before or after play. The model is told to ask with `questions` where the request doesn't say |
| `## Kind` | One word, such as `stained-glass` | Read and kept, and `start` gives the lessons for that kind of part (`lessons.part_kind`) with the general ones, and loads its toolset: its bench commands, measures and skill (below). A kind with no toolset of its own has the core |
| `## Stops` | What's shown before the run goes on, in order, ids `S1`, `S2` | Below. The line `no plan` leaves the plan out |

**A request's pictures and reference files go in at `start`** (`--reference`), with the request, so the plan is written from them.

**An object the part must fit goes in with the request as a reference,** such as bought hardware or a tealight (`start --reference`). Whoever writes the request draws it from its maker's figures; the model never makes it. A need reads only the part and `references/` (mqucifer/print-shop#45), and on `tealight-1` the fit was checked against a tealight the model drew.

**A request that gives a part to change says what else may change.** On `eval-tip-1` the model read a request for a pane as leaving the given frame as it was, and didn't ask.

Coverage is held by ids, as `needs` holds quotes, and nothing is guessed from words: the only reading of a line's words is whether it holds a digit. **A line is covered by an eye need only where no bench command measures it,** and the harness can't know that on its own, so an eye need that covers a line says why in `check.no_bench`, and the person reads it at the plan stop. Where mqucifer/print-shop#37 settles which command measures what, that list can hold it too.

**A request in the old free form still runs.** With no `## Requirements` or `## Non-negotiables`, it has no lines, and nothing is checked for coverage. It still has the plan as its stop, and the breakdown before its needs. A run started before this has no form in its `state.json`, and carries on as it began: no lines, no stops, no breakdown asked for.

The first message lists every line and stop by its id, and the ledger's `request_lines` keeps them. The `needs` table is inserted into by position by older code, so each need's `covers`, `fixed`, `figure` and `no_bench` are a row of `need_marks` instead.

A worked example, of a part that isn't stained glass:

```
A wall bracket for a small shelf, in PETG. It should look plain and sturdy.

## Requirements
- It holds a 6 kg shelf without bending.
- Two screw holes, 4.5 mm across, for the wall.
- It should look plain, with no sharp corners.

## Non-negotiables
- The arm is 120 mm long, from the wall to its tip.
- The plate is at least 4 mm thick, measured
  after the countersinks are cut.

## Kind
bracket

## Stops
- the 2D profile of the arm
- one bracket printed as a test, before the pair
```

Its needs cover `R1` to `R3`, `R3` by eye with its `no_bench`. `N1`'s need is `fixed`, quotes "The arm is 120 mm long", and says in `figure` that the length is from the wall face to the tip. `N2`'s is `fixed` too, and its `figure` says the thinnest is read after the countersinks, as the line says. The stops are `plan`, then `S1` and `S2`. `tests/test_intake.py` runs this request, and a lamp's.

### A fixed need

**Its check counts only at its own settings** (mqucifer/print-shop#37, criterion 2). `suncatcher-2` passed `capture` at `--min-lip 0.25` where the request said 1.2. A `bench` call of a fixed need's check with any other argument is answered first, after its exit and before the bench's own verdict, as `NOT THE REQUIREMENT`, with the need's words and its own command, from the first try, pass or fail. Its reading can't pass the need, as `done` runs the need's own command, and a pass at other settings doesn't start the nudge's count again. Its call's `detail` has `not_the_requirement`. A need that isn't fixed says the same, more quietly, after the bench's answer, as before.

**It changes only by the Sponsor,** through `resume --reopen-needs` with a note, `--from sponsor` or `--from "stand-in: <who>"`, or a send-back with `--reopen-needs`, which has both. Any other reopening, such as a note from the people running this, opens the other needs and says the fixed ones stay. A `needs` that drops a fixed need, unmarks it, or changes its check, its quote, its lines or its `figure` is then refused, naming its check as it was. The opening holds for one settling.

## Stops

**A stop the request names is held** (mqucifer/print-shop#38). On `stainedglass-1` the plan was written at call 5 and nobody saw it, on `cup-1` the test of one pane was skipped, and on `suncatcher-2` the 2D layout's `ask` was skipped, and its fault cost a redesign. Each of `## Stops` is shown with `ask`, naming it in `stop`, in order. Until its ask has a pick, `done` and the next stop's ask are refused, and the refusal names the stop that's open. **A stop holds what's handed over, never the work:** writing, running, the bench and any other `ask` stay open throughout, and an ask that isn't the open stop says that it's still open.

**The plan is the first stop,** `plan`, unless `## Stops` says `no plan`. It's asked once the breakdown and the needs are settled, as it shows both: the model's `plan`, in its words, the breakdown, each need with the lines it covers, whether it's fixed and how its figure is read, each line with the needs that cover it, and any figure of a non-negotiable that no fixed need's check carries. That last is said and never refused, since a figure may be one no bench command reads. The plan comes after the needs so the person sees what the work will be checked against, the fixed needs above all: a figure read the wrong way is caught there, before the first round, and `resume --reopen-needs` with the pick reopens them.

**A pick passes the stop. `none` leaves it open:** what was shown wasn't right, so it's shown again before anything's handed over. The model's told which, and the next stop. **A `none` at the plan reopens the needs too** (mqucifer/print-shop#45), since the plan shows them: on `eval-tip-1` the stand-in's reason named the fit need's check, the model was refused (`needs_settled`) when it moved the need onto the frame, and the operator reopened them by hand. The model's told with the pick, and the fixed needs stay as they are: they change only through `--reopen-needs` with the Sponsor's note. At any later stop a `none` opens nothing. `state.json` has each stop passed, with its ask, the pick, who made it and at which call. The ledger's `picks.stop` names the stop an ask showed, and the view `stops` gives each stop asked, what it is, at which call, and picked when and by whom.

**The needs are settled once.** They're what the work is checked against, so the model can't change them. **A `needs` refused once they're settled goes beside the next question,** in `question.md` under "`needs` calls refused since the last stop", with what it would have changed need by need, so a person sees it: on `eval-tip-1` the model's next ask didn't say it had been refused. The refusal tells the model so. `resume --reopen-needs` lets it, when a person decides a need's check is wrong, and so does `--send-back --reopen-needs`: on `cup-1` the model tried to restate its checks after a send-back, was refused, and ran the missing ones as plain checks, so the approval rested on checks that weren't in its needs.

**A need's check measures what it names** (mqucifer/print-shop#37). Each bench need says what it's about in `check.measures`, from its toolset's short list (`toolsets.py`), and `needs` refuses a command that doesn't measure that, naming the one that does. In all three stained-glass runs the hanging requirement was settled on a stand-in, the frame's bounds by `info` or the whole part's `thin`, where `wall` reads the wall round the hole: `"measures": "wall"` on `info frame.step` is refused, "`wall` does, as `wall FILE --hole X,Y,Z --min-wall W`". A short list the need declares, rather than words in its text, because words both miss and misfire: "wall" is in "the panes sit between the frame's walls" as well as "2 mm of wall round the hanging hole", and a refusal that fires wrongly stops a run. The declared list is exact against the command, and the person at a stop reads each need's `measures` beside its quote. The core's are `size` (`info`), `fit` (`clearance`), `cut` (`readback`), `hole` (`rays`), `wall` (`wall`), `printable` (`thin`), `one-piece` (`thin --one-body`), `insert` (`pause`) and `parameter` (`params`). `render` measures nothing: what only an eye can judge is an eye need. Stained glass adds `pane-hold` (`capture`), `pane-fit` (`panes`), `light` (`light`) and `pane-thickness` (`thickness`). **A `light` or `pane-thickness` limit is the request's:** the need gives one, as the command's option or a `number`, its source is `request`, `answer` or `note`, and the figure is in its quote. There's no default anywhere. A run from before toolsets reads its needs as it did, with no `measures`.

**A need's `number` is one its command reports** (mqucifer/print-shop#45). On `eval-rings-1`, `needs` took `number: min_wall_mm`, where `wall` reports `least_wall_mm`, and only the harness's clean run at the end found it. `rounds/reports.py` lists each command's numbers, and settling refuses a `number` whose first part isn't one, naming those the command reports. What's under a list or a dict isn't listed, as it depends on the part. `tests/test_reports.py` reads each command's numbers from the bench's own code, through each function its entry calls, so a number added to the bench and not to the list fails a test. A `try` isn't held to it: its reading says which numbers the answer has.

**A need is checked on the real part** (mqucifer/print-shop#45). On `eval-tip-1` the fit need ran `clearance` on `pocket_cavity.step`, a pocket the model had modelled itself, and passed while the frame it was given had almost no lip at the tip. So `done` refuses a need whose check names a file that's neither in `part` nor under `references/`, naming the need and the file, before anything runs (`need_off_part`). The needs are settled, so the model asks: at an ask, each such need goes beside the question in `question.md`, against the files on the sheet and the part `done` last named. With neither, as at the plan, it lists each need checked on a file the model made, saying `done` will hold each to the part. A need checked on a model-made stand-in for something the request describes, such as the tealight of the first real run, is refused too: the stand-in goes in as a reference, or the person reopens the needs.

**A part file whose last `thin` failed is said until it passes** (`unprintable.py`, `suncatcher-2`'s lesson 21). On `suncatcher-2` one pane failed `thin` nine times in a row, and the run moved on to other files without it passing. Every later `bench` and `run` reply, and `write` or `edit` with `run`, ends with each such file, the call it failed at and what `thin` said, and that a file failing `thin` can't be printed: fix it and run `thin` on it again before going on, or take it out of the part. It counts the model's own `thin` at the harness's settings (`--one-body` is the same check, stricter; a narrower `--nozzle` is another) and the harness's at `done` and `ask`, and `DISAGREE` or `NO ANSWER` leaves a file as it was. A file that's gone, or that a later `done` left out of `part`, has left the part. It isn't a refusal: `ask` stays open, and its draft and `question.md` list the files under "Part files whose last `thin` failed".

**Any bench check that failed is said until it passes** (`holding.py`, mqucifer/print-shop#45). On `eval-tip-1` `capture` failed on the frame at call 32 and again at call 87. No need ran `capture`, so nothing kept it in front of the model, and both times it went to its plan stop without naming the failure. So every later `bench` and `run` reply, `done`'s, the ask's draft and `question.md`, and the stop for approval's `stop.md`, list each check (its command and files) whose latest try failed, with the call and what the bench said, as `thin`'s line does:
- **A check a settled need runs** clears only on a pass as the need checks it: its own command, read against its limit, so `capture` passing at 1.2 with `least_lip_mm` under the need's figure is still a failure. A pass at other settings doesn't clear it, as with `NOT THE REQUIREMENT`. The harness's own readings of the need, at `done` and at an ask, count too.
- **A check no need runs** stays failing at each setting it failed at until a pass at those settings. A pass at the settings it was first tried at clears every setting, as the others were probes on the way: `eval-tip-1` lowered `--min-lip` from 0.4 to 0.1, failed both, and later passed at 0.4.
- `DISAGREE`, `NO ANSWER` or a container's exit leaves it as it was, and a wrong command isn't a try. `thin` at the harness's settings is said by its own line above, not twice.
- **It leaves** when its files are all gone, or a `done` after it named a part with none of its files in it. A check on the given references is held as a check on the model's own files is.
- **Or the model says why it doesn't apply,** with `set_aside` and its reason. It's out of the list until it fails again, a row of `set_asides`, and shown once at the next ask or stop for approval under "Failing checks the model set aside". A check a settled need runs can't be set aside: `done` decides it.

Replayed on `eval-tip-1`'s record, the list names call 32's `capture` from then on, and calls 80 and 87 at the second plan stop; at the third, call 126's pass at the first settings had cleared both (`~/print-shop/proofs/m18-hold/`).

**Checks are held in the harness, not in a note** (mqucifer/print-shop#26). On `cup-1` a lesson about checking, given to the model in its request, held until a check stood in its way, and then the check was changed. So these are refusals:
- **A need can't pass on `FAIL`,** as it can't on `DISAGREE`. On `cup-1` one passed `capture` on FAIL.
- **A need whose check fails on the files as they stand can't be dropped, or changed to a check that passes, without a reason** (`why_changed`, by the old need's id). The harness reads the old check, and the new one, against the files first. A change to a check that fails too needs none. Each swap is in `need_swaps`, and shown once, at the next ask or stop for approval, with both checks, both readings and the reason.
- **Every file of the `part` passes `thin`** at the nozzle's width before the run stops for approval, whatever the needs say. Two panes the Sponsor found too thin in Orca passed every need, as no need ran `thin` on a pane. `DISAGREE` or `NO ANSWER` there is said at the stop and in the README, and taken as neither.
- **Files printed together can't take the same space.** `done` names `groups`, the ways the part prints, each with its own files, and a file of `part` in no group prints with every group, as panes do. With no `groups`, the whole `part` is one. A pair in one group that shares more than 0.5 mm³, by Manifold and again by OCCT's `common`, isn't met. `clearance` across 16 files ran past 600 s, so this is a helper that asks only that (`bench/overlap.py`).

Each of these is in the ledger's `standing` table, by file and its hash, and a round where every need passed and these didn't has `met` 0.

`done` also asks what kind of change the round was (`fix_shape`) and for the model's closing words (`closing`), so neither column is empty. Both are in its own words, kept as such, and decide nothing.

**What's the part.** `done` names the files that are the part (`part: ["stand.step"]`). A need's check reads files of the part or the given references, and nothing else (mqucifer/print-shop#45): a test piece a need reads is given, under `references/`. Each entry is one row of the sheet, and files seen together are one entry: `{"name": "wide", "files": ["base.step", "lid.step"]}`. `final-sheet.png` draws those alone, so two outlines in one `part` aren't drawn on top of each other. It's the model's word and it decides nothing else. A sheet's `files` are what the Sponsor takes for the part too, and `test_pieces` on `ask` shows others, see-through in grey and marked.

**Approval is a stop.** The Sponsor's definition of done (2026-11-02): design rounds, their approval, a refinement pass whose findings go in the ledger, then the learn loop. `met` means every need passed its check, and every file the harness's own, and doesn't mean approved. When they do, the run stops instead of ending, and `done` can't end it. `approvals/n/` has the sheet of the final part, drawn in the `looks` `done` gave, and `stop.md`, with each reading, `thin` on each file, any need swapped since the last stop, and the model's closing words, and `what_next` prints those words too. The model is shown the same sheet.

**The delivered result is `review/`,** written by the harness at each stop for approval from its records, into `approvals/n/review/` and copied to the run's `review/`. One folder a group, lettered (`A-two-parts`, `B-one-frame-with-pause`), holds its own files under their names and every shared file, each named by its number, its label and its colour from `looks` (`pane_01-cup-blue.step`). Its README gives the size, which files take the same space and so are never loaded together, a table of the shared files, each requirement's reading against its limit and the files its check read, `thin` on each file, and what wasn't checked: files no need's check named, eye needs that passed on a pick, `thin`'s unsure answers and the swaps. The model's closing words are under their own heading. Nothing in it is sliced or printed.
- `resume --approve --by WHO` ends the run as `approved`, with no model call, and then the model writes its after-action review (below). The run is approved whatever the review does.
- `resume --send-back --by WHO --note FILE` goes on as a new round, with the note. A plain `resume` is refused here.
- `resume --close --by WHO --note FILE` ends a run without approving it, below.
- Anyone else is `--by "stand-in: <who>"`, as at a pick. `decisions` holds which, and who.
- A send-back after approval is by hand, with the same `--send-back`. It becomes automatic only once there are more mechanical checks or agreements ahead of time. The refinement pass is still to come; the delivered result is `review/`, below.

**Stopping for the Sponsor.** Only `ask`, where form is at stake, at a stop, or when the model is stuck, in two calls. It works at any stage, with one variant of the files as they stand, and the answer comes back as a pick and its reason, with any `fix_first` and `--note`. **It needs no part file:** `pictures` shows PNG or JPEG files the model made, such as a 2D layout drawn with matplotlib, as they are, and at the plan stop `plan` is its text. A picture is shown to the model first, by the same reading as `read`, and goes in `asks/n/pictures/`, held by its hash as a variant's files are. On `cup-1` the model read its own matplotlib drawing right, and the bench's render wrong, so a plan goes as the model drew it, with no render in between and no container needed for geometry that isn't there yet. With no variants, the pick is `yes` or `none`. `ask` names the variants and `render` draws the sheet, which goes to the model and not yet to the Sponsor. It can add close-ups (`close`), cut-throughs (`cuts`: `z=3`, or `x=50,y=20:40` for a pane in its pocket) and each file's colour and label (`looks`). Then `send_ask` sends it with `says`, what the model says the sheet shows: on both real runs it described the design it meant, not what a bench picture showed. A file changed in between is refused, and a new `ask` replaces the drawing. Whatever the question says, the harness reads each settled need's check and `thin` against the sheet's files as they stand, and any that fail go beside the question in `question.md`, with what the bench says it drew and any need swapped since the last stop. On `cup-1` a question said a pane was split where `capture` read one. The process then exits. A person relays it, writes `pick.json`, and runs `resume`. A pick with no reason is refused, since the reason is what's learned from. After `needs`, `questions` is refused.

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
| `closed` | A person ended it without approving it, after any ending but `approved`, or at a stop | a person |

`questions`, `ask` and `met` aren't endings: the run waits, and `met` is what it rests in until a person decides. A run from before approval was a stop that says `met` stays ended, and `resume` refuses it, as it does a `closed` one. **`resume` goes on after any other,** from the very message it stopped at, with `--note` adding a message, and `--from` saying who the note is from. A crash loses at most the last call's answer, and that call is made again. A conversation started from the record keeps the notes as well.

A conversation that won't fit even compacted starts again from the record, keeping the request, the answers, the needs, each check's latest tries and the files. Its reasoning is what's lost, and `restarts` counts them.

### A call that ends without acting (mqucifer/print-shop#47)

Across the 1,122 recorded calls of 12 runs, 15 ended with no tool call, 12 of them after 16k tokens or more of reasoning, and 10 sat in two loops: `suncatcher-2` from call 248 to 261, and `eval-tip-1` from 70 to 114. Long reasoning alone isn't the sign, as most such calls end in a tool call. What both loops lacked was something to act on: a choice the Sponsor could make, or a reply the model could believe. So the answer is a question, not a cut (the Sponsor, 2026-11-04).

- **It's asked what's missing.** The reply says how long it reasoned, in tokens and time, and that this usually means something is missing: a figure, a reading, what a check's reply means, or a choice that's the Sponsor's. Then: say what with `ask`, and `send_ask`, or end with `blocked`. It replaces `QUIET`, which asked only for a tool call, and after which `suncatcher-2` reasoned to the cap three more times. Where there's no `ask`, as in the after-action review, it's `QUIET` as before.
- **A second one soon after gets a request whose only tools are `ask`, `send_ask` and `blocked`.** `send_ask` is there because an `ask` only draws the sheet. Soon is `Limits.ask_within`, 15 calls, recorded on the run: inside the two loops a repeat came 1 to 14 calls after the one before, and the one gap outside them was 141 (`~/print-shop/proofs/m18-ask/quiet_calls.json`). On the records it would first have come at `suncatcher-2`'s call 251 and `eval-tip-1`'s call 84. It holds until the turn ends in a stop, through a resume from the events, and a call to any other tool meanwhile is refused (`ask_only`) and not carried out.
- **Nothing cuts a call or limits thinking.** The `quiet` ending is as it was.
- **Each is recorded:** a `quiet_reply` event with the call, its reasoning's tokens and seconds, and the tools the next request offered, and a `timeline` row, `asked`, named `what_is_missing` or `ask_only`. The after-action index shows each after its call, and the review is asked whether the question named what was really missing.

### What a run keeps of what it learned (mqucifer/print-shop#39)

- **A shrunk reply says what failed and why.** Compaction shrinks every tool reply but the latest of each kind, and it used to keep only the first line, which for a failed `run` or `bench` is `exit 1`. On `suncatcher-2`, 139 of the 253 replies it shrank said only an exit code, and after a summary the model couldn't compare its failures. Now a bench answer keeps its exit and its verdict line with the reason, a run its exit and its last error (or the last line it printed), and a `write` or `edit` that ran a script what the run did (`session.gist`, at most 300 characters a line). Measured on `suncatcher-2`'s events, replaying each shrink: 139 such replies before, 0 after, for 11,334 more characters over its 24 compactions (`~/print-shop/proofs/m18-loop/compaction-suncatcher-2.json`). What stays whole is as it was: each file's latest text, the latest `run`, `bench` and `done` replies, the newest turn, and what a person said.
- **`record/CHECKS.md` keeps each check's tries,** beside `ROUNDS.md`, written by the harness after every bench call from `bench_calls`: each check (its command and files), the need that runs it, and each try's call, the settings that differ from the need's command (or from its first try, where no need runs it), and what the bench said. A wrong command isn't a try. A conversation started again after `context_full` is given each check's latest 6 tries in its first message, and the system prompt names the file.
- **After a compaction, the next message restates each check whose latest try fails** (mqucifer/print-shop#45): the same list as the replies', each line's reason cut to 100 characters, and each part file whose last `thin` failed, in one form. `Session.after_compaction` asks the run for it right after compacting, and says it before the next call; nothing is said when nothing fails. Replayed at every compaction of `eval-tip-1` and `suncatcher-2`, it's at most 281 and 304 tokens by today's estimate, under the 376 the compaction study allowed (`~/print-shop/proofs/m18-hold/replay.json`).
- **A refused `edit` says how its lines differ.** Of the 18 `edit.closest` refusals on `cup-1` (11) and `suncatcher-2` (7), sorted line by line against every version of the file the events give, none was text that had moved: 8 differed only in indentation, 3 left lines out of the middle, and 7 had a word or a number that was never in the file (`~/print-shop/proofs/m18-loop/closest_by_line.json`). So the reply names the difference: `edit.closest.indent` gives each line's indent and the file's, `edit.closest.apart` the lines left out, and `edit.closest` each of the model's lines that isn't there beside the file's nearest, with the closest block as before. Both new reasons start `edit.closest`, so a count of those stays comparable.
- **A call cut off at `max_tokens` goes back without its reasoning** (mqucifer/print-shop#47). Its text and its calls stay, its `call` event keeps all of it, and the change is a recorded rewrite, after a snapshot so `transcript.rebuild` replays it. The newest turn's reasoning stays whole at compaction, so on `suncatcher-2` each of calls 258-261, cut off at 65,536 tokens, was resent whole: each prompt after compacting was 108k tokens, 63% of it that reasoning, and it compacted once a call for 2.2 hours. Replayed with the recorded answers, main's rule gives 23 compactions and this gives 19, none of them in 259-263.
- **An edited file is kept as one current copy** (mqucifer/print-shop#47). A file's latest text used to stay as its `write` or whole `read` and every `edit` since, call and reply: four of the seven refused edits after `suncatcher-2`'s compactions were on one file kept as a write and 25 edits. Now the reply to its last edit carries the whole file as the edits left it, numbered as `read` shows it, and the rest are notes. The copy is rebuilt from the conversation, each edit applied as `edit` applies it: held to every edit's reply on the 12 recorded runs, all 226 agree (`check_copies.json`). Where it can't be rebuilt, or an edit is in the newest turn, the text and its edits stay whole as before. Replayed on the recorded answers with the cut-off rule, the files' kept text goes from 21.4k to 12.8k tokens a compaction on `suncatcher-2`, and from 15.3k to 9.9k on `eval-tip-1`, and `suncatcher-2` compacts 16 times (`measure.json`). The recorded baseline for refused edits: within 10 calls after a compaction, 6 of 43 edits were `edit.closest` on `suncatcher-2` and 1 of 8 on `eval-tip-1`, against 1 of 38 and 0 of 15 elsewhere. The re-run evals are held to it.
- **A compaction's note can't become a file's text** (mqucifer/print-shop#47). On `eval-tip-1` at call 109 the model wrote a file whose whole text was a note, as earlier `write` calls looked after compaction. A `write` or `edit` with a line that is a note in `content`, `old` or `new` is refused (`compaction_note`), saying the file is as it was and `read` gives it. A refusal over moving the note out of the call: a compacted call stays a well-formed call of its tool, so it's never an example of a call without its text, and the refusal catches a note however it came, in a `write` or an `edit`.
- **Tokens are counted at 3.18 characters a token** (`session.CHARS_PER_TOKEN`), fitted on the 1,122 recorded prompts of 12 runs as `Session._chars` counts them: the median of each prompt's growth against its characters' growth, so the estimate reads as often low as high. 2.7 read the growth 21% high, and so compacted early. Whole prompts at 3.18 are within 6.2% at the median (`~/print-shop/proofs/m18-ask/fit_chars.json`). Compaction's line stays at 125k.
- **What's to be said again after a compaction** goes before the next call: `Session.after_compaction`, which mqucifer/print-shop#45 sets to the checks that still fail.
- **An `edit` on a file that changed since the model had it whole says so,** and which call changed it: the harness keeps each file as the model last had it (its `write`, a whole `read`, or an `edit` of what it had), and after each `run`, `bench`, and run after a `write` or `edit`, which of those files changed. It's held in the process, so a resumed run starts knowing none.

Limits are in `session.Limits`, recorded on the run's row. The model isn't told them, so a run says what a lower one would have done.

## Toolsets

The Sponsor, 2026-11-03: "id be concerned with adding things like light-path commands unles its just a tool option or something, not everything is going to be a stained glass print." So the core stays general, and a check for one kind of object comes in that kind's toolset: its bench commands, the measures its needs may use, and its skills (`rounds/toolsets.py`). It's the first step to a shared core with toolsets per domain.

- **The request's `## Kind`** names the kind, and a run with none, or with a kind that has no toolset of its own, has the core only: a `bench` call of another kind's command is refused, saying which toolset has it, and the `bench` tool's description, the `needs` tool's measures and the start's skills are the toolset's. The start says which toolset it is.
- **A run from before toolsets** keeps every command it had, its skills, and its needs as they were: its `state.json` has no `toolsets`, and its recorded commands are in the ledger by name.
- **Each command, sorted:**

| Command | Toolset | Why |
|---|---|---|
| `info`, `clearance`, `readback`, `rays`, `params`, `render` | core | any part has a size, fits, cuts, holes, parameters and a look |
| `thin` | core | every printed file is read by it, whatever the kind |
| `wall` | core | any part may hang from a hole, or carry a load through one |
| `pause` | core | an insert dropped in mid-print, a nut or a magnet, is any kind's. It reads a closed nut's pocket as it reads a pane's (`~/print-shop/proofs/m18-verify/pause-nut.txt`) |
| `capture`, `panes` | stained glass | they read a pane in its pocket, held by lips on both faces, which only this kind has |
| `light`, `thickness` | stained glass | what a person sees through a pane |

**The stained-glass skill**, `designing-stained-glass`, comes with its toolset, with its reference `checking-stained-glass` (mqucifer/print-shop#37). It's built to Anthropic's skill-authoring best practices, and its reader is the local model, so it says more than Claude would need: a workflow with a checklist from how the piece is built and the 2D layout through each bench check, run, fix, run again, with the request's stops in their places; each term (cell, pane, leading, pocket, play, opening, lip, and a lip before or after play) defined once; exact commands for the checks and open guidance for the design. It folds in lessons 1 (the 2D layout, the leading in one piece), 2 (heights named once), 17 (a lip per cell) and 20 (no sharp tips), in general terms. **Its evaluations came first:** `tests/skill_evals/designing-stained-glass/` has the gaps from the three runs' records (`gaps.md`), six scenarios each with a request, its files and the behaviour expected (`evals.json`), and the fixtures, made from scratch (`make_evals.py`). The operator runs them on the local model; `README.md` there says how. `tests/test_skill_rules.py` holds every skill to the rules: a name of lowercase letters, digits and hyphens, at most 64, in gerund form; a third-person description of at most 1,024 characters that says when to use it; `SKILL.md` under 500 lines; references one level deep, and any over 100 lines opening with its contents. `cadquery` and `references` are measured copies: their names aren't gerunds and their descriptions don't say when to read them, which the test lists and nobody edits. `bench`'s skill is now `using-the-bench`, and says when to use it.

**How a run finds its skills** is in `skill_reads`: each `skill` call, at which call and round, the name asked for and the file it read, or `found` 0 for a name its toolset hasn't got. `runs.toolset` has the run's kind, commands and skills.

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
| `timeline` | Each start, stop, resume, compaction and restart, in order. A compaction has its size before and after, estimated and measured, and what it cut (`said`), and each process its harness commit, where `runs.harness_commit` holds only the first. Each call that ended without a tool call is an `asked` row, with what the next request offered |
| `calls` | Each call's seconds, tokens, finish and share of repeated reasoning, with its round, its phase (how many times a person had sent something) and its process |
| `phases`, `round_costs` (views) | Calls, seconds and tokens, split by what a person sent or by round |
| `runaways` | A call that hit the limit, was cut, or whose reasoning repeats, for review. See below |
| `tries` | Each reading from a `try` |
| `approvals`, `decisions` | Each stop for approval, with its sheet's hash, the part's hashes, the closing words and the delivered folder's groups and files (`review`), and each yes, send-back or close, and who. A close of a run that never stopped for approval has no `approval` |
| `standing` | Each check the harness ran itself, whatever the needs say: `thin` on each file at `done`, and the needs' checks and `thin` at an ask, each with its file's hash. `unsure` is the bench with no answer to rely on |
| `need_swaps` | Each need changed or dropped while its check failed on the files, with the old check and the new, both readings, the model's reason, and where it was shown |
| `need_measures` | What each bench need of a version says it measures (`check.measures`): a table of its own, as `need_marks` is inserted into by position |
| `skill_reads` | Each skill a run read: at which call and round, its name, the file, and whether its toolset has it |
| `set_asides` | Each failing check the model said doesn't apply: its command and files, the failed tries, its reason, and where a person was shown it |
| `request_lines` | The request's lines, stops and kind, by id (`R1`, `N1`, `plan`, `S1`, `kind`) |
| `breakdowns` | Each breakdown the model wrote, at which call |
| `need_marks` | Each need's `covers`, `fixed`, `figure` and `no_bench`, by version: apart from `needs`, which older code inserts into by position |
| `stops` (a view) | Each stop asked, from `picks.stop`: what it is, its ask and call, and the pick, when and by whom |

`picks.seen` holds each variant's files, its test pieces and its sheet, by hash, `picks.said_of_sheet_model_said` what the model said the sheet shows, in its own words, and `picks.fix_first` a fault sent back apart from the pick. `rounds` gains `part_rows`, `closing_model_said` and `judged_files`, what the clean run left by hash.

| `after_actions` | Each try at a run's after-action review: the decision it follows (`decision`, an approval or a close, and `approval` if it has one), its calls, the commit, and how it ended (`reviewed`, `not_begun`, or the ending that stopped it) |
| `nudges` | Each bench check that kept failing and the line its reply gained: its command, its files, the failures in a row and how many it takes |
| `lessons` | Each lesson: the run it came from, who wrote it (`model`, or a person, signed `reviewer: <who>` or as at a pick), the fix's shape in its writer's words, its kind (its area), its text, its evidence (calls and readings, by the ledger's keys), its status, what a lesson for the harness or the request `becomes`, what it `became` once promoted, and where, and the kind of part it's for (`part_kind`) |
| `lesson_routes` | Each lesson's routes, who set each, where it was delivered and when, and when its text stopped being given, and why |
| `lesson_keys` | The checks each lesson is about, by command and verdict |
| `refusals` | Each tool call not carried out as asked: its call, tool, outcome and reason's code, from the events |
| `said_weighed` | What the after-action review made of each thing a person said: the lesson it became, if any, and why |
| `lesson_steps` | Each change to a lesson, who made it and why. An edit keeps the words it replaced |
| `run_lessons` | Each lesson a run was given, how (`start`, or by hand in the request before this), and the words it was given in |
| `refinements`, `refine_files`, `refine_slices`, `refine_findings` | Each refinement pass: the approval and decision it follows, the commit, Orca's version and the context. Each file by its hash, its part and colour, and how it was meshed against OCCT. Each plate Orca sliced, its command, filaments, pauses and G-code's hash. Each finding: its kind, whose (`orca`, `bench` or `pass`), where, what it read and means, and whether it needs the part to change |
| `refine_proposals`, `proposal_decisions` | Each change proposed, its cost, the parameter and how it was checked, and each yes or no to it, and who |
| `lesson_scores` | Whether a lesson held on a route in a run (`used`, `not_used`, `in_the_way`), with the calls that show it: on the model's route by the model at its review or a reviewer, on the others by the analysis (`route`, empty from before) |

Looking a lesson up when a reading fails isn't here yet.

**Written from the events.** `records.py` takes a run's events one at a time, live or from the file, and a second reading adds nothing, so a table doesn't depend on when it was read, and a process that was killed catches up when it's next loaded. `read-back` does it for a run that ended, and only reads its folder. A run from before these tables reads back with less: a note's sender is only "the people running this", its time is its first call's, and who made a pick comes from `picks.picked_by`, never from the message's words, since a stand-in's pick was once told to the model as the Sponsor's. Nothing is backfilled where it wasn't recorded: `fix_shape`, closing words, chunk times, lost arguments, and a need's `source`.

**The ledger only gains.** Tables and columns are added on open, and existing rows stay. An older checkout may still be running against it, so nothing is added to a table it inserts into by position (`needs`, `readings`). `decisions` and `after_actions` were made again once (mqucifer/print-shop#32), as a close needs `approval` to allow none and SQLite can't drop a NOT NULL in place: in one transaction, every row copied, and each earlier review names the approval decision it followed. Older code inserts into both by name, and a review it writes has its `decision` filled when the ledger is next opened.

### Runaways are evidence

The Sponsor: "When we get these run-away calls or repeating itself constantly its almost ALWAYS has been some issue with its context or tool or skill not working well. So to me these are not throwaways - its the obvious spot to look for fixes first." **Nothing here cuts a call or limits thinking.** Crew's whitespace cut is still the only cut.

- **A tool call's arguments are kept as they arrive,** in `streams/n.jsonl`, on the disk before the next piece comes. It stays if they didn't all come whole. `calls.stream` has the chunks' times, the longest gap and how far the arguments got.
- **It can't keep what never arrives.** On one real run the arguments of a call cut off at the limit never arrived, and the likely cause is the server holding them back until they're complete, which hasn't been probed. If so, `tokens_unaccounted` on the row, and `tool_args_chars` of 0, are what show it.
- **A row in `runaways`** is a call with a `finish` of `length` or `runaway`, or whose reasoning repeats earlier text past `Limits.review_repeat_share` (0.5, recorded on the run). Repetition counts sentences of 25 characters or more that match an earlier one word for word, by characters, over calls with at least 2,000 characters of such sentences. Paraphrase isn't counted, so it's a floor.
- **`doing`** is what it was calling, with any path, and how its text and reasoning ended. **`lacked`** is the evidence code can give: the skills read and not read, the compactions so far, the last bench answer, the last refusals and the needs failing. What it really lacked is a reviewer's call, which `review_note` is for.

## Lessons

The first of the learn loop in code (mqucifer/print-shop#28, M18's "learning logic", ADR 0010's decision 5 as changed on 2026-11-02). Before this, the operator wrote lessons into a run's analysis and copied them into the next run's request by hand. On `cup-1` four were given that way. The two about how to build were followed. The one about checking held until a check got in the way, and then the check was changed.

**The model writes its run's lessons, after the approval or a close, while it still has its context.** `resume --approve` records the decision and ends the run as `approved`, then makes one more turn on the same session. `after_action.py` holds it, apart from `review.py`, which writes the folder a person prints from: the two never share a name, in code, in what the model's told, or in the ledger.
- The model is told the part is approved, with the person's note, and asked for its after-action review. The message lists the lessons it was given at the start, and the run call by call from the ledger: the tools each call made, each bench answer and its verdict, and each round's readings where the round ended. A lesson names its calls from that.
- **It sees the harness's record** (mqucifer/print-shop#40). On `suncatcher-2` the review missed the harness's part: replies cut to `exit 1`, the run hitting `context_full`, and that `blocked` was never weighed. So the index shows, at their calls, each compaction and what it cut (replies shrunk by tool, turns' reasoning dropped, texts made a note), each new conversation after `context_full`, and each refusal and failed edit (`refusals`), nudge and runaway. It's told that where the harness hid something, or a stop wasn't made, that's a lesson for the harness. On `suncatcher-2`'s record, read back through a copy of the ledger, it shows 24 compactions, the restart, both `context_full` stops, 7 failed edits, 6 other refusals and 14 runaways (`~/print-shop/proofs/m18-loop/index-suncatcher-2.json`).
- **It sees what people said, in their words:** each note, answer, pick with its reason, fix-first, send-back and the decision itself, marked `[said N]`. Before, the index named only each one's kind and sender. `people` weighs each one: whether it should be a lesson, which of the review's lessons it became, and why. Every one is weighed, or nothing is kept, and each is a row of `said_weighed`.
- **Near its limit, it goes on from the record.** `suncatcher-2`'s review ran on a conversation compacted many times. If the review's won't fit even compacted, it goes on in a new conversation from the record, as a run does, with the decision and the ask after it: the index is from the ledger, so it holds the whole run. The restart is in the timeline and `restarts`, and the review is recorded as ever. Nothing cuts a call, and compaction's limit is as it was.
- Only `read` and `after_action` work in it: the files stay as approved.
- `after_action` takes `lessons`, each with its fix's shape, its kind (its area, below), the routes it proposes, its text, its calls and any readings and keys; `scores`, one for each lesson it was given: `used`, `not_used` or `in_the_way`, with the calls that show it; and `people`. Every call must be one the ledger has of this run, every reading one it read, every lesson given scored once, and everything a person said weighed once, or nothing is kept and it's told what's wrong. Each lesson it writes is a `candidate`.
- **The run stays approved, whatever happens.** A review that can't begin, with no key or no LiteLLM, is a row of `after_actions` with `not_begun`, and so is one that ends any other way. `rounds.py after-action NAME` tries again, and a reviewer can enter lessons by hand. Its calls have a cap of their own (`Limits.after_action_calls`, 30), only so a review that never calls `after_action` ends by name. Nothing cuts a call or limits thinking. The timeline has the review as its own segment, and the approval and a close are interventions of their own kinds, `approved` and `closed`.

### A run that ends without approval

A run that's `blocked`, stopped, cut off by a limit, or judged not to be going anywhere has the most to teach (mqucifer/print-shop#32). The Sponsor, 2026-11-03: "I do wonder if we've set it up where a failure is a valid place to stop. It still has learnings."

- **A person closes it:** `resume NAME --close --by WHO --note FILE`, signed `sponsor` or `stand-in: <who>`, with a note. It works on any run that isn't approved, closed or running: after any ending, or waiting at `questions`, `ask` or `met`. It's refused while a session runs under the root, by the same lock, so a run's process has to be gone first: `stop` it and wait for it to end. A close isn't an approval. It's a row of `decisions` of its own, `closed`, with its stop for approval if it was waiting at one, and the run ends `closed`, with what it had ended as in `was`. Nothing goes on from it.
- **Then the after-action review, as after an approval,** on the same session with only `read` and `after_action`. The model is told who closed it without approving it, how it had ended (the ending's name and what it said, or the stop it waited at) and their note. It isn't told the part is approved. It's asked what didn't work and why, what would have changed it (a tool's reply, a check, a lesson, the request or a stage), and what it would do first next time, and it scores the lessons it was given. A review that fails or can't begin is a row of `after_actions`, the run stays closed, and `after-action NAME` tries again. The decision reaches the model at the first try that begins. The review has its own cap on calls and none on time, so a run cut off by `model_time` is reviewed too. Its lessons are `candidate`s, as ever.
- **A check that keeps failing is nudged.** When a `bench` command fails 4 times on the same files, whatever its other arguments, without passing as a need checks it, its reply gains the nudge. It's said again at each try after that, until the check passes as a need checks it.
  - **What it says:** one line for each try since that pass, giving its call, the arguments that differ from the first try, and how the bench's verdict and its reason began. Then: show what you have with `ask` and ask for what you need, or, if it can't be done, end with `blocked` and a clear account, as a failed, complete run is a valid end and its lessons are asked for after. It's the newest bench reply, so compaction keeps it whole.
  - **What counts:** exit 3 (the command was wrong) counts neither way, and any exit but 0 doesn't pass. A pass starts the count again only if its arguments are a settled need's, or if no need runs that check. `suncatcher-2` passed `capture` at `--min-lip 0.25` after failing at 1.2, 0.35, 0.45 and 0.3, and its need runs it at 1.2, so counting every pass would have let the bending the nudge is for end it.
  - **A need's check with other arguments says so,** pass or fail: which need, each argument that differs (`--min-lip 0.25`, the need says `1.2`), and that `done` checks the need's command.
  - It's counted from `bench_calls`, against the needs as they stand, so a run resumed on this code counts what it did before. Each nudge is a row of `nudges`, with the calls it named. It's lines in a reply, never a limit.
  - **The 4** is from the five runs' records: of 41 runs of failures of one check in a row, 35 were 3 or fewer, and the 6 of 4 or more include both of `suncatcher-2`'s loops (`~/print-shop/proofs/m18-close/streaks.json`, and `streaks_needs.json` as counted here). Counted by the whole command, the `capture` loop is five failures of one each.
- The system prompt says the same where it offers `blocked`, and so do the refusals of `questions` and `needs` once the needs are settled. In five runs before this, the model had never called `blocked`.

**A reviewer checks each against the ledger.** For now that's the operator, who signs `--by "reviewer: <who>"`: checking lessons is the reviewer's own job, not standing in for the Sponsor. `sponsor` and `stand-in: <who>` are taken too. Picks and decisions don't take `reviewer:`.
- `lessons RUN` lists what the run wrote, each with what the ledger holds at each call and reading it names, then what it was given and how each was scored. `lessons` alone lists every lesson.
- `accept` (`--routes R,R`, `--becomes B`), `edit` (`--text FILE`, `--kind`, `--shape`, `--routes`, `--becomes`, `--keys capture:FAIL,thin`, `--part-kind K`) or `reject` (`--why`), each a row of `lesson_steps`.
- **A lesson for the harness is never given as a note.** It's accepted only with what it `--becomes`: `refusal`, `bench_check`, `toolset_check`, `stop` or `harness`. One for the request alone becomes `request`.
- `lessons load FILE --by "reviewer: <who>"` takes a reviewer's steps from a file, in order, by the same rules: `{"steps": [{"do": "write", ...}, {"do": "accept", ...}, ...]}`, as `lessons.load` says. A step names a lesson by its key in the file, or by its number if it's in the ledger. It's all checked before anything is written, and refused whole if a lesson is already there. It's how lessons are entered by hand, and how the operator enters the first rows.

### Areas, routes and layers (mqucifer/print-shop#40)

**A lesson's kind is its area, one of the five** the batch was cut by with the Sponsor on 2026-11-03: `job` (understanding the job), `verify` (verification), `process` (process control), `memory` (memory within a run) and `loop` (the learning loop). The area says what goes wrong. "The learning loop is much like model training, and rounds are its data" (the Sponsor).

**Its routes say who acts on it,** one or more, each with its own delivery and score. The model proposes them, and the reviewer confirms or changes them (`lesson_routes`).

| Route | Delivered as | Scored by |
|---|---|---|
| `model` | text the next run is given: general lessons at the start, a kind's lessons in its skill | that run's own review, as before |
| `harness` | an issue in the next batch, `promote`d when merged | the next analysis |
| `request` | a line in the request template the operator writes from | the analysis |
| `operator` | a line in the checklist below, for the operator and stand-in | the analysis |

- **Delivered:** `promote ID --as B --where W` records it on its route: `note` or `skill` for the model, `request`, `checklist` for the operator, or what it became in the harness. The listing says what's waiting to reach each route.
- **Scored:** `lessons score RUN ID --route R`, once it's delivered there. A lesson's status follows each run's latest score on each route since its latest delivery: the newest that says anything, `used` as `held` and `in_the_way` as `broken`. A correction replaces that run's earlier score, so a lesson whose only score was corrected to `not_used` is back to `given`, or `promoted`.
- **A lesson can move route.** Once it's built into the harness or folded into a skill, and a run's score after that shows it holding, its text stops being given at the start, with a step saying why.

**Lessons for the model come in layers, not all at once.** "We can't just dump everything in context" (the Sponsor, 2026-11-03).
- `start` gives each kept lesson for the model that's general, or for the request's kind of part (`lessons.part_kind`, empty for a general one).
- One promoted `--as skill` lives in that kind's skill, and isn't given at the start.
- **Each is keyed to the checks it's about,** by command and verdict (`lesson_keys`): the failing bench answers and readings its evidence names, or the keys the model or the reviewer gives. They're there for a look-up at the failure, which is the next batch (the Sponsor, 2026-11-03).

**Lessons from before the five are sorted once** when a ledger opens (`lessons.migrate`), each with a step keeping its old kind: build lessons to the model, check lessons to the harness, lesson 10 to the request and the operator, lesson 13 to the operator and the model, and 14 and 19 to the model, as settled with the Sponsor. Each one's area is the builder's sort, for the reviewer to check with `edit --kind`. Then, once, each lesson's status follows its scores, each change a step: lesson 7, corrected from `in_the_way` to `not_used`, reads `given`. `lesson_scores` is made again, every row kept, so a score on a route that isn't the model's needn't follow a lesson given as text. On a copy of the real ledger every row is kept and reads back: 26 routes, 12 keys and 25 steps are added, and a second open changes nothing (`~/print-shop/proofs/m18-loop/migration-ledger-copy.json`, `read-back-ledger-copy.json`). A lesson of an old kind written later by older code is sorted when the ledger next opens.

### The reviewer's and the stand-in's checks

For the operator, at each review and each ask (mqucifer/print-shop#40). It's the checklist the `operator` route delivers to.

- **Check each candidate lesson against the run's analysis and against each person's note:** a finding in either that no lesson holds is a lesson missed.
- **Write a lesson that's missing by hand,** with `lessons load`.
- **Read a bench verdict before its picture.** On `cup-1` the stand-in gave a false cause, read from a picture whose check said FAIL.
- **Give the reference pictures at the start,** with `start --reference`, not after the first round. On `stainedglass-1` the Sponsor's intent arrived late, after the plan had been written without it. The other half of that lesson, reading the plan at the first write, is now the plan stop (mqucifer/print-shop#38).

**The next run starts with what was kept for the model.** `start` puts each kept lesson for the model (`accepted`, `edited`, `given`, `held`, `broken` or `promoted`, its text still given, as above) into the first message, after the request and its files, under `## Lessons from earlier runs`, each with its number, its area, the run it came from and who wrote it. `lessons.md` in the run's folder holds them as given, so a conversation started again from the record has the same, and `run_lessons` has a row for each. A lesson for the harness, the request or the operator alone isn't given.

**Scoring and retiring.** Each run's latest score moves a lesson, as above. A reviewer can score again (`lessons score RUN ID`), and the latest counts. A lesson given twice and not used, or that got in the way, is listed for the reviewer under "To retire", and `retire ID --why` takes it out. Nothing retires one by code.

**Its statuses:** `candidate`, then `accepted`, `edited` or `rejected`, then `given`, then `held` or `broken`, then `promoted` or `retired`.

**The first rows** are the lessons of `stainedglass-1`'s and `cup-1`'s analyses, written by the operator, with `cup-1`'s scores. Their words come from the Sponsor's runs, so they're a file off this repository, loaded with `lessons load` after this merges.

**Not yet:** looking a lesson up when a reading fails (the next batch, by the keys above), runs with and without a lesson to measure its gain, promotion into skills by code, and the crew's lessons (M16).

## The refinement pass

After approval, the approved folder is sliced through Orca, and what Orca made of it becomes findings in the ledger (mqucifer/print-shop#51, M18's step between approval and the learn loop). It runs after approval only, never inside design rounds, and never touches the printer. No model is called.

```
scripts/rounds.py refine NAME --nozzle MM --filament [COLOUR=]NAME... [--machine M] [--process P]
scripts/rounds.py refined NAME
scripts/rounds.py proposal NAME ID --approve|--reject --by WHO [--note FILE]
```

The Sponsor, 2026-11-05: "It should use orca's cli to add pauses not raw gcode. Use what we can of orca at any point. Same with color setting by object." And: "The goal of it is feedback that we can learn from. Part of that feedback could be part changes that need approval first." Printability is a second pass on an approved file, never a reason to fail a design.

**What Orca's CLI does** (2.4.2 on the Mac, `~/print-shop/proofs/m18-refine/cli-probe/`, each tested):
- **A pause:** `--load-custom-gcodes` takes Orca's own `CustomGCode::Info` as JSON, a `PausePrint` at a `print_z`. Orca writes `;PAUSE_PRINT` and the machine profile's pause G-code (`M600` on the Centauri). It puts an item on the nearest layer, a tie going to the lower, and runs it before that layer prints. So "pause after layer 13" on 0.2 mm layers is `print_z` 2.8. One file applies to one plate.
- **A filament and colour by object:** `--load-filaments` and `--load-filament-ids`, one id per file. Each filament needs `filament_colour`. The store's composed profiles have none, and with two filaments and none, Orca crashes (SIGSEGV), so the pass sets it from the file's colour.
- **Placement:** arrange turns parts unless `--allow-rotations=0` (its CLI starts it on; `--allow-rotations 0` exits 254). `EXCLUDE_OBJECT_DEFINE` says where each object went.
- **Warnings:** only in the log on macOS (`result.json` is written on Linux only), read with `--debug 3`.
- **STEP:** refused (exit 250). So each STEP file is meshed in the bench's image at Orca's own GUI defaults, 0.003 mm and 0.5 rad, and the mesh's volume and box are held to OCCT's. The operator's call on the issue (option A); the meshing is one seam.

**What it does,** for each folder of the approved result:
1. **Plates.** The largest file is the frame, on a plate of its own with its pause. A file inside the frame's box, above its underside, is a pane, set in at the pause. The panes and any other files print on a plate of their colour, as the printer has one filament loaded at a time. Each plate has its own filaments, from the store, on the Sponsor's textured PEI plate.
2. **The pause.** The bench's `pause` on the frame, at the process's own layers, with each pane as it sits (its mid-height and thickness). With no pane file, the inputs come from the run's own last `pause` call. Orca is given the layer it reads, and writes the pause itself.
3. **What Orca says,** first: its warnings; where it wrote the pause, against the bench's layer; each pane's first layer over it after the pause, with Orca's labels (on our frames, `Bridge` and `Overhang wall`: Orca doesn't know a pane is there) and how far it's laid above the pane's top; anything printed inside a pane's outline above its floor before the pause; bridges and overhangs; gap fill and the narrowest line; time and filament for each plate.
4. **The pass's own reading,** last: each layer's section of the part against every line Orca laid there, in the image. A stretch of the part with no line, at the part's edge, is `bare`: on the probe a 0.5 mm bar got no line on layer 1. Two wall lines laid over each other, more than 0.15 mm across, are `walls_overlap`: the same bar got one 0.42 mm wall down each side from layer 2, 0.34 mm over each other.
5. **Findings** go in the ledger, each with what it read, where (file, plate, layer and z), what it means for the print, and whose reading it is. Where none needs the part to change, the print steps are the pass's own: written into `refinements/n/README.md`, and into the run's `review/README.md` above the model's words, which stay marked as the model's.
6. **A proposal,** where a finding needs the part to change. Today that's a pane standing above the last layer before its pocket closes: raise the ceiling to the first layer line over the pane, at the cost of the top lip. Where the design's script has the ceiling's z or the pocket's depth as a top-level number, it's checked: `params --keep` re-runs the script with it in the bench's container, the run's bench needs are read on what it made, then `pause`, then Orca again. Otherwise it's words with the measured reason. Either way it waits: `proposal ... --approve|--reject`, signed as any decision. Nothing in the approved folder changes.

**Checked against the record.** On copies of the approved runs from 2026-11-04 and 2026-11-05, the bench and Orca both pause after the layer the record holds: `eval-hanging-2` after 13 and `eval-rings-2` after 11, as `scores.md` has them, and `eval-light-3` after 14, as its own last `pause` read (call 47 in its ledger; its folder has no pane file, so that call gives the inputs). On `eval-hanging-2` Orca leaves layer 14 open, where `pause` closes it (the pocket's ceiling is on that layer's mid-height), so its first layer over the pane is 0.3 mm above it, not 0.1: said, with both readings. `eval-rims-2` and `eval-thick-2` were never approved, and the pass refuses them.

**Not here yet:** supports and orientation (mqucifer/print-shop#9), the model proposing settings (mqucifer/print-shop#13), and the model at all: its place comes once there are findings to learn from. A proposal approved isn't applied by code: it goes to the design, as a new run or a send-back.

## A run's folder

| Path | Holds |
|---|---|
| `request.md` | As it came |
| `work/` | `/work` in each container, and the only thing model-written code can touch. `references/` is what was given, `record/` what the harness keeps for the model to read (`ROUNDS.md`, `CHECKS.md` and each round's diff), and nothing is ever read back from it |
| `events.jsonl` | Every call, with its reasoning, and every tool call. `transcript.rebuild(events, n)` gives call `n`'s exact request, across stops and compaction. It's what could later train an adapter (M14) |
| `views/` | Each picture the model was shown |
| `state.json`, `needs.json`, `needs/` | Where it stands, and the needs |
| `questions/n.md`, `n.answers.md` | Each stop at intake |
| `breakdown.md`, `breakdowns/n.json` | The latest breakdown, and each one |
| `asks/n/` | `question.md`, `sheet.png` if it drew variants, `variants/`, `test-pieces/` and `pictures/` if it showed any, `asked.json` with the stop, the plan, the looks, the model's words on the sheet and the standing checks, and `pick.json` |
| `rounds/nnn/` | `judged/` (what the clean run left), `readings/` (each need's answer, in text and numbers), `standing/` and `standing.json` (`thin` on each file, and which files take the same space), `run.txt`, `judgement.json`, `diff.patch` |
| `approvals/n/` | `sheet.png`, `stop.md`, `closing.md` and `review/`, for the person who decides |
| `review/` | The latest stop's folder to print from: one folder a group, and its README |
| `streams/n.jsonl` | A call's tool-call arguments as they arrived, if they didn't all come whole |
| `lessons.md` | The lessons it was given at the start, as given |
| `after-action-fault-n.txt` | A fault of ours in the after-action review, its trace |
| `final-sheet.png` | The latest `part` to pass, one row each, for the people who see the result. It decides nothing |
| `refinements/n/` | Each refinement pass: `README.md` (print steps, findings, proposals), `plates/` (each plate's G-code and Orca's log), `meshing/`, `bench/`, `cover/`, `findings.json`, and `proposals/` with each check's files |
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
- **Compaction lost the script once.** Each file's latest full text stays whole, and only a whole `read` counts. Here the latest `bench` and `done` replies stay too, what the Sponsor said is never touched, and a shrunk reply keeps what failed and why.
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

`tests/test_intake.py` needs no Docker: the request's form, a bracket's and a lamp's, and one in the old form, the breakdown before the needs, every line covered or the lines left over named, a fixed need's check at other settings from the first try and its change only by the Sponsor, and the stops, the plan first with no part file, `done` and the next stop held, `none` leaving one open, and the record. The in-image tests' requests say `no plan`, as their runs aren't about stops, and each settles its needs after a breakdown.

`tests/test_close.py` needs no Docker: a close after each kind of ending, what the model's told and asked, the refusals, the lock on the command line, the nudge counted by command and file and from a resumed run's earlier calls, and a ledger from before made again with every row kept. `tests/test_lessons.py` needs no Docker: the after-action review on a run put at its stop, each refusal, the reviewer's steps, giving at the start, scoring and retiring, a file of steps, and the command line with no key. `tests/test_lessons_in_image.py`, under `bench`, goes the whole way in real containers: a run met by the harness's checks, approved, its review, the reviewer's step by the command line, and the next run starting with the lesson, using it and scoring it.

`tests/test_holding.py` needs no Docker: a failed check named in every later reply until it passes at its settings or as its need checks it, a pass at the first settings clearing probes, `set_aside` and its refusals, the harness's readings counting, a check leaving the part, the list after a compaction, `done` refusing a need off the part, the question naming each, and a `none` at the plan reopening the needs. `tests/test_reports.py` reads each command's numbers from the bench's code.

`tests/test_memory.py` needs no Docker: what a shrunk reply keeps, the latest replies staying whole, `record/CHECKS.md` and a new conversation given it, and each way a refused `edit` says how its lines differ, or that its file changed and which call changed it. `tests/test_loop.py` needs no Docker: routes proposed and confirmed, keys, layers by kind of part and skill, delivery and a score on each route, a corrected score, a lesson's text stopping once its fix holds, a ledger from before sorted once with every row kept, the review's index of the harness's record and people's words, the review near its limit, and the effort read from a fake of LiteLLM's documented `/model/info`.

`tests/test_refine.py` needs no Docker: Orca's G-code and log read as Orca 2.4.2 writes them, the pause given to Orca, a colour by filament, the ceiling's arithmetic, the approved folder held to its hashes, and a pass with fakes of the bench and Orca: steps, a tie, a pane above the pause and its proposal, a plate refused, and a decision signed once. `tests/test_refine_bench.py` (`-m bench`) meshes a STEP against OCCT and reads made-up lines against a part, and `params --keep`. `tests/test_refine_orca.py` (`-m slicer`) runs the issue's four fixtures through the real Orca and image.

`tests/test_rounds_cup_local.py` (`-m bench_local`) reads `cup-1` back where the Mac holds it, and skips elsewhere: its request, references, needs and each round's scripts, with every name read from the run's own folder, so nothing of it is in this repository. `needs` refuses the version that passed on FAIL, round 1 doesn't stop for approval as `thin` fails two panes, and round 2's `review/` holds the same files, by what they hold, under the same names as the folder made by hand. Its run is kept in `~/print-shop/proofs/m18-truth/`, with `figures.json`.
