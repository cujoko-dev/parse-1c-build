---
name: parse-1c-build-architecture
description: >-
  Architecture of parse-1c-build: EPF/ERF/CF/CFE unpack/pack pipeline
  (v8unpack, V8Reader, gcomp),
  BSL extraction layout (prefixes 0_/1_/2_/9_, objects/Class/Name for CF),
  meta/bin structure,
  and round-trip rebuild. Use when modifying Parser/Builder, extending BSL
  split/merge,
  debugging roundtrip mismatches, or working with the p1cb CLI.
---

# parse-1c-build: архитектура парсера

## Что делает проект

**parse-1c-build** — Python-библиотека и CLI для **распаковки и сборки**
1C-артефактов:

- `.epf`/`.erf` — через **v8unpack** (по умолчанию) или deprecated **V8Reader +
  1C платформа**
- `.cf`/`.cfe` — через **v8unpack** + раскладка `Класс/Объект/`
- `.md`/`.ert` — через **GComp**

Проект **не** парсит BSL в AST. Он оркестрирует внешние инструменты и добавляет
слой **извлечения BSL** из артефактов v8unpack чтобы модули были редактируемы
как `.bsl`-файлы со стабильной структурой `bin/ + meta/`.

## Точки входа

- **CLI:** `p1cb` → `parse_1c_build.__main__:run` → `core.run()`
- **Субкоманды:** `parse` (модуль `parse.py`) и `build` (модуль `build.py`)
- **Программный API:** `Parser`, `Builder` из `parse_1c_build`

## Ключевые модули

| Файл                | Роль                                                                       |
| ------------------- | -------------------------------------------------------------------------- |
| `base.py`           | `Processor`: настройки, пути к `v8unpack`/`gcomp`, deprecated `use_reader` |
| `parse.py`          | `Parser`: распаковка EPF/ERF/CF/CFE/MD/ERT                                 |
| `build.py`          | `Builder`: сборка через v8unpack/GComp                                     |
| `bsl.py`            | split/merge BSL, префиксы `0_`/`1_`/`2_`/`9_`, `meta/` + `bin/`            |
| `cf_layout.py`      | CF/CFE: нарезка dump → `Класс/Объект/`, корень конфигурации                |
| `metadata_types.py` | UUID типов метаданных → имена классов                                      |
| `process_utils.py`  | `run_silent` / `check_silent`                                              |

## Префиксы BSL

- `0_` — модули объекта/конфигурации (Объект, Менеджер, УправляемоеПриложение,
  …)
- `1_` — все формы (общие и объектов)
- `2_` — все команды (общие и объектов)
- `9_` — общие модули (только корень CF)

## Пайплайн распаковки EPF/ERF

```
Parser.run(input.epf)
  → output dir = parent / "{stem}_epf_src"
  → rmtree existing output
  → v8unpack -P input.epf output_dir   (или V8Reader bat)
  → (если не --raw) bsl.split_dir(output_dir)
```

**V8Reader-ветка (deprecated, только .epf/.erf):** пишет временный `.bat`
(cp866), запускает 1cv8 с `/Execute V8Reader.epf`.

## Пайплайн распаковки CF/CFE

```
Parser.run(input.cf)
  → output dir = parent / "{stem}_cf_src"
  → v8unpack -P
  → (если не --raw) cf_layout.organize_configuration_dir
       → objects/Catalogs|Documents|…/Name/{0_*.bsl,1_*.bsl,2_*.bsl,bin,meta}
       → корень: 0_/1_/2_/9_*.bsl + bin/meta/objects
```

## Типы форм и как извлекается BSL (`bsl.py`)

### Управляемые формы (`UUID.0`)

- Имя файла соответствует regex `[0-9a-f]{8}-...-[0-9a-f]{12}\.0`
- BSL спрятан как **строка** внутри 1C-кортежа — **3-й элемент** (index 2)
  корневого кортежа
- `split_file` извлекает строку, убирает `""` → `"`, пишет `<companion>.bsl`,
  заменяет строку на `"<BSL_MODULE_PLACEHOLDER>"`

### Файлы `module` и `text`

- Если содержимое **не** выглядит как кортеж (`{ … }`) → считается **plain BSL**
- Файл целиком копируется в `.bsl`, оригинал заменяется однострочным placeholder

### ❌ Файл `form`

**Обычные формы** — файл `form` содержит **разметку/метаданные**, **НЕ** текст
модуля. `split_file` **не обрабатывает** файл `form` как BSL, даже если внутри
есть текст процедур. Это специфика `bsl-forms.mdc`.

### ❌ UUID без `.0`

Чистые UUID-файлы (без суффикса `.0`) — не содержат модуля, не трогать.

```
UUID           ← дескриптор (имя формы) — не BSL
UUID.0         ← управляемая форма — BSL внутри кортежа
UUID.0/form    ← обычная форма — разметка, НЕ BSL
UUID.0/module  ← модуль обычной формы — plain BSL
UUID.0/text    ← объектный модуль — plain BSL
```

## Структура `meta/` + `bin/` после split_dir

```
output_dir/
  0_ОбъектName.bsl          ← объектный модуль (из text)
  1_ФормаName.bsl           ← модуль формы (из UUID.0 или module)
  meta/
    bsl_renames.txt         ← .bsl → companion path map
    renames.txt             ← target → bin/... для v8unpack rebuild
  bin/
    ...все остальные файлы...
```

`_apply_bin_layout` перемещает всё (кроме `meta/`, `bin/`, корневых `*.bsl`) в
`bin/` и перезаписывает оба `meta/*.txt`.

## Round-trip: сборка обратно

```
Builder.run(output_dir/)
  → prepare_temp_for_build:
      copy bin/ → flat tree (per meta/renames.txt)
      copy root *.bsl
      rewrite meta/bsl_renames.txt (убрать bin/ prefix)
      merge_dir (BSL → placeholder замены обратно)
      delete temp bsl_renames
  → v8unpack -B temp_dir result.epf
  → backup старого result.epf (опционально)
```

## Структура тестов

```
tests/
  test_parse.py    # CLI parse → bin/root существует
  test_build.py    # Roundtrip: parse → build → parse --raw, побайтовое сравнение
  test_bsl.py      # split_file / merge_file edge cases, split_dir/merge_dir, meta/ invariants
  test_base.py     # Processor / settings failures
  fixtures/                   # committed sample fixtures
    test.epf
    test_epf_src/
  local-fixtures/             # gitignored local samples (e.g. CF/CFE)
```

## Практические заметки

- **`--raw`:** только `v8unpack -P`, без `split_dir` — кортежные файлы остаются
  нетронутыми
- **Кодировка:** читает с BOM → `utf-8-sig`; пишет через `write_bytes` чтобы не
  менять переводы строк
- **Merge:** ищет `"<BSL_MODULE_PLACEHOLDER>"` в кортеже; если нет — первую
  пустую `""` (пустые модули)
- **`_move_to_bin_with_retry`:** обход `PermissionError` при перемещении файлов
  на Windows
- **Нет AST-модели:** «parsed state» — это файловая система; никакого in-memory
  графа объектов 1C нет
