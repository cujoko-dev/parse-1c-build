"""configinfo расширений (.cfe): корень при разборе и таблица хешей при сборке."""

from __future__ import annotations

import base64
import hashlib
import subprocess
from pathlib import Path

import pytest

from parse_1c_build import cf_layout, configinfo
from parse_1c_build.base import Processor
from parse_1c_build.build import Builder
from parse_1c_build.parse import Parser

ROOT = "cd23f969-05ae-5af5-a23d-d84414da3ad6"
SAMPLE = (
    "{0,\r\n{216,0,\r\n{80327,0}\r\n}\r\n},\r\n"
    f"{{2,{ROOT},}},\r\n"
    '{3,"555036e7-2141-54e6-93b5-d70292b7c8ee",L2g6wODGDmtbYNYDvGLmmVZCH0U=,'
    '"8d81d08a-dbd1-5e99-b828-b2c2853d505e.0",ry5RCle66MX9+ALd2niYfqgvX7A=,'
    f'"{ROOT}",SMjiQxioJv3xUALlqXTVYrDoSEc=}}'
)
LOCAL_CFE = sorted((Path(__file__).parent / "local-fixtures").glob("*.cfe"))


def test_root_uuid_from_configinfo() -> None:
    assert configinfo.root_uuid(SAMPLE) == ROOT
    assert configinfo.root_uuid("{0,{216,0}}") is None


def test_entries_keep_order_and_check_count() -> None:
    assert [name for name, _ in configinfo.entries(SAMPLE)] == [
        "555036e7-2141-54e6-93b5-d70292b7c8ee",
        "8d81d08a-dbd1-5e99-b828-b2c2853d505e.0",
        ROOT,
    ]
    with pytest.raises(configinfo.ConfigInfoError, match="объявлено 4"):
        configinfo.entries(SAMPLE.replace("{3,", "{4,"))
    with pytest.raises(configinfo.ConfigInfoError, match="нет таблицы"):
        configinfo.entries("{0,{216,0}},\r\n{2," + ROOT + ",}")


def test_with_hashes_changes_only_hash_values() -> None:
    names = [name for name, _ in configinfo.entries(SAMPLE)]
    new = {name: configinfo.block_hash(name.encode()) for name in names}
    refreshed = configinfo.with_hashes(SAMPLE, new)
    assert configinfo.entries(refreshed) == [(name, new[name]) for name in names]
    assert refreshed.split(f"{{2,{ROOT},}}")[0] == SAMPLE.split(f"{{2,{ROOT},}}")[0]
    assert refreshed.count("\r\n") == SAMPLE.count("\r\n")
    with pytest.raises(configinfo.ConfigInfoError, match="Нет хешей"):
        configinfo.with_hashes(SAMPLE, {names[0]: new[names[0]]})


def test_block_hash_is_base64_sha1() -> None:
    assert configinfo.block_hash(b"") == "2jmj7l5rSw0yVb/vlWAYkK/YBwk="


def test_write_text_like_keeps_bom(tmp_path: Path) -> None:
    path = tmp_path / configinfo.FILE_NAME
    path.write_bytes(b"\xef\xbb\xbf" + SAMPLE.encode())
    configinfo.write_text_like(path, SAMPLE.replace("{3,", "{3, "))
    assert path.read_bytes().startswith(b"\xef\xbb\xbf{0,")


def test_cf_layout_takes_extension_root_from_configinfo(tmp_path: Path) -> None:
    (tmp_path / configinfo.FILE_NAME).write_bytes(b"\xef\xbb\xbf" + SAMPLE.encode())
    assert cf_layout._config_uuid_from_root(tmp_path) == ROOT
    (tmp_path / configinfo.FILE_NAME).unlink()
    with pytest.raises(Exception, match="neither root nor configinfo"):
        cf_layout._config_uuid_from_root(tmp_path)


def _hashes_match_blocks(container: Path, tmp_path: Path) -> list[tuple[str, str]]:
    v8unpack = str(Processor().get_v8_unpack_file_path())
    blocks, raw = tmp_path / "blocks", tmp_path / "raw"
    subprocess.run(
        [v8unpack, "-U", str(container), str(blocks)], check=True, capture_output=True
    )
    subprocess.run(
        [v8unpack, "-P", str(container), str(raw)], check=True, capture_output=True
    )
    pairs = configinfo.entries(configinfo.read_text(raw / configinfo.FILE_NAME))
    for name, value in pairs:
        data = (blocks / f"{name}.data").read_bytes()
        assert value == base64.b64encode(hashlib.sha1(data).digest()).decode(), name
    return pairs


@pytest.mark.skipif(not LOCAL_CFE, reason="нет tests/local-fixtures/*.cfe")
@pytest.mark.parametrize("cfe", LOCAL_CFE, ids=lambda p: p.stem)
def test_cfe_roundtrip_with_changed_module_refreshes_configinfo(
    cfe: Path, tmp_path: Path
) -> None:
    marker = "// parse-1c-build: изменённый модуль"
    src = tmp_path / f"{cfe.stem}_cfe_src"
    Parser().run(cfe, src)
    modules = sorted(src.rglob("*.bsl"))
    assert modules, "в расширении нет модулей"
    modules[0].write_bytes(modules[0].read_bytes().rstrip() + f"\n{marker}\n".encode())

    built = tmp_path / f"{cfe.stem}.cfe"
    Builder().run(src, built, do_not_backup=True)
    assert _hashes_match_blocks(built, tmp_path / "check")

    reparsed = tmp_path / f"{cfe.stem}-again_cfe_src"
    Parser().run(built, reparsed)
    relative = modules[0].relative_to(src)
    assert marker in (reparsed / relative).read_text(encoding="utf-8-sig")


@pytest.mark.skipif(not LOCAL_CFE, reason="нет tests/local-fixtures/*.cfe")
def test_cfe_raw_build_leaves_source_configinfo_untouched(tmp_path: Path) -> None:
    cfe = LOCAL_CFE[0]
    raw = tmp_path / f"{cfe.stem}_cfe_src"
    Parser().run(cfe, raw, raw=True)
    before = (raw / configinfo.FILE_NAME).read_bytes()

    built = tmp_path / f"{cfe.stem}.cfe"
    Builder().run(raw, built, do_not_backup=True)
    assert (raw / configinfo.FILE_NAME).read_bytes() == before
    assert _hashes_match_blocks(built, tmp_path / "check")
