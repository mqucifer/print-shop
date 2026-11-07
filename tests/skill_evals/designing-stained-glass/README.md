# Evaluating `designing-stained-glass`

The skill's evaluations, written before its text, as Anthropic's skill-authoring best practices ask. Its reader is the local model the rounds run on, not Claude, so this is where it's measured. Nothing here calls a model: the operator runs them, after the merge.

- `gaps.md`: what the model did without the skill, from the three stained-glass runs' records. The baseline.
- `evals.json`: six scenarios, each a request in the request's form, the files it's given, the gaps it tests, the baseline, and the behaviour expected.
- `make_evals.py`: every file the scenarios give, made from scratch, with right answers from arithmetic.

## Running one

From a checkout nothing edits until the runs end, as for any run (`docs/rounds.md`):

```
mkdir -p ~/print-shop/skill-evals/files && cd ~/print-shop/skill-evals/files
cp <checkout>/tests/skill_evals/designing-stained-glass/make_evals.py .
<checkout>/scripts/bench.py python make_evals.py
```

Then for each scenario, write its `request` to a file and start a run with its `files` as references:

```
python3 -c "import json,sys; e={x['id']: x for x in json.load(open('<checkout>/tests/skill_evals/designing-stained-glass/evals.json'))['evals']}['light-through-every-pane']; open('light.md','w').write(e['request'])"
<checkout>/scripts/rounds.py start eval-light-1 --request light.md \
  --reference light/frame.step --reference light/pane_a.step \
  --reference light/pane_b.step --reference light/pane_c.step
```

A stand-in answers its stops as for any run. Each scenario's request is in the request's set form (mqucifer/print-shop#36) and names `## Kind` `stained-glass`, so the run has the stained-glass toolset, its commands and this skill.

## Scoring

Each expected behaviour is read from the run's record, not from what the model says it did:
- the skills it read, at which call: `SELECT at_call, name, file FROM skill_reads WHERE run=?`;
- its needs, with what each measures: `needs.json`;
- each bench call's command and verdict: `SELECT at_call, argv, verdict FROM bench_calls WHERE run=?`;
- what it said: `events.jsonl`, by call.

Score each behaviour held or not, with the call that shows it, and compare with the scenario's `baseline`: the three runs had every bench command and no stained-glass skill, so `gaps.md` is the baseline on this model. A scenario run once is one reading, so run each at least twice before changing the skill.

## A score for each round

A scenario with a `right` answer (`rim-by-share` has one) is scored round by round against it (mqucifer/print-shop#67, `docs/rounds.md`). Write it to a file and start the run with it, so each round is scored as it ends:

```
python3 -c "import json; e={x['id']: x for x in json.load(open('<checkout>/tests/skill_evals/designing-stained-glass/evals.json'))['evals']}['rim-by-share']; json.dump(e['right'], open('rims.right.json','w'))"
<checkout>/scripts/rounds.py start eval-rims-1 --request rims.md --right rims.right.json \
  --reference rims/frame.step --reference rims/pane_big.step --reference rims/pane_small.step
```

`right.json` stays beside the run's records, outside `work/`, so the model never reads it. A run started without one is scored after with `scripts/rounds.py scores NAME --right FILE`, which only reads the ledger.
