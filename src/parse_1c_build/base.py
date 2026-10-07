import sys
from pathlib import Path

from cjk_commons.settings import get_attribute, get_path_attribute, get_settings

from parse_1c_build.__about__ import APP_AUTHOR, APP_NAME

# File extensions for containers handled by Parser and Builder.
EXTENSIONS_EPF_ERF = (".epf", ".erf")
EXTENSIONS_CF_CFE = (".cf", ".cfe")
# Configuration update files (.cfu). The outer container is the same v8 format,
# so v8unpack parses/builds it, but inside it is a delta (metadata + full images
# of changed objects), not a CF layout. It is therefore handled raw only: no
# cf_layout organize/split on parse, no configinfo on build.
EXTENSIONS_CFU = (".cfu",)
EXTENSIONS_V8UNPACK = EXTENSIONS_EPF_ERF + EXTENSIONS_CF_CFE + EXTENSIONS_CFU
EXTENSIONS_MD_ERT = (".md", ".ert")

USE_READER_DEPRECATED_MSG = (
    "--use-reader is deprecated and will be removed; "
    "it only applies to .epf/.erf (V8Reader)"
)


def bundled_v8unpack_path() -> Path | None:
    """Return the v8unpack binary shipped with this package, if present."""
    name = "v8unpack.exe" if sys.platform == "win32" else "v8unpack"
    candidate = Path(__file__).resolve().parent / "vendor" / name
    if candidate.is_file():
        return candidate
    return None


class Processor:
    """Процессор"""

    def __init__(self, **kwargs):
        settings_file_path = get_path_attribute(
            kwargs,
            "settings_file_path",
            default_path=Path("settings.yaml"),
            is_dir=False,
            check_if_exists=False,
        )
        self.settings = get_settings(
            settings_file_path, app_name=APP_NAME, app_author=APP_AUTHOR
        )

        self.use_reader = get_attribute(
            kwargs, "use_reader", self.settings, "use_reader", default=False
        )
        self._use_reader_warned = False

    def warn_use_reader_deprecated(self, logger) -> None:
        """Log deprecation once per processor instance when use_reader is set."""
        if self.use_reader and not self._use_reader_warned:
            logger.warning(USE_READER_DEPRECATED_MSG)
            self._use_reader_warned = True

    def get_v8_unpack_file_path(self, **kwargs) -> Path:
        if "v8unpack_file_path" in kwargs:
            return get_path_attribute(
                kwargs,
                "v8unpack_file_path",
                is_dir=False,
                check_if_exists=False,
            )
        bundled = bundled_v8unpack_path()
        if bundled is not None:
            return bundled
        sibling = Path(sys.executable).with_name(
            "v8unpack.exe" if sys.platform == "win32" else "v8unpack"
        )
        if sibling.is_file():
            return sibling.resolve()
        return get_path_attribute(
            kwargs,
            "v8unpack_file_path",
            self.settings,
            "v8unpack_file",
            Path("v8unpack/v8unpack.exe"),
            False,
        )

    def get_gcomp_file_path(self, **kwargs) -> Path:
        return get_path_attribute(
            kwargs,
            "gcomp_file_path",
            self.settings,
            "gcomp_file",
            Path("GComp/Release/gcomp.exe"),
            False,
        )


def add_generic_arguments(subparser) -> None:
    subparser.add_argument(
        "-h",
        "--help",
        action="help",
        help="Show this help message and exit",
    )
    subparser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Select interactively",
    )
    subparser.add_argument(
        "-u",
        "--use-reader",
        action="store_true",
        help=("Deprecated; will be removed. Parse or build .epf/.erf with V8Reader"),
    )

    # todo Добавить help
    subparser.add_argument("input", nargs="?")

    # todo Добавить help
    subparser.add_argument("output", nargs="?")
