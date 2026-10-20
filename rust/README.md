# Optional native acceleration (maturin / PyO3)

Crate: `rust/p1cb_native` (Python module `p1cb_native`).

## Build into the project venv

```powershell
pdm run -p .dev build-native-rust
```

Uses `maturin develop --release --uv` (PDM/uv venv has no pip).

## Force pure Python

```powershell
$env:P1CB_RUST_FORCE_PYTHON = '1'
```

## What is ported

- `organize_configuration_dir` — CF/CFE flat dump → `objects/Class/Name` + root
  BSL layout
  (discovery, moves, BSL extract including managed-form tuple scanner, meta
  writes)

## Profiling

```powershell
# Full parse (v8unpack + organize)
.\scripts\run.ps1 -TimeoutSec 21600 -Command 'pdm run -p .dev python scripts/profile_cf_parse.py --in "..." --out "..." --json-out "..."'

# Build prepare vs pack
.\scripts\run.ps1 -TimeoutSec 21600 -Command 'pdm run -p .dev python scripts/profile_cf_build.py --in "..." --out "..." --json-out "..."'

# Organize-only (raw dump)
.\scripts\run.ps1 -TimeoutSec 7200 -Command 'pdm run -p .dev python scripts/profile_cf_organize.py --in "..." --json-out "..."'
```
