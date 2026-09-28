"""Organize flat v8unpack CF/CFE dumps into Class/Object layout with BSL prefixes."""

from __future__ import annotations

import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from parse_1c_build import bsl, configinfo
from parse_1c_build.metadata_types import (
    CONFIG_MODULE_SLOTS,
    CONFIGURATION_TYPE_UUID,
    METADATA_TYPES,
)

logger.disable(__name__)

_FORCE_PYTHON = os.environ.get("P1CB_RUST_FORCE_PYTHON", "").strip().casefold() in (
    "1",
    "true",
    "yes",
)
try:
    from p1cb_native import (
        organize_configuration_dir as _rust_organize_configuration_dir,  # type: ignore[import-not-found]
    )
except ImportError:
    _rust_organize_configuration_dir = None

_RE_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_RE_COLLECTION = re.compile(
    r"\{("
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"),(\d+)((?:,[0-9a-fA-F-]{36})*)\}"
)
# Metadata identity: older dumps use {0,0,uuid},"Name"; newer (e.g. Retail) use {1,0,...}.
_RE_OBJECT_NAME = re.compile(r'\{[01],0,([0-9a-fA-F-]{36})\},"([^"]+)"')
_RE_CONFIG_IDENTITY = re.compile(r'\{[01],0,([0-9a-fA-F-]{36})\},"([^"]+)"')

CF_OBJECTS_FILENAME = "cfobjects.txt"
ROOT_MARKER_FILES = frozenset({"root", "version", "versions"})


@dataclass
class MetaObject:
    type_uuid: str
    object_uuid: str
    name: str
    class_folder: str
    root_prefix: str | None
    related_stems: set[str] = field(default_factory=set)

    @property
    def rel_dir(self) -> str:
        if self.root_prefix is not None:
            return ""
        return f"{bsl.OBJECTS_DIRNAME}/{self.class_folder}/{self.name}"


@dataclass
class DumpIndex:
    """Top-level dump_dir index: stem -> entry names (O(1) lookup, no glob)."""

    dump_dir: Path
    by_stem: dict[str, list[str]]
    names: set[str]

    @classmethod
    def build(cls, dump_dir: Path) -> DumpIndex:
        by_stem: dict[str, list[str]] = {}
        names: set[str] = set()
        with os.scandir(dump_dir) as it:
            for entry in it:
                names.add(entry.name)
                stem = entry.name.split(".", 1)[0].lower()
                by_stem.setdefault(stem, []).append(entry.name)
        for stem_names in by_stem.values():
            stem_names.sort()
        return cls(dump_dir=dump_dir, by_stem=by_stem, names=names)

    def stem_exists(self, stem: str) -> bool:
        return bool(self.by_stem.get(stem.lower()))

    def iter_stem_paths(self, stem: str) -> list[Path]:
        result: list[Path] = []
        for name in self.by_stem.get(stem.lower(), ()):
            path = self.dump_dir / name
            if path.exists():
                result.append(path)
        return result

    def forget(self, name: str) -> None:
        self.names.discard(name)
        stem = name.split(".", 1)[0].lower()
        entries = self.by_stem.get(stem)
        if not entries:
            return
        remaining = [n for n in entries if n != name]
        if remaining:
            self.by_stem[stem] = remaining
        else:
            del self.by_stem[stem]

    def take_stem_paths(self, stem: str) -> list[Path]:
        paths = self.iter_stem_paths(stem)
        for path in paths:
            self.forget(path.name)
        return paths

    def remaining_paths(self) -> list[Path]:
        paths: list[Path] = []
        for name in sorted(self.names):
            path = self.dump_dir / name
            if path.exists():
                paths.append(path)
        return paths


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig")


def _name_from_text(text: str, object_uuid: str) -> str:
    for m in _RE_OBJECT_NAME.finditer(text):
        if m.group(1).lower() == object_uuid.lower():
            return m.group(2)
    m = _RE_OBJECT_NAME.search(text)
    return m.group(2) if m else object_uuid


def _config_uuid_from_root(dump_dir: Path) -> str:
    root_path = dump_dir / "root"
    if not root_path.is_file():
        # Расширение (.cfe) хранит корень в configinfo, а не в root.
        configinfo_path = dump_dir / configinfo.FILE_NAME
        if not configinfo_path.is_file():
            raise configinfo.ConfigInfoError(
                f"CF dump has neither root nor configinfo file: '{dump_dir}'"
            )
        uuid = configinfo.root_uuid(_read_text(configinfo_path))
        if uuid is None:
            raise configinfo.ConfigInfoError(
                f"Cannot find extension root UUID in '{configinfo_path}'"
            )
        return uuid
    m = _RE_UUID.search(_read_text(root_path))
    if not m:
        raise Exception(f"Cannot find configuration UUID in '{root_path}'")
    return m.group(0).lower()


def _parse_collections(config_text: str) -> list[tuple[str, list[str]]]:
    result: list[tuple[str, list[str]]] = []
    for m in _RE_COLLECTION.finditer(config_text):
        type_uuid = m.group(1).lower()
        count = int(m.group(2))
        uuids = [u.lower() for u in _RE_UUID.findall(m.group(3))]
        if count == 0:
            continue
        if type_uuid == CONFIGURATION_TYPE_UUID:
            continue
        result.append((type_uuid, uuids[:count]))
    return result


def _discoverobjects(
    dump_dir: Path, index: DumpIndex
) -> tuple[str, str, list[MetaObject]]:
    config_uuid = _config_uuid_from_root(dump_dir)
    config_path = dump_dir / config_uuid
    if not config_path.is_file():
        raise Exception(f"Configuration descriptor missing: '{config_path}'")
    config_text = _read_text(config_path)
    objects: list[MetaObject] = []
    top_level: set[str] = {config_uuid}
    desc_texts: dict[str, str] = {}

    for type_uuid, uuids in _parse_collections(config_text):
        class_folder, root_prefix = METADATA_TYPES.get(
            type_uuid, (f"Type_{type_uuid[:8]}", None)
        )
        for object_uuid in uuids:
            top_level.add(object_uuid)
            desc = dump_dir / object_uuid
            if desc.is_file():
                text = _read_text(desc)
                desc_texts[object_uuid] = text
                name = _name_from_text(text, object_uuid)
            else:
                name = object_uuid
            objects.append(
                MetaObject(
                    type_uuid=type_uuid,
                    object_uuid=object_uuid,
                    name=name,
                    class_folder=class_folder,
                    root_prefix=root_prefix,
                )
            )

    for obj in objects:
        obj.related_stems.add(obj.object_uuid)
        text = desc_texts.get(obj.object_uuid)
        if not text:
            continue
        for ref in _RE_UUID.findall(text):
            ref_l = ref.lower()
            if ref_l in top_level and ref_l != obj.object_uuid:
                continue
            if index.stem_exists(ref_l):
                obj.related_stems.add(ref_l)
    return config_uuid, config_text, objects


def _safe_move(src: Path, dest: Path, *, ensure_parent: bool = True) -> None:
    if ensure_parent:
        dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    os.replace(src, dest)


def _extract_plain_module(module_path: Path, dest_bsl: Path) -> bool:
    """Extract plain-text module file to .bsl and replace with placeholder."""
    if not module_path.is_file():
        return False
    raw = module_path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        body = raw[3:]
    else:
        body = raw
    try:
        decoded = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        # A password-protected common module stores compiled/encrypted payload
        # in this slot.  It must remain byte-for-byte in bin/ and must not stop
        # organization of the rest of a CF/CFE dump.
        return False
    if decoded.strip() == "":
        dest_bsl.write_bytes(b"")
        module_path.write_bytes("\r\n".encode("utf-8"))
        return True
    dest_bsl.write_bytes(body)
    module_path.write_bytes(b"\xef\xbb\xbf" + bsl.BSL_PLACEHOLDER.encode("utf-8"))
    return True


def _extract_root_prefixed_object(
    index: DumpIndex,
    obj: MetaObject,
    root: Path,
    renames: list[tuple[str, str]],
) -> None:
    """Place dump files into root bin/ and extract BSL with root_prefix."""
    assert obj.root_prefix is not None
    bin_dir = root / bsl.BIN_DIRNAME
    bin_dir.mkdir(parents=True, exist_ok=True)
    for stem in sorted(obj.related_stems):
        for src in index.take_stem_paths(stem):
            rel = src.name
            dest = bin_dir / rel
            _safe_move(src, dest, ensure_parent=False)
            renames.append((rel, f"{bsl.BIN_DIRNAME}/{rel}"))

    text_path = bin_dir / f"{obj.object_uuid}.0" / "text"
    # Common commands store the handler module in uuid.2/text (not .0).
    command_text_path = bin_dir / f"{obj.object_uuid}.2" / "text"
    form_path = bin_dir / f"{obj.object_uuid}.0"
    bsl_name = f"{obj.root_prefix}{obj.name}.bsl"
    bsl_path = root / bsl_name
    if text_path.is_file():
        if _extract_plain_module(text_path, bsl_path):
            renames.append(
                (
                    bsl_name,
                    f"{bsl.BIN_DIRNAME}/{obj.object_uuid}.0/text",
                )
            )
    elif command_text_path.is_file():
        if _extract_plain_module(command_text_path, bsl_path):
            renames.append(
                (
                    bsl_name,
                    f"{bsl.BIN_DIRNAME}/{obj.object_uuid}.2/text",
                )
            )
    elif form_path.is_file() and not form_path.is_dir():
        if bsl.split_file(form_path, bsl_path):
            renames.append((bsl_name, f"{bsl.BIN_DIRNAME}/{obj.object_uuid}.0"))


def _extract_object_modules(object_dir: Path, object_uuid: str) -> None:
    """Extract modules inside an object mini-layout (bin already filled)."""
    bin_dir = object_dir / bsl.BIN_DIRNAME
    if not bin_dir.is_dir():
        return
    meta_dir = object_dir / bsl.META_DIRNAME
    meta_dir.mkdir(parents=True, exist_ok=True)
    bsl_renames: list[tuple[str, str]] = []
    renames_txt: list[str] = []
    handled_texts: set[Path] = set()
    texts: list[Path] = []
    form_items: list[Path] = []
    nested_names: dict[str, str] = {}
    object_uuid_l = object_uuid.lower()

    object_descriptor = bin_dir / object_uuid
    if object_descriptor.is_file():
        descriptor_text = _read_text(object_descriptor)
        nested_names = {
            match.group(1).lower(): match.group(2)
            for match in _RE_OBJECT_NAME.finditer(descriptor_text)
        }

    # One walk: collect files, build renames.txt entries, classify candidates.
    for dirpath, _dirnames, filenames in os.walk(bin_dir):
        base = Path(dirpath)
        for filename in filenames:
            path = base / filename
            rel = path.relative_to(bin_dir).as_posix()
            renames_txt.append(f"{rel}{bsl.RENAMES_ARROW}{bsl.BIN_DIRNAME}/{rel}\n")
            if filename == "text":
                texts.append(path)
            elif (
                bsl.is_managed_form_file(path)
                and path.name.removesuffix(".0").lower() != object_uuid_l
            ):
                form_items.append(path)
            elif filename == "module" and base.name.endswith(".0"):
                form_items.append(path)

    def _add_plain(text_path: Path, bsl_name: str, *, raw: bytes | None = None) -> None:
        if text_path in handled_texts:
            return
        if raw is None:
            if not text_path.is_file():
                return
            raw = text_path.read_bytes()
        body = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
        if not body.decode("utf-8-sig").strip():
            return
        dest = object_dir / bsl_name
        if dest.exists():
            return
        dest.write_bytes(body)
        text_path.write_bytes(b"\xef\xbb\xbf" + bsl.BSL_PLACEHOLDER.encode("utf-8"))
        rel = text_path.relative_to(bin_dir).as_posix()
        bsl_renames.append((bsl_name, f"{bsl.BIN_DIRNAME}/{rel}"))
        handled_texts.add(text_path)

    _add_plain(
        bin_dir / f"{object_uuid}.0" / "text",
        f"{bsl.BSL_PREFIX_OBJECT}Объект.bsl",
    )

    for text_path in texts:
        if text_path in handled_texts:
            continue
        parent_name = text_path.parent.name
        if not parent_name.endswith(".2"):
            continue
        raw = text_path.read_bytes()
        body = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
        text = body.decode("utf-8-sig")
        if not text.strip():
            continue
        stem = parent_name[:-2]
        is_command = "ОбработкаКоманды" in text
        if stem.lower() == object_uuid_l and not is_command:
            _add_plain(
                text_path,
                f"{bsl.BSL_PREFIX_OBJECT}Менеджер.bsl",
                raw=raw,
            )
            continue
        if not is_command:
            continue
        cmd_name = nested_names.get(stem.lower(), stem)
        desc = bin_dir / stem
        if desc.is_file():
            m = _RE_OBJECT_NAME.search(_read_text(desc))
            if m:
                cmd_name = m.group(2)
        elif cmd_name == stem:
            cmd_name = stem.split("-")[0]
        bsl_name = f"{bsl.BSL_PREFIX_COMMAND}{cmd_name}.bsl"
        if (object_dir / bsl_name).exists():
            bsl_name = f"{bsl.BSL_PREFIX_COMMAND}{cmd_name}_{stem[:8]}.bsl"
        _add_plain(text_path, bsl_name, raw=raw)

    form_items.sort(key=lambda p: (len(p.parts), str(p)))
    for item in form_items:
        companion = item.relative_to(bin_dir).as_posix()
        form_bsl_name: str | None = None
        if bsl.is_managed_form_file(item):
            form_name = bsl.get_form_or_object_name(bin_dir, item.name)
            if form_name:
                form_bsl_name = f"{bsl.BSL_PREFIX_FORM}{form_name}.bsl"
        elif item.name == "module" and item.parent.name.endswith(".0"):
            form_name = bsl.get_form_or_object_name(bin_dir, item.parent.name)
            if form_name:
                form_bsl_name = f"{bsl.BSL_PREFIX_FORM}{form_name}.bsl"
        if form_bsl_name and (object_dir / form_bsl_name).exists():
            internal_name = (
                item.name if bsl.is_managed_form_file(item) else item.parent.name
            )
            internal_stem = internal_name.removesuffix(".0")
            form_bsl_name = (
                f"{Path(form_bsl_name).stem}_{internal_stem[:8]}.bsl"
            )
        if form_bsl_name and bsl.split_file(item, object_dir / form_bsl_name):
            bsl_renames.append((form_bsl_name, f"{bsl.BIN_DIRNAME}/{companion}"))

    with (meta_dir / "renames.txt").open("w", encoding="utf-8") as f:
        f.writelines(sorted(renames_txt))
    if bsl_renames:
        bsl.write_bsl_renames_file(object_dir, sorted(set(bsl_renames)))


def _extract_config_modules(
    index: DumpIndex,
    config_text: str,
    root: Path,
    renames: list[tuple[str, str]],
) -> None:
    """Extract configuration application/session modules into root 0_*.bsl."""
    m = _RE_CONFIG_IDENTITY.search(config_text)
    if not m:
        return
    identity = m.group(1).lower()
    bin_dir = root / bsl.BIN_DIRNAME
    for slot, role in CONFIG_MODULE_SLOTS.items():
        stem = f"{identity}.{slot}"
        # slot dirs are named identity.N — stem split is identity, so take exact name
        src_dir = index.dump_dir / stem
        if not src_dir.exists():
            continue
        text_path = src_dir / "text"
        if not text_path.is_file():
            continue
        dest_dir = bin_dir / stem
        if src_dir.exists() and not dest_dir.exists():
            _safe_move(src_dir, dest_dir)
            index.forget(stem)
            renames.append((stem, f"{bsl.BIN_DIRNAME}/{stem}"))
        text_path = dest_dir / "text"
        if not text_path.is_file():
            continue
        if text_path.read_bytes().decode("utf-8-sig").strip() == "":
            continue
        bsl_name = f"{bsl.BSL_PREFIX_OBJECT}{role}.bsl"
        if _extract_plain_module(text_path, root / bsl_name):
            renames.append((bsl_name, f"{bsl.BIN_DIRNAME}/{identity}.{slot}/text"))


def organize_configuration_dir(
    dump_dir: Path,
    timings: dict[str, float] | None = None,
) -> None:
    """Transform flat v8unpack CF dump into objects/Class/Name + root BSL layout.

    If *timings* is provided, phase durations (seconds) are accumulated into it:
    ``index_discover``, ``root_modules``, ``move_objects``, ``extract_modules``,
    ``remaining_and_meta``.

    Uses optional Rust acceleration (``p1cb_native``) unless
    ``P1CB_RUST_FORCE_PYTHON`` is set.
    """
    dump_dir = Path(dump_dir).resolve()
    if not _FORCE_PYTHON and _rust_organize_configuration_dir is not None:
        rust_timings = _rust_organize_configuration_dir(str(dump_dir))
        if timings is not None and isinstance(rust_timings, dict):
            for name, seconds in rust_timings.items():
                timings[name] = timings.get(name, 0.0) + float(seconds)
        return
    _organize_configuration_dir_python(dump_dir, timings=timings)


def _organize_configuration_dir_python(
    dump_dir: Path,
    timings: dict[str, float] | None = None,
) -> None:
    """Pure-Python implementation of :func:`organize_configuration_dir`."""

    def _phase(name: str, started: float) -> None:
        if timings is not None:
            timings[name] = timings.get(name, 0.0) + (time.perf_counter() - started)

    dump_dir = dump_dir.resolve()
    t0 = time.perf_counter()
    index = DumpIndex.build(dump_dir)
    _config_uuid, config_text, objects = _discoverobjects(dump_dir, index)
    _phase("index_discover", t0)
    logger.info(f"CF layout: {len(objects)} metadata object(s) in '{dump_dir}'")

    # In-place: keep moves on the same volume (rename), no staging copy.
    rootbin = dump_dir / bsl.BIN_DIRNAME
    rootmeta = dump_dir / bsl.META_DIRNAME
    objects_root = dump_dir / bsl.OBJECTS_DIRNAME
    rootbin.mkdir(parents=True, exist_ok=True)
    rootmeta.mkdir(parents=True, exist_ok=True)
    objects_root.mkdir(parents=True, exist_ok=True)
    index.forget(bsl.BIN_DIRNAME)
    index.forget(bsl.META_DIRNAME)
    index.forget(bsl.OBJECTS_DIRNAME)

    root_renames: list[tuple[str, str]] = []
    objects_index: list[tuple[str, str]] = []

    t0 = time.perf_counter()
    for obj in objects:
        if obj.root_prefix is None:
            continue
        _extract_root_prefixed_object(index, obj, dump_dir, root_renames)
        objects_index.append((f"@{obj.root_prefix}{obj.name}", obj.object_uuid))

    _extract_config_modules(index, config_text, dump_dir, root_renames)
    _phase("root_modules", t0)

    extract_jobs: list[tuple[Path, str]] = []
    t0 = time.perf_counter()
    for obj in objects:
        if obj.root_prefix is not None:
            continue
        obj_dir = objects_root / obj.class_folder / obj.name
        objbin = obj_dir / bsl.BIN_DIRNAME
        objbin.mkdir(parents=True, exist_ok=True)
        for stem in sorted(obj.related_stems):
            for src in index.take_stem_paths(stem):
                _safe_move(src, objbin / src.name, ensure_parent=False)
        extract_jobs.append((obj_dir, obj.object_uuid))
        objects_index.append((obj.rel_dir, obj.object_uuid))
    _phase("move_objects", t0)

    t0 = time.perf_counter()
    if extract_jobs:
        workers = min(32, max(4, (os.cpu_count() or 4) * 2))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_extract_object_modules, obj_dir, object_uuid)
                for obj_dir, object_uuid in extract_jobs
            ]
            for fut in as_completed(futures):
                fut.result()
    _phase("extract_modules", t0)

    t0 = time.perf_counter()
    for item in index.remaining_paths():
        name = item.name
        if name in (bsl.BIN_DIRNAME, bsl.META_DIRNAME, bsl.OBJECTS_DIRNAME):
            continue
        dest = rootbin / name
        _safe_move(item, dest, ensure_parent=False)
        index.forget(name)
        root_renames.append((name, f"{bsl.BIN_DIRNAME}/{name}"))

    with (rootmeta / CF_OBJECTS_FILENAME).open("w", encoding="utf-8") as f:
        for rel, uuid in sorted(objects_index, key=lambda x: x[0]):
            f.write(f"{rel}{bsl.RENAMES_ARROW}{uuid}\n")
    with (rootmeta / "renames.txt").open("w", encoding="utf-8") as f:
        for target, source in sorted(set(root_renames), key=lambda x: x[0]):
            if target.endswith(".bsl"):
                continue
            f.write(f"{target}{bsl.RENAMES_ARROW}{source}\n")
    bsl_root_entries = [(t, s) for t, s in root_renames if t.endswith(".bsl")]
    if bsl_root_entries:
        bsl.write_bsl_renames_file(dump_dir, sorted(set(bsl_root_entries)))
    _phase("remaining_and_meta", t0)

    logger.info(f"CF layout organized in '{dump_dir}'")


def has_cf_layout(dir_path: Path) -> bool:
    """True if directory looks like organized CF sources."""
    return (dir_path / bsl.META_DIRNAME / CF_OBJECTS_FILENAME).is_file()


def _copy_tree_entries(src_dir: Path, dest_dir: Path) -> None:
    """Copy all entries from src_dir into dest_dir (no overwrite of existing)."""
    for item in src_dir.iterdir():
        dest = dest_dir / item.name
        if dest.exists():
            continue
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)


def _move_tree_entries(src_dir: Path, dest_dir: Path) -> None:
    """Move prepared temp entries into the flat dump without copying again."""
    for item in src_dir.iterdir():
        dest = dest_dir / item.name
        if dest.exists():
            continue
        item.replace(dest)


def prepare_configuration_for_build(input_dir: Path, temp_parent: Path) -> Path:
    """Flatten organized CF layout to a temp dump directory for v8unpack -B."""
    input_dir = input_dir.resolve()
    temp_dump = temp_parent / "cf_dump"
    temp_dump.mkdir(parents=True, exist_ok=True)

    objects_path = input_dir / bsl.META_DIRNAME / CF_OBJECTS_FILENAME
    object_actions: list[tuple[str, str, Path]] = []
    with objects_path.open(encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if bsl.RENAMES_ARROW not in line:
                continue
            rel, _uuid = (s.strip() for s in line.split("-->", 1))
            if rel.startswith("@"):
                continue
            obj_dir = input_dir / rel
            if not obj_dir.is_dir():
                continue
            if bsl.has_bin_layout(obj_dir):
                safe_key = rel.replace("\\", "/").replace("/", "__")
                object_actions.append(("prepare", safe_key, obj_dir))
            else:
                bin_dir = obj_dir / bsl.BIN_DIRNAME
                if bin_dir.is_dir():
                    object_actions.append(("copy", rel, bin_dir))

    prepare_actions = [action for action in object_actions if action[0] == "prepare"]
    workers = min(4, len(prepare_actions))
    prepared_by_key: dict[str, Path] = {}
    if workers:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                key: pool.submit(
                    bsl.prepare_temp_for_build,
                    obj_dir,
                    temp_parent / f"obj_{key}",
                )
                for _kind, key, obj_dir in prepare_actions
            }
            for kind, key, _path in object_actions:
                if kind == "prepare":
                    prepared_by_key[key] = futures[key].result()

    # Preserve cfobjects.txt order when duplicate flat-dump entries exist.
    for kind, key, path in object_actions:
        if kind == "prepare":
            _move_tree_entries(prepared_by_key[key], temp_dump)
        else:
            _copy_tree_entries(path, temp_dump)

    if (input_dir / bsl.META_DIRNAME / bsl.BSL_RENAMES_FILENAME).is_file() and (
        input_dir / bsl.BIN_DIRNAME
    ).is_dir():
        if bsl.has_bin_layout(input_dir):
            prepared_root = bsl.prepare_temp_for_build(
                input_dir, temp_parent / "cf_root"
            )
            _move_tree_entries(prepared_root, temp_dump)
        else:
            bsl.merge_dir(input_dir)
            _copy_tree_entries(input_dir / bsl.BIN_DIRNAME, temp_dump)
    elif (input_dir / bsl.BIN_DIRNAME).is_dir():
        _copy_tree_entries(input_dir / bsl.BIN_DIRNAME, temp_dump)

    return temp_dump
