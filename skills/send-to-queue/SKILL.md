---
name: send-to-queue
description: Send files to the person's print queue on this Mac (print-shop), for them to pick up when they can. Load when asked to print, queue or send a part, an STL, or G-code sliced in Orca. Sending is all it does.
---

# Sending files to the print queue

print-shop's queue (mqucifer/print-shop#210, mqucifer/print-shop#226) holds the files the person will print. You send them there with one command, from any folder. The person picks the queue up later, often from their phone: they answer its questions, review its settings, load the spool and start each print.

**Sending is all you do.** You never answer the questions, approve, arm or start a print, and you never run `scripts/print_job.py`. The command has no option for any of them and ignores `PRINT_AGENT_ALLOW_HARDWARE`.

## The command

```
uv run --project ~/Documents/git/print-shop python ~/Documents/git/print-shop/scripts/send.py --by "Claude, in the <repo> session" --note "what these are, and the order to print them" lid.stl x2 for="the lid of the cable box: clips onto its rim" loaded=handled seen=top,walls suggest="wall_loops=4 because the clip flexes each time it's opened" box.stl spool=2 for="the cable box's base: holds a power brick" loaded="carries a real load" tiles.gcode project=tiles.3mf
```

- `--by`: who you are, as the person will see it: "Claude, in the <repository> session".
- `--note`: one line for the person: what the files are, and anything they need to know first.
- After each file, what's said of it:
  - `xN`: that many prints of it. One by default.
  - `spool=S`: the spool it's for, by its number in the stock (`uv run --project ~/Documents/git/print-shop python ~/Documents/git/print-shop/scripts/stock.py list`). The loaded one by default. Only name one the person asked for.
  - **`for="..."` and `loaded=...`: give both for every part.** `for` is what the part is for, in a few plain words. `loaded` is how it's loaded, one of: "decoration or display", "handled, clipped or lightly loaded", "carries a real load", "a prototype or test fit" (a word of one will do: `loaded=handled`). The questions read it back for the person to confirm, instead of asking, so they answer less from the phone. Said wrong, they correct it there.
  - `seen=walls,top,bottom`: the faces that will be seen, as the part lies in its file, where you know. `seen=nothing` for none.
  - `suggest="KEY=VALUE because why"`: an Orca setting you suggest, with why, as the person types a correction at the review. It's shown at the review as yours, unticked, and used only if they take it. Suggest only what this part gives a reason for. A pause isn't suggested: the person sets it at the review.
  - `project=P.3mf`: for G-code, the Orca project it was sliced from, which carries each object's overrides. Without it, a project beside the G-code whose objects match is taken, and the answer says which.
- Files: a part as an STL, or a file sliced by hand in Orca: its G-code, or the plate's sliced file (`.gcode.3mf`). An Orca project that isn't sliced is refused, saying what to export.

If a part has a sidecar (`PART.part.json`) beside it, or sits in a design run, its notes go with it: you needn't repeat them, and what you give stands in their place.

## What comes back

The command answers in words to relay to the person as they are: what was queued, what came with each file (its notes, the project, your suggestions), and what wasn't queued and why. It exits 0 when all were queued, 1 when some weren't, 2 when none was.

- The queue keeps its own copy of each file and what belongs to it, so the originals can move.
- The same bytes as a job already waiting, for the same spool, aren't queued twice: send `xN` for more prints. The same bytes as a past print are queued, and the answer says that print and how it came out: tell the person.
- A file sliced by hand waits for the person to look at what it prints before it's ready.
- Nothing is prepared until the person opens the queue (`print_job.py --queue`); if it's open already, it starts at once.

Then tell the person what was sent and anything the answer said wasn't taken. That's the end of your part.
