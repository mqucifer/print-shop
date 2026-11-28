"""A made-up remote host's `docker`, for the remote bench's tests (mqucifer/print-shop#373).

Run as `python tests/fake_docker.py ROOT ARGS...` in place of `docker -H ssh://HOST ARGS...`.
Its volumes are folders in ROOT, its images' IDs are `ROOT/images.json`'s, and its `/proc/meminfo`
is `ROOT/meminfo` (plenty, where that's absent). A bench command it runs is a made-up `load`
(`bench`), read from the files sent; every other command is the few the remote's helpers run:
`tar` in and out, `cat /proc/meminfo` and `df`. Every call is logged in `ROOT/log.jsonl`, so a
test sees what was asked. `ROOT/down` makes every call fail as an SSH that can't connect would;
`ROOT/drop` makes the next bench command fail so, and the host go down.

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
VALUED = {"--name", "--network", "--cpus", "--cpuset-cpus", "--memory", "--memory-swap",
          "--pids-limit", "--tmpfs", "--cap-drop", "--cap-add", "--security-opt",
          "--log-driver", "--ulimit", "--user", "-e", "-v", "-w"}
PLENTY = "MemTotal: 127000000 kB\nMemFree: 40000000 kB\nMemAvailable: 90000000 kB\n"


def bench(args: list[str], work: Path, env: dict[str, str]) -> tuple[int, str]:
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
        "ran": {"host": env.get("BENCH_HOST"), "image_id": env.get("BENCH_IMAGE_ID")},
    }
    out = work / "bench-out"
    out.mkdir(exist_ok=True)
    stem = f"load-{time.time_ns()}"
    (out / f"{stem}.json").write_text(json.dumps(record))
    (out / f"{stem}.txt").write_text(f"verdict: {record['verdict']}\n")
    said = f"verdict: {record['verdict']}\nsaved: bench-out/{stem}.txt and .json\n"
    return (1 if over else 0), said


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
    mounts = _mounts(options.get("-v", []), root)
    for target, folder in mounts.items():
        if not folder.is_dir():
            print(f"no volume for {target}", file=sys.stderr)
            return 125
    _log(root, {"run": argv, "image": image, "command": command[:3]})
    if command[:2] == ["cat", "/proc/meminfo"]:
        meminfo = root / "meminfo"
        print(meminfo.read_text() if meminfo.exists() else PLENTY, end="")
        return 0
    if command[:1] == ["df"]:
        print("Filesystem 1B-blocks Used Available Use% Mounted\noverlay 1 1 900000000000 1% /")
        return 0
    if command[:2] == ["tar", "-x"]:
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
    running = root / "running"
    running.mkdir(exist_ok=True)
    (running / name).write_text(options["--memory"][0])
    try:
        if (root / "drop").exists():
            (root / "drop").unlink()
            (root / "down").write_text("")
            print("ssh: connect to host spark port 22: Connection refused", file=sys.stderr)
            return 255
        delay = root / "delay"
        if delay.exists():
            time.sleep(float(delay.read_text()))
        code, said = bench(args, mounts["/work"], env)
        print(said, end="")
        return code
    finally:
        (running / name).unlink()


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
        case ["volume", "create", name]:
            (root / "volumes" / name).mkdir(parents=True)
            _log(root, {"volume": "create", "name": name})
            print(name)
            return 0
        case ["volume", "rm", "-f", *names]:
            for name in names:
                shutil.rmtree(root / "volumes" / name, ignore_errors=True)
            _log(root, {"volume": "rm", "names": names})
            return 0
        case ["volume", "ls", *_]:
            for folder in sorted((root / "volumes").glob("*")):
                print(folder.name)
            return 0
        case ["rm", "-f", *names]:
            _log(root, {"rm": names})
            return 0
        case ["kill", name]:
            _log(root, {"kill": name})
            return 0
        case ["stats", "--no-stream", "--format", _, *names]:
            for name in names:
                if (root / "running" / name).exists():
                    print(f"{name} 1GiB / 3GiB")
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
