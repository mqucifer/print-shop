"""A hand-sliced file, printed as it is and kept in the ledger (mqucifer/print-shop#164).

The Sponsor, 2026-11-17: "a way to put a fully sliced build through that it doesn't try and
manage, but just maintains the settings and outcome/images in the ledger? Would be nice for
reprints too". A part given as an STL is laid, asked about, proposed for, sliced and reviewed.
A file the Sponsor sliced by hand in Orca (a light test with a setting on each object, a
calibration print, a known-good job) is none of that: it goes to the approval gate and the
printer as it is, and the ledger keeps what it was sliced with.

**Two files are taken, and which is said:**
- **G-code** (`.gcode`), as Orca exports it. Its settings are in its config block. It names its
  objects (`EXCLUDE_OBJECT_DEFINE`) and carries none of their per-object overrides: Orca
  writes the plate's settings to the block and nothing of an object's own.
- **Orca's sliced plate file** (`.gcode.3mf`, "Export plate sliced file"). It's a zip holding
  the same G-code (`Metadata/plate_<n>.gcode`), each object's overrides
  (`Metadata/model_settings.config`) and each object's name as the G-code has it
  (`Metadata/slice_info.config`). The G-code inside is what's sent, byte for byte; the printer
  takes G-code, never the zip.

**A G-code's overrides can come from the project it was sliced from** (`project=`, the saved
`.3mf`): Orca keeps them there in the same `model_settings.config`. They're matched to the
G-code's objects by name, and that is all that ties the two files: the record says so.

Both facts are from Orca 2.4.2 on the Mac, 2026-11-17, slicing a project with an override on
each of two objects (`~/print-shop/proofs/m19-sliced/orca-probe/`): the G-code's 22 layers
showed the 0.12 mm override was used, its config block said `layer_height = 0.2` and named
neither object's settings, and the plate file's `model_settings.config` held both.

**The bytes are what's sent.** The file is read once (`read`), its G-code's sha256 kept. When
the job starts, those bytes are copied into the job's folder under a name carrying the job id
(`take`), refused if they've changed since they were read. The copy is hashed again just before
the upload (`verify`), and a reprint sends that copy again, refused if it has changed (`again`).

**Nothing here proposes or changes anything.** `compare` lists what differs from the
inventory's tuning for the loaded filament, `report` says what the slice's own reading shows,
and `loaded_mismatch` says when the file names another filament or nozzle than is loaded.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree

from pydantic import BaseModel

from print_shop.inventory import Material
from print_shop.slicer.store import KINDS, Store, choose

if TYPE_CHECKING:
    # The ledger keeps a `SlicedRecord` and the job's state a `SlicedJob`, so both import this:
    # what reads G-code (`slicer.orca`, `slicer.naming`) imports the state, and is imported
    # where it's used.
    from print_shop.core.state import SliceResult
    from print_shop.ledger import Ledger, Row
    from print_shop.setup.reading import Flag, SliceReading

__all__ = [
    "BY_HAND",
    "NOT_SETTINGS",
    "PROFILE_IDS",
    "RATIONALE",
    "SOURCE",
    "Again",
    "Comparison",
    "Difference",
    "ObjectOverrides",
    "SlicedChanged",
    "SlicedError",
    "SlicedFile",
    "SlicedJob",
    "SlicedRecord",
    "again",
    "by_kind",
    "compare",
    "loaded_mismatch",
    "read",
    "report",
    "same_setting",
    "take",
    "verify",
]

#: How the ledger marks a job the loop chose nothing for.
BY_HAND = "by hand"
#: The ledger's `orientation_source` for one: nothing was turned.
SOURCE = "sliced"
#: What the approval gate and the ledger say of its pose.
RATIONALE = ("sliced by hand: sent as the file is, with nothing turned, proposed or sliced "
             "again")

#: The config block's keys that name the three profiles, as Orca wrote them.
PROFILE_IDS = {
    "machine": "printer_settings_id",
    "process": "print_settings_id",
    "filament": "filament_settings_id",
}

_PLATE = re.compile(r"^Metadata/plate_(\d+)\.gcode$")
_DEFINE = re.compile(rb"^EXCLUDE_OBJECT_DEFINE NAME=(\S+)", re.M)
#: What Orca adds to an object's name in the G-code: which object, and which copy of it.
_COPY = re.compile(r"_id_\d+_copy_\d+$")
_MODEL_SETTINGS = "Metadata/model_settings.config"
_SLICE_INFO = "Metadata/slice_info.config"
#: An object's or part's metadata that says what it is, never a setting overridden.
_NOT_OVERRIDES = frozenset({"name", "extruder", "matrix"})
#: A profile's keys that aren't settings: Orca writes none of them in a config block as they are.
NOT_SETTINGS = frozenset({
    "name", "inherits", "from", "version", "type", "setting_id", "filament_id", "instantiation",
    "is_custom_defined", "compatible_printers", "compatible_printers_condition",
    "compatible_prints", "compatible_prints_condition",
})
#: Settings Orca writes its own way in a config block whatever the profile says, so a slice
#: made with exactly the tuning shows them changed. None says how a part prints on a
#: one-filament printer, and none is compared. Each is from the four slices Orca 2.4.2 made
#: with the store's own profiles (`tests/data/recorded/presets/`):
#: the colour Orca draws the filament in; the thumbnail's format ("144x144" becomes
#: "144x144/PNG"); a point written with a comma ("0.5x0.5" becomes "0.5,0.5"); and the prime
#: tower, which Orca's command line turns off for one filament.
_NOT_COMPARED = frozenset({
    "extruder_colour", "filament_colour", "default_filament_colour", "thumbnails",
    "best_object_pos", "enable_prime_tower",
})
#: A value Orca renamed, read as its new name: the profiles still say the old one.
_RENAMED = {"zig-zag": "rectilinear"}
#: What the comparison says of a setting the tuning doesn't state.
ORCAS_DEFAULT = "Orca's default"
#: What a file's name ends with, dropped for the part's name on the printer.
_SUFFIXES = (".gcode.3mf", ".3mf", ".gcode", ".gco", ".g")


class SlicedError(ValueError):
    """A file that can't be taken as a sliced job, or a past job that can't print again."""


class SlicedChanged(RuntimeError):  # noqa: N818 - a refusal, said as what happened
    """The bytes aren't the ones that were read, taken or printed, so nothing is sent."""


class ObjectOverrides(BaseModel):
    """One object's own settings, over the plate's: only what was overridden."""

    model_config = {"frozen": True}

    #: The object as the G-code names it, or as the plate file does where the G-code has none.
    name: str
    settings: dict[str, str] = {}
    #: A part's or modifier's own settings inside the object, by its name.
    parts: dict[str, dict[str, str]] = {}

    def line(self) -> str:
        said = [f"{k}={v}" for k, v in sorted(self.settings.items())]
        for part, settings in sorted(self.parts.items()):
            said += [f"{part}: {k}={v}" for k, v in sorted(settings.items())]
        return f"{self.name}: " + ", ".join(said)


class Difference(BaseModel):
    """One setting the file was sliced with that isn't the tuning's value."""

    model_config = {"frozen": True}

    kind: str  # "machine", "process" or "filament"
    key: str
    tuning: str
    file: str

    def line(self) -> str:
        return f"{self.key}: {self.tuning or '-'} -> {self.file or '-'} ({self.kind})"


class Comparison(BaseModel):
    """The file's settings against the inventory's tuning for the loaded filament: the three
    profiles a print of this filament would slice with, by name, and each setting that differs."""

    model_config = {"frozen": True}

    machine: str
    process: str
    filament: str
    differs: tuple[Difference, ...] = ()


class SlicedFile(BaseModel):
    """A hand-sliced file, read. Nothing is copied or changed."""

    model_config = {"frozen": True}

    source: Path
    kind: str  # "gcode", or "plate" for Orca's sliced plate file
    plate: int | None = None
    #: Of the G-code's bytes, which are what's sent.
    sha256: str
    #: Of the plate file itself, or "" for G-code.
    container_sha256: str = ""
    #: Every setting in the config block, as Orca wrote it.
    config: dict[str, str]
    #: The objects the G-code names.
    objects: tuple[str, ...] = ()
    #: Each object's overrides. () is a file that carries them and has none; None is a file
    #: that can't carry them, with `overrides_from` saying why.
    overrides: tuple[ObjectOverrides, ...] | None = None
    overrides_from: str = ""
    #: Orca's estimate, from the G-code.
    print_time_s: int
    filament_g: float | None
    filament_cm3: float
    filament_mm: float
    layer_count: int

    @property
    def machine(self) -> str:
        return self.config.get(PROFILE_IDS["machine"], "")

    @property
    def process(self) -> str:
        return self.config.get(PROFILE_IDS["process"], "")

    @property
    def filament(self) -> str:
        return _name(self.config.get(PROFILE_IDS["filament"], ""))

    @property
    def filament_type(self) -> str:
        return _first(self.config.get("filament_type", ""))

    @property
    def nozzle_mm(self) -> float | None:
        try:
            return float(_first(self.config.get("nozzle_diameter", "")))
        except ValueError:
            return None

    def gcode_bytes(self) -> bytes:
        """The G-code as it is now at the source: the file's, or the plate file's member."""
        if self.kind == "gcode":
            return self.source.read_bytes()
        with zipfile.ZipFile(self.source) as z:
            return z.read(f"Metadata/plate_{self.plate}.gcode")

    def said(self) -> str:
        """Which file was taken, in a sentence."""
        if self.kind == "plate":
            return (f"{self.source.name} is Orca's sliced plate file: the G-code inside it "
                    f"(plate {self.plate}) is what's sent, byte for byte")
        return f"{self.source.name} is G-code: its bytes are what's sent"


class SlicedRecord(BaseModel):
    """What the ledger keeps of a hand-sliced print, beside its row."""

    model_config = {"frozen": True}

    #: Always "by hand": nothing in this print was chosen by the loop, so nothing in it is a
    #: correction of the loop, and no lesson was applied to it.
    sliced: str = BY_HAND
    source: str  # where the file was when it was sent
    kind: str
    plate: int | None = None
    gcode_sha256: str
    container_sha256: str = ""
    #: The job this one prints again, or None for a file sent the first time.
    reprint_of: str | None = None
    #: The profile names Orca wrote.
    machine: str
    process: str
    filament: str
    #: Every setting in the config block, under the profile it belongs to: "process",
    #: "filament", "machine", and "other" for what none of the store's profiles has.
    settings: dict[str, dict[str, str]]
    #: Against the inventory's tuning for the loaded filament, or None with `compared_note`
    #: saying why there was nothing to compare with.
    compared: Comparison | None = None
    compared_note: str = ""
    objects: tuple[str, ...] = ()
    overrides: tuple[ObjectOverrides, ...] | None = None
    overrides_from: str = ""
    #: Orca's estimate. The real time is the row's own, from its start to its end.
    print_time_s: int
    filament_g: float | None = None
    filament_cm3: float
    #: The file named another filament or nozzle than was loaded, and the Sponsor said to print
    #: it: what differed, or "".
    loaded_ok: str = ""

    def lines(self) -> list[str]:
        """What was kept of the file, as `--list` and the desk say it."""
        out = [f"profiles: {self.machine} / {self.process} / {self.filament}, as Orca named them"]
        if self.compared is None:
            out.append(f"not compared with a tuning: {self.compared_note or 'none was found'}")
        elif not self.compared.differs:
            out.append(f"the same as the inventory's tuning ({self.compared.process} / "
                       f"{self.compared.filament}) in every setting it has")
        else:
            out.append(f"{len(self.compared.differs)} differ from the inventory's tuning "
                       f"({self.compared.process} / {self.compared.filament}), tuning -> file:")
            out += [f"  {d.line()}" for d in self.compared.differs]
        if self.overrides is None:
            out.append("per-object overrides: not kept, " + (self.overrides_from or
                       "the file doesn't carry them"))
        elif not self.overrides:
            out.append(f"per-object overrides: none ({self.overrides_from})")
        else:
            out.append(f"per-object overrides ({self.overrides_from}):")
            out += [f"  {o.line()}" for o in self.overrides]
        if self.loaded_ok:
            out.append(f"printed with what was loaded, as you said: {self.loaded_ok}")
        return out


class SlicedJob(BaseModel):
    """A hand-sliced file on its way to the gate: what the daemon is handed, and the graph's
    state carries."""

    model_config = {"frozen": True}

    file: SlicedFile
    #: The part's name in the ledger: the file's own, and a reprint's original's.
    name: str
    material: Material | None = None
    compared: Comparison | None = None
    compared_note: str = ""
    reprint_of: str | None = None
    loaded_ok: str = ""

    @classmethod
    def of(
        cls, file: SlicedFile, *, material: Material | None = None,
        compared: Comparison | None = None, compared_note: str = "", name: str | None = None,
        reprint_of: str | None = None, overrides: tuple[ObjectOverrides, ...] | None = None,
        overrides_from: str = "", loaded_ok: str = "",
    ) -> SlicedJob:
        """The job for `file`. A reprint gives the original's `name`, and its `overrides`,
        which the kept G-code can't carry."""
        if reprint_of is not None:
            file = file.model_copy(update={"overrides": overrides,
                                           "overrides_from": overrides_from})
        return cls(file=file, name=name or file.source.name, material=material,
                   compared=compared, compared_note=compared_note, reprint_of=reprint_of,
                   loaded_ok=loaded_ok)

    def record(self, store: Store | None = None) -> SlicedRecord:
        """What the ledger keeps of it."""
        f = self.file
        return SlicedRecord(
            source=str(f.source), kind=f.kind, plate=f.plate, gcode_sha256=f.sha256,
            container_sha256=f.container_sha256, reprint_of=self.reprint_of,
            machine=f.machine, process=f.process, filament=f.filament,
            settings=by_kind(f.config, store), compared=self.compared,
            compared_note=self.compared_note, objects=f.objects, overrides=f.overrides,
            overrides_from=f.overrides_from, print_time_s=f.print_time_s,
            filament_g=f.filament_g, filament_cm3=f.filament_cm3, loaded_ok=self.loaded_ok,
        )


class Again(BaseModel):
    """A past sliced job, found to print again: its kept file, and what goes with it."""

    path: Path
    name: str
    reprint_of: str
    overrides: tuple[ObjectOverrides, ...] | None = None
    overrides_from: str = ""


# --- reading the file ---------------------------------------------------------------------------


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _first(raw: str) -> str:
    """The first of a per-filament value, unquoted: the Centauri Carbon has one extruder."""
    return re.split(r"[;,]", raw.strip())[0].strip().strip('"')


def _name(raw: str) -> str:
    """The first profile name of a block's value. A name may hold commas (the Sponsor's
    "Elegoo RAPID PETG 270Degree,MaxFanSpeed=50,…"), so only Orca's own `";"` between two
    filaments' names divides them."""
    return raw.split('";"')[0].strip().strip('"')


def read(path: Path, plate: int | None = None, project: Path | None = None) -> SlicedFile:
    """The hand-sliced file at `path`: G-code, or Orca's sliced plate file, told apart by what
    the file is and never by its name. `plate` picks one of a plate file's G-codes. `project`
    is the Orca project it was sliced from, whose per-object overrides are kept with it.

    Raises:
        SlicedError: If it isn't a file, holds no G-code, holds several and none was named, or
            its G-code has no Orca config block or totals; or the project isn't one, or none
            of its objects is in the G-code.
    """
    file = _read(path, plate)
    if project is None:
        return file
    overrides, where = _project_overrides(project.expanduser().resolve(), file.objects)
    return file.model_copy(update={"overrides": overrides, "overrides_from": where})


def _project_overrides(
    project: Path, objects: tuple[str, ...]
) -> tuple[tuple[ObjectOverrides, ...], str]:
    """Each of the G-code's objects' overrides in the project it was sliced from, matched by
    the object's name: all that ties the two files."""
    if not project.is_file() or not zipfile.is_zipfile(project):
        raise SlicedError(f"{project} isn't an Orca project (.3mf)")
    with zipfile.ZipFile(project) as z:
        found, where = _overrides(z, 0, objects)
    if found is None:
        raise SlicedError(f"{project.name} gives no object's overrides: {where}")
    kept = tuple(o for o in found if o.name in objects)
    if found and not kept:
        raise SlicedError(
            f"none of {project.name}'s objects with overrides "
            f"({', '.join(o.name for o in found)}) is in the G-code "
            f"({', '.join(objects) or 'which names none'}): is it the project this was sliced "
            "from?")
    return kept, (f"the project {project.name} (sha256 {_sha(project.read_bytes())[:12]}…), "
                  "matched to the G-code's objects by name")


def _read(path: Path, plate: int | None) -> SlicedFile:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise SlicedError(f"{path} is not a file")
    if not zipfile.is_zipfile(path):
        if plate is not None:
            raise SlicedError(f"--plate picks a plate in a sliced plate file; {path.name} is "
                              "G-code, which holds one")
        return _file(path, "gcode", None, path.read_bytes(), "", None,
                     "G-code names its objects and carries none of their overrides: export "
                     "the plate's sliced file (.gcode.3mf) from Orca, or name the project it "
                     "was sliced from with --project, to keep them")
    with zipfile.ZipFile(path) as z:
        plates = sorted(int(m.group(1)) for n in z.namelist() if (m := _PLATE.match(n)))
        if not plates:
            raise SlicedError(
                f"{path.name} holds no G-code: it's a project, not a sliced plate. Slice it in "
                "Orca, then export the plate's sliced file (.gcode.3mf) or its G-code")
        if plate is None and len(plates) > 1:
            raise SlicedError(
                f"{path.name} holds {len(plates)} sliced plates ({', '.join(map(str, plates))}): "
                "name one with --plate")
        chosen = plate if plate is not None else plates[0]
        if chosen not in plates:
            raise SlicedError(f"{path.name} has no sliced plate {chosen}: it holds "
                              f"{', '.join(map(str, plates))}")
        data = z.read(f"Metadata/plate_{chosen}.gcode")
        objects = _objects(data)
        overrides, where = _overrides(z, chosen, objects)
    return _file(path, "plate", chosen, data, _sha(path.read_bytes()), overrides, where)


def _objects(data: bytes) -> tuple[str, ...]:
    return tuple(dict.fromkeys(m.decode(errors="replace") for m in _DEFINE.findall(data)))


def _file(
    path: Path, kind: str, plate: int | None, data: bytes, container: str,
    overrides: tuple[ObjectOverrides, ...] | None, overrides_from: str,
) -> SlicedFile:
    from print_shop.slicer.orca import (  # noqa: PLC0415 - a cycle otherwise
        parse_config_block,
        parse_gcode_header,
    )

    with tempfile.TemporaryDirectory(prefix="print-shop-sliced-") as scratch:
        # The parsers read a path: a plate file's G-code is laid out for them, then removed.
        gcode = path if kind == "gcode" else Path(scratch) / "plate.gcode"
        if kind != "gcode":
            gcode.write_bytes(data)
        try:
            config = parse_config_block(gcode)
        except (RuntimeError, UnicodeDecodeError) as exc:
            raise SlicedError(
                f"{path.name} has no Orca config block (; CONFIG_BLOCK_START … END), so the "
                "settings it was sliced with can't be kept: export it from Orca") from exc
        try:
            totals = parse_gcode_header(gcode)
        except (RuntimeError, ValueError) as exc:
            raise SlicedError(f"{path.name} can't be read as Orca's G-code: {exc}") from exc
    return SlicedFile(
        source=path, kind=kind, plate=plate, sha256=_sha(data), container_sha256=container,
        config=config, objects=_objects(data), overrides=overrides,
        overrides_from=overrides_from, print_time_s=totals.print_time_s,
        filament_g=totals.filament_g, filament_cm3=totals.filament_cm3,
        filament_mm=totals.filament_mm, layer_count=totals.layer_count,
    )


def _metadata(element: ElementTree.Element) -> dict[str, str]:
    return {str(m.get("key")): str(m.get("value") or "")
            for m in element.findall("metadata") if m.get("key")}


def _set(metadata: dict[str, str]) -> dict[str, str]:
    """The settings among an object's or part's metadata."""
    return {k: v for k, v in metadata.items()
            if k not in _NOT_OVERRIDES and not k.startswith("source_")}


def _overrides(
    z: zipfile.ZipFile, plate: int, objects: tuple[str, ...]
) -> tuple[tuple[ObjectOverrides, ...] | None, str]:
    """Each object's overrides in a plate file, named as the G-code names the object, and
    where they were read from; or None and why not."""
    if _MODEL_SETTINGS not in z.namelist():
        return None, f"the plate file has no {_MODEL_SETTINGS}"
    try:
        root = ElementTree.fromstring(z.read(_MODEL_SETTINGS))
    except ElementTree.ParseError as exc:
        return None, f"the plate file's {_MODEL_SETTINGS} can't be read ({exc})"
    held: dict[str, tuple[str, dict[str, str], dict[str, dict[str, str]]]] = {}
    for obj in root.findall("object"):
        metadata = _metadata(obj)
        parts = {}
        for part in obj.findall("part"):
            of_part = _metadata(part)
            if _set(of_part):
                parts[of_part.get("name") or f"part {part.get('id')}"] = _set(of_part)
        held[str(obj.get("id"))] = (metadata.get("name", ""), _set(metadata), parts)
    if not held:
        return None, f"the plate file's {_MODEL_SETTINGS} names no object"
    names = _gcode_names(z, root, plate)
    spare = list(objects)
    found = []
    for object_id, (name, settings, parts) in held.items():
        if not settings and not parts:
            continue
        said = names.get(object_id) or [
            next((o for o in spare if _COPY.sub("", o) == name), name)]
        for gcode_name in said:
            if gcode_name in spare:
                spare.remove(gcode_name)
            found.append(ObjectOverrides(name=gcode_name, settings=settings, parts=parts))
    return tuple(found), f"the plate file's {_MODEL_SETTINGS}"


def _gcode_names(
    z: zipfile.ZipFile, settings: ElementTree.Element, plate: int
) -> dict[str, list[str]]:
    """Each object id's names in the G-code, one for each copy on the plate: the plate's
    instances give an object's `identify_id`, and `slice_info.config` the name sliced under it."""
    if _SLICE_INFO not in z.namelist():
        return {}
    try:
        info = ElementTree.fromstring(z.read(_SLICE_INFO))
    except ElementTree.ParseError:
        return {}
    sliced_as: dict[str, str] = {}
    for found in info.findall("plate"):
        if _metadata(found).get("index") == str(plate):
            sliced_as = {str(o.get("identify_id")): str(o.get("name"))
                         for o in found.findall("object")}
    names: dict[str, list[str]] = {}
    for found in settings.findall("plate"):
        if _metadata(found).get("plater_id") != str(plate):
            continue
        for instance in found.findall("model_instance"):
            metadata = _metadata(instance)
            name = sliced_as.get(metadata.get("identify_id", ""))
            if name:
                names.setdefault(metadata.get("object_id", ""), []).append(name)
    return names


# --- the settings, by profile and against the tuning ---------------------------------------------


def by_kind(config: dict[str, str], store: Store | None) -> dict[str, dict[str, str]]:
    """The config block's settings under the profile each belongs to, by which of the store's
    profiles has the key. With no store, or for a key none has, it's under "other"."""
    known: dict[str, set[str]] = {
        kind: {key for profile in store.all(kind) for key in profile.composed}
        for kind in KINDS
    } if store is not None else {}
    found: dict[str, dict[str, str]] = {"process": {}, "filament": {}, "machine": {},
                                        "other": {}}
    ids = {key: kind for kind, key in PROFILE_IDS.items()}
    for key, value in config.items():
        kind = ids.get(key) or next(
            (k for k in ("process", "filament", "machine") if key in known.get(k, ())), "other")
        found[kind][key] = value
    return found


def _items(value: Any) -> tuple[str, ...]:
    """A setting's value as its entries, a number written two ways read as one."""
    parts = [str(v) for v in value] if isinstance(value, list) else re.split(r"[;,]", str(value))
    out = []
    for part in parts:
        text = part.strip().strip('"').strip()
        try:
            number = float(text.rstrip("%"))
        except ValueError:
            out.append(_RENAMED.get(text, text))
        else:
            out.append(f"{number:g}" + ("%" if text.endswith("%") else ""))
    # An empty list and an empty value are the same nothing.
    return () if out == [""] else tuple(out)


def _text(value: Any) -> str:
    """A G-code or note setting as one string: Orca writes its line ends as `\\n` in a block."""
    text = "\n".join(str(v) for v in value) if isinstance(value, list) else str(value)
    return text.replace("\\n", "\n").replace('\\"', '"').strip().strip('"').strip()


def same_setting(key: str, tuning: Any, file: str) -> bool:
    """Whether a profile's value, or one asked for, is the value a config block has."""
    if key.endswith("_gcode") or "\n" in str(tuning) or "\\n" in file:
        return _text(tuning) == _text(file)
    return _items(tuning) == _items(file)


def _shown(value: Any) -> str:
    return ",".join(str(v) for v in value) if isinstance(value, list) else str(value)


def compare(
    config: dict[str, str], material: Material, store: Store, bed: str | None = None,
) -> Comparison:
    """The file's settings against the inventory's tuning for `material`: the profiles a print
    of the loaded spool on the fitted nozzle slices with (`store.choose`), on `bed`.

    A setting the tuning states is compared by its value, a profile's name among them. A
    setting it doesn't state is Orca's default there, and is listed only when the file itself
    says it was changed: Orca names what differs from its system presets in the block's
    `different_settings_to_system`, when it was sliced from saved presets. `_NOT_COMPARED`
    are left out.

    Raises:
        NoChoice: If the store picks no one set of profiles for the nozzle and filament.
    """
    choice = choose(store, nozzle_mm=material.nozzle_mm,
                    filament=material.tuning or material.variety)
    tuned = {"machine": choice.machine, "process": choice.process, "filament": choice.filament}
    differs = []
    for kind in ("process", "filament", "machine"):
        profile = tuned[kind]
        named = config.get(PROFILE_IDS[kind], "")
        if _name(named) != profile.name:
            differs.append(Difference(kind=kind, key=PROFILE_IDS[kind], tuning=profile.name,
                                      file=_name(named)))
        values = dict(profile.composed)
        if kind == "process" and bed:
            values["curr_bed_type"] = bed
        for key in sorted(values):
            if (key in NOT_SETTINGS or key in _NOT_COMPARED or key in PROFILE_IDS.values()
                    or key not in config):
                continue
            if not same_setting(key, values[key], config[key]):
                differs.append(Difference(kind=kind, key=key, tuning=_shown(values[key]),
                                          file=config[key]))
    stated = {key for profile in tuned.values() for key in profile.composed}
    kinds = by_kind(config, store)
    for key in _changed_from_system(config):
        if key in stated or key in _NOT_COMPARED or key not in config:
            continue
        kind = next((k for k in ("process", "filament", "machine") if key in kinds[k]),
                    "not in the tuning")
        differs.append(Difference(kind=kind, key=key, tuning=ORCAS_DEFAULT, file=config[key]))
    return Comparison(machine=choice.machine.name, process=choice.process.name,
                      filament=choice.filament.name, differs=tuple(differs))


def _changed_from_system(config: dict[str, str]) -> list[str]:
    """The settings Orca says differ from its system presets, in the order it names them: the
    block's `different_settings_to_system`, one `;`-joined list for each preset."""
    raw = config.get("different_settings_to_system", "")
    return list(dict.fromkeys(k for k in (part.strip().strip('"').strip()
                                          for part in raw.split(";")) if k))


def loaded_mismatch(file: SlicedFile, material: Material, store: Store | None) -> list[str]:
    """Where the file names another nozzle or filament than is fitted and loaded, each a
    sentence; [] when it names the same.

    The nozzle is its diameter. The filament is its type, and, where the store knows the
    file's filament profile by name, its variety too. A profile the store doesn't know is
    compared by type alone.
    """
    found = []
    where = material.printer_id
    if file.nozzle_mm is not None and abs(file.nozzle_mm - material.nozzle_mm) > 1e-6:
        found.append(f"the file is sliced for a {file.nozzle_mm:g} mm nozzle, and the fitted "
                     f"nozzle on {where} is {material.nozzle_mm:g} mm")
    loaded = (f"loaded on {where} is spool #{material.spool_id}, {material.variety} "
              f"({material.type}), {material.colour}")
    if file.filament_type and file.filament_type.casefold() != material.type.casefold():
        found.append(f"the file is sliced for {file.filament_type} ('{file.filament}'), and "
                     f"{loaded}")
        return found
    variety = next((p.variety for p in (store.all("filament") if store else ())
                    if p.name == file.filament), None)
    if variety is not None and variety.casefold() != material.variety.casefold():
        found.append(f"the file's filament profile, '{file.filament}', is {variety}, and "
                     f"{loaded}")
    return found


# --- taking it, and sending the same bytes ------------------------------------------------------


def _stem(name: str) -> str:
    lowered = name.lower()
    for suffix in _SUFFIXES:
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return name


def take(job: SlicedJob, outdir: Path, job_id: str) -> SliceResult:
    """The file's G-code copied into the job's folder under a name carrying the job id, as
    every job's is (`slicer/naming.py`): the same bytes, and the copy is what's uploaded.

    Raises:
        SlicedChanged: If the bytes aren't the ones `read` hashed.
    """
    from print_shop.slicer.naming import name_gcode  # noqa: PLC0415 - a cycle otherwise
    from print_shop.slicer.orca import parse_gcode_header  # noqa: PLC0415

    try:
        data = job.file.gcode_bytes()
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise SlicedChanged(f"{job.file.source} can't be read any more ({exc}), so nothing "
                            "was taken or sent") from exc
    now = _sha(data)
    if now != job.file.sha256:
        raise SlicedChanged(
            f"{job.file.source} has changed since it was read (sha256 {job.file.sha256[:12]}… "
            f"then, {now[:12]}… now), so nothing was taken or sent: send it again")
    outdir.mkdir(parents=True, exist_ok=True)
    taken = outdir / "taken.gcode"
    taken.write_bytes(data)
    return name_gcode(parse_gcode_header(taken), job_id=job_id, part=_stem(job.name))


def verify(gcode: Path, sha256: str) -> None:
    """Just before the upload: the taken copy still holds the bytes that were read.

    Raises:
        SlicedChanged: If it doesn't, or it's gone.
    """
    try:
        now = _sha(gcode.read_bytes())
    except OSError as exc:
        raise SlicedChanged(f"{gcode.name} can't be read ({exc}), so nothing was uploaded") \
            from exc
    if now != sha256:
        raise SlicedChanged(
            f"{gcode.name} has changed since it was taken (sha256 {sha256[:12]}… then, "
            f"{now[:12]}… now), so nothing was uploaded: send the file again")


def again(ledger: Ledger, jobs: Path, job_id: str) -> Again:
    """A past sliced job's kept file, to send again as a new job.

    Raises:
        SlicedError: If the job isn't in the ledger, wasn't hand-sliced, or its kept file is
            gone or no longer holds the bytes it printed.
    """
    row: Row | None = ledger.get(job_id)
    if row is None:
        raise SlicedError(f"job {job_id} isn't in the ledger, so there's nothing to print "
                          "again: --list shows the jobs it has")
    kept = row.sliced
    if kept is None:
        raise SlicedError(
            f"job {job_id} ({row.stl_name}) wasn't sliced by hand: it's run again from its "
            "part, with the command the print desk gives for it (scripts/print_desk.py)")
    path = jobs / job_id / row.gcode_name
    if not path.is_file():
        raise SlicedError(f"job {job_id}'s file isn't there any more ({path}): send the "
                          "file again with --sliced")
    now = _sha(path.read_bytes())
    if now != kept.gcode_sha256:
        raise SlicedError(
            f"job {job_id}'s file has changed since it printed (sha256 "
            f"{kept.gcode_sha256[:12]}… then, {now[:12]}… now), so it isn't that job again: "
            "send the file you mean with --sliced")
    where = kept.overrides_from
    if kept.overrides is not None and not where.startswith("job "):
        where = f"job {job_id}'s record, read from {where}"
    return Again(path=path, name=row.stl_name, reprint_of=job_id, overrides=kept.overrides,
                 overrides_from=where)


# --- the report ---------------------------------------------------------------------------------


def report(job: SlicedJob, reading: SliceReading | None, flags: tuple[Flag, ...]) -> list[str]:
    """What the slice shows, read as the review reads one, for the person to see before the
    gate. A report only: it proposes nothing, and nothing is written back to the file."""
    out = ["The slice, read as it is (nothing is proposed, and nothing is written back):"]
    if reading is None:
        return [*out, "  its G-code couldn't be read layer by layer, so there's no reading"]
    out += [f"  {line}" for line in reading.lines()]
    if not reading.pauses:
        out.append("  no pause or filament swap is written in it")
    out += [f"  noted  {f.id}: {f.reading}" for f in flags]
    return out
