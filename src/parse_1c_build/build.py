import shutil
import tempfile
from pathlib import Path

from loguru import logger
import questionary

from parse_1c_build import bsl, cf_layout, configinfo
from parse_1c_build.base import (
    EXTENSIONS_CF_CFE,
    EXTENSIONS_EPF_ERF,
    EXTENSIONS_MD_ERT,
    EXTENSIONS_V8UNPACK,
    Processor,
    add_generic_arguments,
)
from parse_1c_build.process_utils import check_silent

logger.disable(__name__)

ERR_CWD_INPUTS_NOT_FOUND = (
    "Не найдено подходящих каталогов исходников "
    "(*_epf_src, *_erf_src, *_cf_src, *_cfe_src, …) "
    "в текущем каталоге (и подкаталогах). Укажите каталог или используйте -i"
)
ERR_INTERACTIVE_NOT_FOUND = "Не найдено подходящих входных каталогов для интерактивного выбора"
ERR_INTERACTIVE_CANCELLED = "Интерактивный выбор отменен пользователем"


def _resolve_output_path(
    input_dir_path: Path,
    output_path: Path | None,
) -> Path:
    """Resolve output file path from input dir and optional output path."""
    if output_path is not None and not output_path.is_dir():
        return output_path
    parent = output_path if (output_path is not None and output_path.is_dir()) else input_dir_path.parent
    # input_dir_path.name is like "name_epf_src" or "name_ert_src"
    name_with_ext = input_dir_path.name.rpartition("_")[0]
    name, _, ext = name_with_ext.rpartition("_")
    return Path(parent, f"{name}.{ext}")


def _backup_existing(path: Path) -> None:
    """Rename existing file to path.name.1.bak, .2.bak, ..."""
    n = 1
    while True:
        bak = path.parent / f"{path.name}.{n}.bak"
        if not bak.exists():
            break
        n += 1
    path.rename(bak)
    logger.info("Существующий выходной файл перемещён в резервную копию: '{}'", bak)


def _input_dirs_get() -> list[Path]:
    """Возвращает список входных каталогов исходников для интерактивного выбора."""
    allowed_ext = {ext[1:] for ext in (*EXTENSIONS_V8UNPACK, *EXTENSIONS_MD_ERT)}
    input_dirs = []
    for dir_path in Path.cwd().rglob("*_src"):
        if not dir_path.is_dir():
            continue
        name_without_src, _, _ = dir_path.name.rpartition("_src")
        _, _, ext = name_without_src.rpartition("_")
        if ext.lower() in allowed_ext:
            input_dirs.append(dir_path)
    return sorted(input_dirs)


def _input_dirs_select_interactive(input_dirs: list[Path]) -> list[Path]:
    """Интерактивно выбирает входные каталоги для сборки."""
    if not input_dirs:
        raise Exception(ERR_INTERACTIVE_NOT_FOUND)

    try:
        selected = questionary.checkbox(
            "Выберите каталоги исходников для сборки",
            choices=[
                questionary.Choice(
                    title=str(path.relative_to(Path.cwd())),
                    value=path,
                )
                for path in input_dirs
            ],
            validate=lambda value: "Выберите хотя бы один каталог" if not value else True,
            qmark="build",
        ).ask()
    except KeyboardInterrupt as exc:
        raise Exception(ERR_INTERACTIVE_CANCELLED) from exc

    if selected is None:
        raise Exception(ERR_INTERACTIVE_CANCELLED)

    return selected


class Builder(Processor):
    @staticmethod
    def _build_temp_from_renames(input_dir_path: Path) -> Path:
        """Build temp directory from renames.txt (target -> source)."""
        temp_source_dir_path = Path(tempfile.mkdtemp())
        renames_path = input_dir_path / "renames.txt"
        with renames_path.open(encoding="utf-8-sig") as f:
            for line in f:
                if bsl.RENAMES_ARROW not in line:
                    continue
                target_str, source_str = (s.strip() for s in line.split("-->", 1))
                new_path = temp_source_dir_path / target_str
                new_path.parent.mkdir(parents=True, exist_ok=True)
                old_path = input_dir_path / source_str
                if old_path.is_dir():
                    shutil.copytree(old_path, new_path)
                else:
                    shutil.copy2(old_path, new_path)
        return temp_source_dir_path

    def _get_source_dir_for_epf_build(self, input_dir_path: Path) -> Path:
        """Return path to source directory for v8unpack -B (temp or input_dir_path)."""
        if self.use_reader:
            self.warn_use_reader_deprecated(logger)
            return self._build_temp_from_renames(input_dir_path)
        if bsl.has_bin_layout(input_dir_path):
            temp_parent = Path(tempfile.mkdtemp())
            return bsl.prepare_temp_for_build(input_dir_path, temp_parent)
        bsl_files = list(input_dir_path.rglob("*.bsl"))
        if bsl_files:
            temp_parent = Path(tempfile.mkdtemp())
            temp_source = temp_parent / input_dir_path.name
            shutil.copytree(input_dir_path, temp_source)
            bsl.merge_dir(temp_source)
            return temp_source
        return input_dir_path

    def _run_epf_erf_build(
        self,
        input_dir_path: Path,
        output_file_path: Path,
        force: bool = False,
    ) -> None:
        """Build EPF/ERF from source directory via v8unpack -B."""
        source_dir = self._get_source_dir_for_epf_build(input_dir_path)
        args = [str(self.get_v8_unpack_file_path())]
        if force:
            args.append("--force")
        args += ["-B", str(source_dir), str(output_file_path)]
        check_silent(args)
        logger.info(f"'{output_file_path}' built from '{input_dir_path}'")

    def _run_cf_cfe_build(
        self,
        input_dir_path: Path,
        output_file_path: Path,
        force: bool = False,
    ) -> None:
        """Build CF/CFE from organized or raw source directory via v8unpack -B."""
        if self.use_reader:
            self.warn_use_reader_deprecated(logger)
            logger.warning(
                "--use-reader is ignored for .cf/.cfe; using v8unpack"
            )
        if cf_layout.has_cf_layout(input_dir_path):
            temp_parent = Path(tempfile.mkdtemp())
            source_dir = cf_layout.prepare_configuration_for_build(
                input_dir_path, temp_parent
            )
        else:
            source_dir = input_dir_path
        args = [str(self.get_v8_unpack_file_path())]
        if force:
            args.append("--force")
        args += ["-B", str(source_dir), str(output_file_path)]
        check_silent(args)
        if (source_dir / configinfo.FILE_NAME).is_file():
            self._refresh_configinfo_hashes(
                source_dir, source_dir == input_dir_path, output_file_path
            )
        logger.info(f"'{output_file_path}' built from '{input_dir_path}'")

    def _unpack_blocks(self, container_path: Path, output_dir: Path) -> None:
        check_silent(
            [
                str(self.get_v8_unpack_file_path()),
                "-U",
                str(container_path),
                str(output_dir),
            ]
        )

    def _refresh_configinfo_hashes(
        self,
        source_dir: Path,
        source_is_input: bool,
        output_file_path: Path,
    ) -> None:
        """Пересчитывает таблицу хешей configinfo расширения по собранному контейнеру.

        Хеши — base64(SHA1) сжатых блоков (`v8unpack -U`), поэтому сначала контейнер
        собирается как есть, затем configinfo обновляется и контейнер собирается
        ещё раз. Сжатие детерминировано: блоки остальных файлов не меняются, что
        проверяется повторной распаковкой. Каталог исходников пользователя не
        меняется: при сборке из него правится временная копия.

        Конфигуратор 8.3.27 при `/LoadCfg -Extension` таблицу не сверяет (проверено
        на фикстурах CodeMask), но контейнер остаётся таким, каким его пишет
        платформа: другие загрузчики расширений не проверялись.
        """
        temp_parent = Path(tempfile.mkdtemp())
        try:
            self._unpack_blocks(output_file_path, temp_parent / "blocks")
            text = configinfo.read_text(source_dir / configinfo.FILE_NAME)
            names = [name for name, _ in configinfo.entries(text)]
            hashes = configinfo.hashes_from_unpacked(temp_parent / "blocks", names)
            refreshed = configinfo.with_hashes(text, hashes)
            if refreshed == text:
                return
            if source_is_input:
                build_dir = temp_parent / "source"
                shutil.copytree(source_dir, build_dir)
            else:
                build_dir = source_dir
            configinfo.write_text_like(build_dir / configinfo.FILE_NAME, refreshed)
            check_silent(
                [
                    str(self.get_v8_unpack_file_path()),
                    "--force",
                    "-B",
                    str(build_dir),
                    str(output_file_path),
                ]
            )
            self._unpack_blocks(output_file_path, temp_parent / "check")
            actual = configinfo.hashes_from_unpacked(temp_parent / "check", names)
            if actual != hashes:
                changed = sorted(name for name in names if actual[name] != hashes[name])
                raise configinfo.ConfigInfoError(
                    "Блоки контейнера изменились при повторной сборке: "
                    + ", ".join(changed)
                )
            logger.info(
                "configinfo: пересчитаны хеши {} из {} файлов",
                sum(
                    1 for name, old in configinfo.entries(text) if hashes[name] != old
                ),
                len(names),
            )
        finally:
            shutil.rmtree(temp_parent, ignore_errors=True)

    def _run_md_ert_build(
        self,
        input_dir_path: Path,
        output_file_path: Path,
    ) -> None:
        """Build MD/ERT via gcomp -c -F ... -DD ..."""
        suffix = output_file_path.suffix.lower()
        args = [str(self.get_gcomp_file_path())]
        if suffix == ".ert":
            args.append("--external-report")
        elif suffix == ".md":
            args.append("--meta-data")
        args += ["-c", "-F", str(output_file_path), "-DD", str(input_dir_path)]
        check_silent(args)
        logger.info(f"'{output_file_path}' built from '{input_dir_path}'")

    def run(
        self,
        input_dir_path: Path,
        output_path: Path | None = None,
        do_not_backup: bool = False,
    ) -> None:
        """Собирает обработку из исходных файлов"""
        output_file_path = _resolve_output_path(input_dir_path, output_path)
        if not do_not_backup and output_file_path.exists() and output_file_path.is_file():
            _backup_existing(output_file_path)
        logger.info(
            "Начинаю сборку контейнера '{}' из '{}'",
            output_file_path,
            input_dir_path,
        )

        # Без бэкапа существующий выходной файл остаётся на месте, а v8unpack без
        # --force отказывается его перезаписывать. Бэкап же освобождает путь сам:
        # он переносит старый файл в .bak
        force = do_not_backup

        suffix = output_file_path.suffix.lower()
        if suffix in EXTENSIONS_EPF_ERF:
            self._run_epf_erf_build(input_dir_path, output_file_path, force)
        elif suffix in EXTENSIONS_CF_CFE:
            self._run_cf_cfe_build(input_dir_path, output_file_path, force)
        elif suffix in EXTENSIONS_MD_ERT:
            self._run_md_ert_build(input_dir_path, output_file_path)
        else:
            raise Exception("Undefined output file type")


def _input_dir_paths_get(args) -> list[Path]:
    """Build input directory paths list from parsed CLI args."""
    if args.interactive:
        return _input_dirs_select_interactive(_input_dirs_get())

    if args.input:
        return [Path(args.input)]

    discovered = _input_dirs_get()
    if not discovered:
        raise Exception(ERR_CWD_INPUTS_NOT_FOUND)

    return discovered


def run(args) -> None:
    """Запустить"""
    from parse_1c_build import logger

    logger.enable("cjk_commons")
    logger.enable("commons_1c")
    logger.enable(Builder.__module__)
    logger.enable(cf_layout.__name__)
    try:
        builder = Builder(**vars(args))
        output_path = None if args.output is None else Path(args.output)
        for input_dir_path in _input_dir_paths_get(args):
            builder.run(
                input_dir_path=input_dir_path,
                output_path=output_path,
                do_not_backup=args.do_not_backup,
            )
    except Exception as exc:
        logger.exception(exc)
        raise SystemExit(1)


def add_subparser(subparsers) -> None:
    """Добавить подпарсер"""
    desc = (
        "Build files in a directory to 1C:Enterprise file. "
        "If input is omitted, all matching source directories under the current directory are built."
    )
    subparser = subparsers.add_parser(
        Path(__file__).stem,
        add_help=False,
        description=desc,
        help=desc,
    )
    subparser.set_defaults(func=run)
    add_generic_arguments(subparser)
    subparser.add_argument(
        "-x",
        "--do-not-backup",
        action="store_true",
        help="Do not backup 1C-file before building",
    )
