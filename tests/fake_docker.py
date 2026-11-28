"""A made-up remote host's `docker`, for the remote bench's tests (mqucifer/print-shop#373).

Run as `python tests/fake_docker.py ROOT ARGS...` in place of `docker -H ssh://HOST ARGS...`.
Its volumes are folders in ROOT, its images' IDs are `ROOT/images.json`'s, and its `/proc/meminfo`
is `ROOT/meminfo` (plenty, where that's absent). A bench command it runs is a made-up `load`
(`bench`), read from the files sent; every other command is the few the remote's helpers run:
`tar` in and out (with `du` of /work, `ROOT/du_k` where given), `/proc/meminfo` and `df`
(`ROOT/df_free` bytes where given). An image is run by its ID alone, one of `images.json`'s.
Every call is logged in `ROOT/log.jsonl`, so a test sees what was asked. `ROOT/down` makes
every call fail as an SSH that can't connect would; `ROOT/drop` makes the next bench command
fail so, and the host go down; `ROOT/ncpu` is how many cores it has, past which a pinned run
is refused as Docker refuses it; `ROOT/silent` makes a bench command say nothing;
`ROOT/slow_in` slows the files going in, by that many seconds; and `ROOT/others` lists other
processes' bench containers, a name and a memory label to a line, as `docker ps` gives them.

A `load` of an assembly file that plants `need_mb` meets its container's memory where its
`--memory` is less (mqucifer/print-shop#380): it says so as `load` does (`bench: met ...` on its
stderr) and saves a NO ANSWER; under more, it reads as any other. Its record says the memory it
ran in and what its container was told it met where it ran first (`BENCH_OFFERED`). Where it
also plants `bound_up_to_mb`, a read that meets the memory reads a glass band's lower bound
instead, in a container of at most that much: past it, as where a larger host's whole takes
longer to meet its memory and leaves the band too little time, it's NO ANSWER. `ROOT/kill`
makes the next bench command exit 137, and `ROOT/oom` makes Docker's events say the kernel
stopped it for memory, at its own clock's time; `events` answers for the containers in
`ROOT/oom` whose name starts with the one asked about, as a name filter can match, and whose
time is in the window asked, by its clock: `CLOCK_BEHIND` s behind this host's, as another host's
may be, and said by `info`.
`ROOT/in_fails` makes the helper's `tar -x` say what it holds on its stderr and exit 2.

Standard library only, and nothing of the tests' own: it runs as a program of its own.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

#: The options of `docker run` that take a value.
VALUED = {"--name", "--label", "--pull", "--network", "--cpus", "--cpuset-cpus", "--memory",
          "--memory-swap", "--pids-limit", "--tmpfs", "--cap-drop", "--cap-add", "--security-opt",
          "--log-driver", "--ulimit", "--user", "-e", "-v", "-w"}
#: How far its clock is behind this host's, in s.
CLOCK_BEHIND = 3600


def _clock() -> float:
    return time.time() - CLOCK_BEHIND


PLENTY = "MemTotal: 127000000 kB\nMemFree: 40000000 kB\nMemAvailable: 90000000 kB\n"


def _mib(size: str) -> int:
    units = {"m": 1, "g": 1024}
    return int(float(size[:-1]) * units[size[-1].lower()]) if size[-1:].isalpha() else int(
        size) // 2**20


def met(args: list[str], work: Path, memory: str | None) -> str | None:
    """The `bench: met` line a planted `load` says where its `need_mb` is past its container's
    `memory`, or None."""
    raw = json.loads((work / args[1]).read_text())
    need = raw.get("need_mb")
    if need is None or memory is None or _mib(memory) >= int(need):
        return None
    limit = _mib(memory) * 2**20
    return (f"bench: met 12.0 limit={limit} peak={limit - 2**20} need={int(need) * 2**20} "
            f"room={limit - 300 * 2**20} at meshing pass 1's whole, at a mesh of at most 5 mm")


def bench(args: list[str], work: Path, env: dict[str, str],
          memory: str | None = None) -> tuple[int, str]:
    """`load`, made up from what was sent: the skin pulled across its layers at 1.6% over its
    walls and its core, as `tests/test_settings_study.fake_loading` reads it, from the part's
    `print` in the assembly file; 2 where a file it names wasn't sent."""
    raw = json.loads((work / args[1]).read_text())
    for part in raw["parts"]:
        if not (work / part["file"]).exists():
            return 2, f"verdict: NO ANSWER: {part['file']} wasn't sent\n"
    part = next(p for p in raw["parts"] if p["name"] == "bar")
    printed = part.get("print") or {}
    for region in printed.get("regions") or ():
        if region.get("mesh") and not (work / region["mesh"]).exists():
            return 2, f"verdict: NO ANSWER: {region['mesh']} wasn't sent\n"
    walls, infill = printed.get("walls") or 1, printed.get("infill") or 0
    lifted = "--lift" in args
    across = (0.8 if lifted else 1.6) / (walls * (1 + infill / 50))
    core_across = 0.2 * 40 / (infill or 1)
    bolt = 0.3 if printed.get("regions") else 0.5
    over = across > 1.0 or core_across > 0.481
    record: dict[str, Any] = {
        "command": "load", "argv": args, "verdict": "FAIL" if over else "OK", "why": "made up",
        "numbers": {
            "parts": {"bar": {
                "printed": True, "across_layers_pct": across, "across_at": [70, 10, 0],
                "across_limit_pct": 1.0, "stretch_pct": 1.0, "stretch_is": "pull",
                "stretch_at": [75, 10, 10], "stretch_from_layers_deg": 4.0,
                "stretch_limit_pct": 5.1,
                "cores": [{"cell": "the part", "across_layers_pct": core_across,
                           "across_limit_pct": 0.481, "stretch_pct": 0.4,
                           "stretch_limit_pct": 1.51, "stretch_from_lines_deg": 45.0,
                           "unchecked": infill < 20, "across_at": [40, 10, 5]}],
            }},
            "joints": {"bar+post": {"nearest_share": bolt, "gives_first": "the bolt"}},
            "moves_mm": 3.0 / walls,
        },
        "notes": ["the infill's figures are published tests"],
        "ran": {"host": env.get("BENCH_HOST"), "image_id": env.get("BENCH_IMAGE_ID"),
                "memory_max": None if memory is None else str(_mib(memory) * 2**20),
                "offered": env.get("BENCH_OFFERED")},
    }
    if met(args, work, memory) is not None:
        bound = raw.get("bound_up_to_mb")
        if bound is not None and memory is not None and _mib(memory) <= int(bound):
            record["numbers"]["lower_bound"] = {"parts": ["bar"], "why": "made up"}
        else:
            record.update(verdict="NO ANSWER", why="nothing of the assembly is read: it met "
                          "the container's memory", numbers={})
            over = False
    out = work / "bench-out"
    out.mkdir(exist_ok=True)
    stem = f"load-{time.time_ns()}"
    (out / f"{stem}.json").write_text(json.dumps(record))
    (out / f"{stem}.txt").write_text(f"verdict: {record['verdict']}\n")
    said = f"verdict: {record['verdict']}\nsaved: bench-out/{stem}.txt and .json\n"
    return (2 if record["verdict"] == "NO ANSWER" else 1 if over else 0), said


def _log(root: Path, entry: dict[str, Any]) -> None:
    with (root / "log.jsonl").open("a") as stream:
        stream.write(json.dumps(entry) + "\n")


def _mounts(volumes: list[str], root: Path) -> dict[str, Path]:
    found = {}
    for spec in volumes:
        source, target, *_ = spec.split(":")
        found[target] = (root / "volumes" / source) if "/" not in source else Path(source)
    return found


def _run(argv: list[str], root: Path) -> int:
    options: dict[str, list[str]] = {}
    flags: set[str] = set()
    words = iter(argv)
    for word in words:
        if word in VALUED:
            options.setdefault(word, []).append(next(words))
        elif word.startswith("-"):
            flags.add(word)
        else:
            image = word
            break
    command = list(words)
    images = json.loads((root / "images.json").read_text()) if (
        root / "images.json").exists() else {}
    if image not in images.values():
        print(f"Unable to find image '{image}' locally\ndocker: Error response from daemon: "
              f"No such image: {image}", file=sys.stderr)
        return 125
    pinned = options.get("--cpuset-cpus", [""])[0]
    ncpu = root / "ncpu"
    if pinned and ncpu.exists():
        most = max(int(c) for part in pinned.split(",") for c in part.split("-"))
        if most >= int(ncpu.read_text()):
            print("docker: Error response from daemon: Requested CPUs are not available - "
                  f"requested {pinned}, available: 0-{int(ncpu.read_text()) - 1}",
                  file=sys.stderr)
            return 125
    if command[:1] == ["timeout"]:
        command = command[4:]
    labels = dict(e.split("=", 1) for e in options.get("--label", []))
    said = f"{labels.get('print-shop.bench.memory', '')} {labels.get('print-shop.bench.disk', '')}"
    running = root / "running"
    running.mkdir(exist_ok=True)
    if "-d" in flags:
        # A hold: it runs, as `sleep` does, until it's removed.
        (running / options["--name"][0]).write_text(said.strip())
        if (root / "hold_fails").exists():
            print("ssh: connection reset by peer", file=sys.stderr)
            return 255
        print("0" * 64)
        return 0
    mounts = _mounts(options.get("-v", []), root)
    for target, folder in mounts.items():
        if not folder.is_dir():
            print(f"no volume for {target}", file=sys.stderr)
            return 125
    _log(root, {"run": argv, "image": image, "command": command[:3]})
    if command[:2] == ["sh", "-c"] and command[2].startswith("cat /proc/meminfo"):
        meminfo = root / "meminfo"
        print(meminfo.read_text() if meminfo.exists() else PLENTY, end="")
        free = (root / "df_free").read_text() if (root / "df_free").exists() else "900000000000"
        print(f"Filesystem 1B-blocks Used Available Use% Mounted\noverlay 1 1 {free} 1% /")
        return 0
    if command[:2] == ["sh", "-c"] and command[2].startswith("du -sk /work"):
        du = (root / "du_k").read_text() if (root / "du_k").exists() else "12"
        print(f"{du}\t/work", file=sys.stderr)
        command = ["tar", "-c", "-C", "/work", "-f", "-", "--", *command[4:]]
    if command[:1] == ["df"]:
        print("Filesystem 1B-blocks Used Available Use% Mounted\noverlay 1 1 900000000000 1% /")
        return 0
    if command[:2] == ["tar", "-x"]:
        if (root / "in_fails").exists():
            # As tar in the helper refuses a file sent: its own words, and its exit 2.
            print((root / "in_fails").read_text(), file=sys.stderr, end="")
            return 2
        if (root / "slow_in").exists():
            time.sleep(float((root / "slow_in").read_text()))
        base = command[command.index("-C") + 1]
        got = []
        with tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read()), mode="r") as tar:
            for member in tar:
                path = f"{base}/{member.name}"
                target = next(t for t in sorted(mounts, key=len, reverse=True)
                              if path == t or path.startswith(t + "/"))
                inside = mounts[target] / path[len(target):].lstrip("/")
                got.append([member.name, member.uid, member.gid])
                if member.isdir():
                    inside.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    stream = tar.extractfile(member)
                    assert stream is not None
                    inside.parent.mkdir(parents=True, exist_ok=True)
                    inside.write_bytes(stream.read())
        _log(root, {"extracted": got})
        return 0
    if command[:2] == ["tar", "-c"]:
        folder = mounts[command[command.index("-C") + 1]]
        names = command[command.index("--") + 1:]
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode="w") as tar:
            for name in names:
                if not (folder / name).exists():
                    print(f"tar: {name}: Cannot stat", file=sys.stderr)
                    return 2
                tar.add(folder / name, arcname=name)
        sys.stdout.buffer.write(out.getvalue())
        return 0
    # A bench command: `python -I -c ENTRY args...`, its args after the entry.
    args = command[command.index("-c") + 2:]
    env = dict(e.split("=", 1) for e in options.get("-e", []))
    name = options["--name"][0]
    (running / name).write_text(said.strip())
    try:
        if (root / "drop").exists():
            (root / "drop").unlink()
            (root / "down").write_text("")
            print("ssh: connect to host spark port 22: Connection refused", file=sys.stderr)
            return 255
        delay = root / "delay"
        if delay.exists():
            until = time.monotonic() + float(delay.read_text())
            while time.monotonic() < until:
                if (root / "killed" / name).exists():
                    return 137
                time.sleep(0.05)
        memory = options.get("--memory", [None])[0]
        if (root / "kill").exists():
            (root / "kill").unlink()
            if (root / "oom").exists():
                stopped = (root / "oom_name").read_text() if (root / "oom_name").exists() else name
                with (root / "oom").open("a") as events:
                    events.write(f"{stopped.replace('NAME', name)} {_clock()}\n")
            return 137
        code, said = bench(args, mounts["/work"], env, memory)
        if not (root / "silent").exists():
            print(said, end="")
            line = met(args, mounts["/work"], memory)
            if line is not None:
                print(line, file=sys.stderr)
        return code
    finally:
        (running / name).unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    root = Path(argv[0])
    args = argv[1:]
    if root.is_dir():
        _log(root, {"argv": args})
    if (root / "down").exists():
        print("ssh: connect to host spark port 22: Connection refused", file=sys.stderr)
        return 255
    images = json.loads((root / "images.json").read_text()) if (
        root / "images.json").exists() else {}
    match args:
        case ["version", *_]:
            print("29.0.0")
            return 0
        case ["image", "inspect", "--format", _, image]:
            if image not in images:
                print(f"Error: No such image: {image}", file=sys.stderr)
                return 1
            print(images[image])
            return 0
        case ["volume", "create", *rest]:
            name = rest[-1]
            labels = dict(rest[k + 1].split("=", 1) for k, w in enumerate(rest)
                          if w == "--label")
            (root / "volumes" / name).mkdir(parents=True)
            (root / "volumes" / f"{name}.labels").write_text(json.dumps(labels))
            _log(root, {"volume": "create", "name": name, "labels": labels})
            print(name)
            return 0
        case ["volume", "rm", "-f", *names]:
            failing = (root / "rm_fails").read_text().split() if (
                root / "rm_fails").exists() else []
            for name in names:
                if name in failing:
                    print(f"Error response from daemon: remove {name}: volume is in use",
                          file=sys.stderr)
                    continue
                shutil.rmtree(root / "volumes" / name, ignore_errors=True)
                (root / "volumes" / f"{name}.labels").unlink(missing_ok=True)
                print(name)
            _log(root, {"volume": "rm", "names": names})
            return 1 if any(n in failing for n in names) else 0
        case ["volume", "ls", "--filter", _, "--format", _]:
            for path in sorted((root / "volumes").glob("*.labels")):
                labels = json.loads(path.read_text())
                if "print-shop.bench.job" in labels:
                    print(f"{path.name.removesuffix('.labels')} {labels['print-shop.bench.job']} "
                          f"{labels.get('print-shop.bench.created', '')}")
            return 0
        case ["volume", "ls", *_]:
            for folder in sorted((root / "volumes").glob("*")):
                if folder.is_dir():
                    print(folder.name)
            return 0
        case ["rm", "-f", *names]:
            for name in names:
                (root / "running" / name).unlink(missing_ok=True)
            _log(root, {"rm": names})
            return 0
        case ["kill", name]:
            (root / "killed").mkdir(exist_ok=True)
            (root / "killed" / name).write_text("")
            (root / "running" / name).unlink(missing_ok=True)
            _log(root, {"kill": name})
            return 0
        case ["ps", "-a", "--filter", _, "--format", _] | ["ps", "--filter", _, "--format", _]:
            holding = (root / "running").exists() and any(
                p.name.endswith("-hold") for p in (root / "running").iterdir())
            if (root / "count_fails").exists() and holding:
                print("ssh: connection reset by peer", file=sys.stderr)
                return 255
            others = root / "others"
            if others.exists():
                print(others.read_text(), end="")
            for running in sorted((root / "running").glob("*")) if (
                    root / "running").exists() else ():
                print(f"{running.name} {running.read_text()}")
            return 0
        case ["stats", "--no-stream", "--format", _, *names]:
            for name in names:
                if (root / "running" / name).exists():
                    print(f"{name} 1GiB / 3GiB")
            return 0
        case ["info", "--format", "{{.SystemTime}}"]:
            import datetime

            print(datetime.datetime.fromtimestamp(_clock(), datetime.UTC).strftime(
                "%Y-%m-%dT%H:%M:%S.%f123Z"))
            return 0
        case ["events", "--since", since, "--until", until, "--filter", watched, "--filter",
              "event=oom", "--format", _]:
            lines = (root / "oom").read_text().splitlines() if (root / "oom").exists() else []
            for line in lines:
                stopped, at = line.split()
                if (stopped.startswith(watched.removeprefix("container="))
                        and float(since) <= float(at) <= float(until)):
                    print(f"oom {stopped}")
            return 0
        case ["save", image]:
            sys.stdout.buffer.write(f"image:{image}".encode())
            return 0
        case ["load"]:
            image = sys.stdin.buffer.read().decode().removeprefix("image:")
            given = json.loads((root / "given.json").read_text())
            images[image] = given[image]
            (root / "images.json").write_text(json.dumps(images))
            print(f"Loaded image: {image}")
            return 0
        case ["run", *rest]:
            return _run(rest, root)
    print(f"fake docker doesn't know {args}", file=sys.stderr)
    return 64


if __name__ == "__main__":
    os.umask(0o022)
    sys.exit(main(sys.argv[1:]))
