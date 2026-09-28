"""`configinfo` контейнера расширения (.cfe).

В `.cfe`, выгруженном конфигуратором (`/DumpCfg -Extension`), нет файлов
`root`/`version`/`versions`, как в `.cf`. Вместо них файл `configinfo`:

    {0,{216,0,{80327,0}}},
    {2,<UUID корня расширения>,},
    {4,"<файл>",<хеш>,"<файл>",<хеш>,...}

Хеш — base64(SHA1) блока данных файла в том виде, как он лежит в контейнере
(сжатым; `v8unpack -U` кладёт его в `<файл>.data`). После изменения модулей
таблицу нужно пересчитать по новым блокам.
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

FILE_NAME = "configinfo"

_RE_ROOT = re.compile(
    r"\{2,([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}),"
)
# Таблица хешей: `{N,"файл",хеш,...}`; хеш — base64 без кавычек.
_RE_TABLE = re.compile(
    r'\{(\d+),("[^"]*",[A-Za-z0-9+/]+={0,2}(?:,"[^"]*",[A-Za-z0-9+/]+={0,2})*),?\}'
)
_RE_ENTRY = re.compile(r'"([^"]*)",([A-Za-z0-9+/]+={0,2})')


class ConfigInfoError(ValueError):
    """`configinfo` не удалось разобрать или обновить"""


def read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig")


def write_text_like(path: Path, text: str) -> None:
    """Пишет текст с BOM, если он был у файла; переводы строк не трогает."""
    bom = b"\xef\xbb\xbf" if path.read_bytes().startswith(b"\xef\xbb\xbf") else b""
    path.write_bytes(bom + text.encode("utf-8"))


def root_uuid(text: str) -> str | None:
    """UUID корня расширения или None, если записи `{2,<uuid>,` нет."""
    match = _RE_ROOT.search(text)
    return match.group(1).lower() if match else None


def _table(text: str) -> re.Match[str]:
    match = None
    for match in _RE_TABLE.finditer(text):
        pass
    if match is None:
        raise ConfigInfoError("В configinfo нет таблицы хешей файлов")
    return match


def entries(text: str) -> list[tuple[str, str]]:
    """Пары (имя файла, хеш) из таблицы в порядке записи."""
    table = _table(text)
    pairs = _RE_ENTRY.findall(table.group(2))
    if int(table.group(1)) != len(pairs):
        raise ConfigInfoError(
            f"В таблице configinfo объявлено {table.group(1)} файлов, найдено {len(pairs)}"
        )
    return pairs


def with_hashes(text: str, hashes: dict[str, str]) -> str:
    """Текст configinfo с новыми хешами; порядок, имена и прочий текст прежние."""
    table = _table(text)
    names = {name for name, _ in entries(text)}
    missing = names - hashes.keys()
    if missing:
        raise ConfigInfoError(
            f"Нет хешей для файлов configinfo: {', '.join(sorted(missing))}"
        )

    def replace(match: re.Match[str]) -> str:
        return f'"{match.group(1)}",{hashes[match.group(1)]}'

    body = _RE_ENTRY.sub(replace, table.group(2))
    start, end = table.span(2)
    return text[:start] + body + text[end:]


def block_hash(data: bytes) -> str:
    """Хеш блока контейнера так, как его пишет платформа в configinfo."""
    return base64.b64encode(hashlib.sha1(data).digest()).decode("ascii")


def hashes_from_unpacked(unpacked_dir: Path, names: list[str]) -> dict[str, str]:
    """Хеши файлов по блокам `<имя>.data` из `v8unpack -U`."""
    result = {}
    for name in names:
        data_path = unpacked_dir / f"{name}.data"
        if not data_path.is_file():
            raise ConfigInfoError(f"В собранном контейнере нет блока '{name}'")
        result[name] = block_hash(data_path.read_bytes())
    return result
