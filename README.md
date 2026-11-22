# Jarvis

An AI assistant for Windows, powered by OpenClaw, with a computer use tool. Jarvis is voice activated, lives in a floating desktop orb, picks the tools a request needs, and reports only results he has verified.

Start a spoken request with **“Jarvis”**. Wake-word listening runs while the assistant is visible and unmuted, and every spoken follow-up requires the wake word too.

## What it does

- Answers questions and researches information with the models available to your account.
- Operates any Windows app through a computer use tool: it reads fresh screenshots and the app's accessibility tree, and clicks and types with its own background pointer, so you keep your mouse and keyboard. It works across multiple monitors.
- Shows background task progress and reports verified actions and failures.
- Provides tools for browser tasks, local files, reminders, projects, drawings, and system controls.

## Start Jarvis

This is a **Windows source repository**. It does not include a complete clean-machine installer. Running it requires the local Python environment, Node/OpenClaw runtime, speech models, and your own account configuration described in the [user guide](docs/user-guide.md#source-checkout).

For an already configured checkout:

1. Run [setup-jarvis.cmd](setup-jarvis.cmd) once to enable Windows startup and launch Jarvis.
2. Run [start-jarvis.cmd](start-jarvis.cmd) for subsequent launches.
3. Say **“Jarvis, …”** followed by your request.

Run the launchers from your normal Windows desktop so the assistant can use the microphone and interact with your applications.

## Repository layout

| Location | Contents |
| --- | --- |
| [desktop/](desktop/) | Assistant logic, voice activation, desktop tools, and the Electron interface. |
| [phone/](phone/) | The local model service and the speech voice. |
| [prompts/](prompts/) | Shared assistant personality and behavior. |
| [scripts/](scripts/) | Setup, service management, and the test runner. |
| [scripts/windows/](scripts/windows/) | Windows commands for status, startup, and local features. |
| [tests/](tests/) | Automated tests. |
| [tests/verification/](tests/verification/) | Live integration checks and disposable-window verification scripts. |
| [docs/](docs/) | User, development, and email setup guides. |

The root keeps only the README, Git settings, and the two main launchers. Downloaded runtimes, credentials, recordings, logs, saved projects, and machine-specific settings stay local and are ignored by Git.

## Useful commands

Run these from the repository root:

```powershell
.\scripts\windows\jarvis-status.cmd
.\scripts\python.cmd scripts\run-tests.py
```

| Task | Command |
| --- | --- |
| Stop the desktop assistant | `scripts\windows\stop-jarvis.cmd` |
| Enable Windows startup | `scripts\windows\enable-startup.cmd` |
| Disable Windows startup | `scripts\windows\disable-startup.cmd` |
| Install local document and speech dependencies | `scripts\windows\setup-local-features.cmd` |

## Documentation

- [User guide](docs/user-guide.md): voice behavior, controls, tools, settings, and troubleshooting.
- [Development](docs/development.md): source layout, tests, and verification.
- [Email setup](docs/email-setup.md): account instructions for when email access is enabled.

Example configuration files live beside their services. Real credentials and personal configuration belong in the ignored local files; they are not part of this repository.
