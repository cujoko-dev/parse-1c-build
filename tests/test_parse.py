from pathlib import Path

import pytest

from parse_1c_build import parse
from parse_1c_build.cf_layout import (
    _extract_object_modules,
    _extract_plain_module,
)
from parse_1c_build.cli import get_argparser
from parse_1c_build.parse import run as parse_run


@pytest.fixture()
def test(request):
    return get_argparser()


def test_parse(test, tmpdir):
    parser = test

    temp_dir_path = Path(tmpdir)
    args = parser.parse_args(f"parse tests/fixtures/test.epf {temp_dir_path}".split())

    parse_run(args)

    # With named BSL + bin layout, unpacked structure is under bin/
    assert (temp_dir_path / "bin" / "root").exists()


def test_extract_plain_module_keeps_password_protected_payload(tmp_path: Path) -> None:
    module_path = tmp_path / "text"
    protected_payload = b"\xf9\x30\xa2\xcd\x00\xff"
    module_path.write_bytes(protected_payload)
    bsl_path = tmp_path / "ProtectedModule.bsl"

    assert _extract_plain_module(module_path, bsl_path) is False
    assert module_path.read_bytes() == protected_payload
    assert not bsl_path.exists()


def test_object_command_uses_name_from_object_descriptor(tmp_path: Path) -> None:
    object_uuid = "92876c9e-52ff-480d-9878-5766f4f9d12c"
    command_uuid = "38d92d75-c8b8-42d6-9e0a-6dc0811a8652"
    bin_dir = tmp_path / "bin"
    command_dir = bin_dir / f"{command_uuid}.2"
    command_dir.mkdir(parents=True)
    (bin_dir / object_uuid).write_text(
        f'{{0,0,{object_uuid}}},"Object",{{0,0,{command_uuid}}},"ВнешнийДоступ"',
        encoding="utf-8",
    )
    (command_dir / "text").write_text(
        "Процедура ОбработкаКоманды(ПараметрКоманды, ПараметрыВыполнения)\n"
        "КонецПроцедуры",
        encoding="utf-8",
    )

    _extract_object_modules(tmp_path, object_uuid)

    assert (tmp_path / "2_ВнешнийДоступ.bsl").is_file()
    assert not (tmp_path / "2_38d92d75.bsl").exists()
    assert (tmp_path / "meta" / "bsl_renames.txt").read_text(encoding="utf-8") == (
        f"2_ВнешнийДоступ.bsl --> bin/{command_uuid}.2/text\n"
    )


def test_parser_logs_start_before_dispatch(tmp_path: Path, monkeypatch) -> None:
    input_file = tmp_path / "configuration.cf"
    output_dir = tmp_path / "configuration_cf_src"
    calls: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(
        parse.logger,
        "info",
        lambda message, *args: calls.append((message, args)),
    )
    monkeypatch.setattr(
        parse.Parser,
        "_run_cf_cfe",
        lambda self, input_path, output_path, raw: calls.append(
            ("dispatch", (input_path, output_path, raw))
        ),
    )

    parser = object.__new__(parse.Parser)
    parser.run(input_file, output_dir, raw=False)

    assert calls == [
        (
            "Начинаю разбор контейнера '{}' в '{}'",
            (input_file, output_dir),
        ),
        ("dispatch", (input_file, output_dir, False)),
    ]


def test_cfu_is_routed_to_dedicated_handler(tmp_path: Path, monkeypatch) -> None:
    input_file = tmp_path / "update.cfu"
    output_dir = tmp_path / "update_cfu_src"
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        parse.Parser,
        "_run_cfu",
        lambda self, input_path, output_path, raw: calls.append(
            (input_path, output_path, raw)
        ),
    )

    parser = object.__new__(parse.Parser)
    parser.run(input_file, output_dir, raw=False)

    assert calls == [(input_file, output_dir, False)]


def test_run_cfu_forces_raw_unpack(tmp_path: Path, monkeypatch) -> None:
    """A CFU is a delta container: it must be unpacked raw regardless of the
    requested raw flag, and cf_layout organize/split must never run."""
    input_file = tmp_path / "update.cfu"
    output_dir = tmp_path / "update_cfu_src"
    unpack_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        parse.Parser,
        "_run_v8unpack_parse",
        lambda self, input_path, output_path, raw: unpack_calls.append(
            (input_path, output_path, raw)
        ),
    )

    parser = object.__new__(parse.Parser)
    parser._run_cfu(input_file, output_dir, raw=False)

    assert unpack_calls == [(input_file, output_dir, True)]
