# Development

## Source layout

The Python desktop application lives in `desktop/`, with its Electron interface in `desktop/ui/`. The local model service and the speech voice live in `phone/`. Shared personality instructions are in `prompts/SOUL.md`.

`scripts/` contains setup and service-management helpers. The two primary launchers stay at the repository root; the remaining Windows commands are grouped in `scripts/windows/`. Each command resolves the repository location from its own file path.

## Automated tests

From a configured checkout, run:

```powershell
.\scripts\python.cmd scripts\run-tests.py
```

The runner discovers the Python tests in `tests/`, then runs the JavaScript tests with the bundled Node executable or Node on `PATH`.

To run an individual Python test module:

```powershell
.\scripts\python.cmd -m unittest tests.desktop.test_semantic_tools
```

## Live verification

`tests/verification/` contains checks that need the configured local runtime, an interactive Windows session, or live model access. Run a script only when its required services and devices are available.

Examples:

```powershell
.\scripts\python.cmd tests\verification\verify-semantic-routing.py
.\scripts\python.cmd tests\verification\verify-semantic-tools.py
.\scripts\python.cmd tests\verification\verify-app-closing.py
```

The semantic-tool and app-closing checks use disposable windows. Audio and app-launch adapters are mocked in the semantic-tool check. Speech checks exercise the configured recognizers and audio capture separately.

## Local configuration

Use the service-specific example files as templates. The local `.runtime/`, `.venv/`, `.openclaw/`, `.secrets/`, `.cache/`, and `logs/` directories are excluded from Git, as are personal settings, recordings, and generated dependencies.

Keep explanatory documentation in `docs/`. Project source code is maintained without comments.
