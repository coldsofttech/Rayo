# UI

The desktop window is hosted by [Cornea Shell](https://github.com/coldsofttech/CorneaShell) (`cornea-shell`). It runs on Windows only; the library, command line and API work on every system.

## Static files, dynamic app

Cornea serves the built files in `src/example_ui/ui/dist`, but the page is a normal single-page app that talks to Python while it runs. `ui/` holds only the interface; everything dynamic lives in `example_api` and `example_package`:

- **Data:** `fetch('/api/v1/...')` calls the `example_api` routes and gets JSON.
- **Native actions:** `POST /api/bridge/<action>` runs a Python function registered in `example_ui/app.py`. The example is `app_info`. Cornea also provides file and folder pickers, opening links and platform info.
- **State:** lives in React in the page, and in Python behind the routes.

Cornea hosts the page and the API on one address, behind a start-up token that becomes a session cookie, a host and origin check, and strict response headers. See [security](security.md).

## Layout

```text
src/example_ui/__main__.py   the `example-ui` command: options, output, exit code
src/example_ui/app.py        builds the Cornea shell: serves dist, mounts the API, registers actions
src/example_ui/ui/           React + Vite (JavaScript): src/, index.html, package.json, tests
src/example_ui/ui/dist/      build output, not committed; frozen into the Windows build
```

`app.py` only wires things together. It must not hold business logic, and `example_api` and `example_package` must never import `example_ui`.

## Running it

```bash
uv sync
npm ci --prefix src/example_ui/ui
npm run build --prefix src/example_ui/ui
uv run python -m example_ui                 # window
uv run python -m example_ui --no-window     # prints an address for a browser
uv run python -m example_ui --dev           # Vite with hot reload (needs Node)
```

`--dev` starts `npm run dev` through Cornea and relaxes the content security policy for Vite. Use it only while developing.

## Writing the frontend

- Plain JavaScript with JSX. Call Python only through `src/api.js`.
- Cornea's policy forbids inline scripts and styles, so put styles in CSS files.
- Add UI kits such as Fluent UI as optional dependencies when you need them; none is included.
- `npm run lint`, `npm test` (Vitest, Testing Library and an accessibility check) and `npm run build` run in CI.

## Packaging

The window is not part of the Python package: `uv build` publishes only the core library and the command line. Users get the window from the Windows installer, which freezes `ui/dist` into the build (`installer/pyinstaller/ui.spec`) and needs no Node. The `example-ui` command exists in that build; from the source tree you start it with `python -m example_ui`.

The window and taskbar icon is `src/example_ui/ui/public/icon.png` (copied into `dist` by the build), and the app id, title and version are constants at the top of `example_ui/app.py`. The Windows setup and the standalone executables are described in [packaging](packaging.md).

## Tests

The Python tests in `tests/ui/` replace Cornea's `Shell`, so no window opens. In `tests/ui/integration/`, a Windows-only test starts the real server without a window and needs a built `dist`, and another starts the built `example-ui.exe` (see [packaging](packaging.md)).
