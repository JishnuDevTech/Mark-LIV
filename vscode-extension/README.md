# JARVIS Native VS Code Extension

This extension is a native VS Code client for the existing JARVIS dashboard HTTP bridge. It does **not** start another assistant or backend. It calls `/api/state`, `/api/health`, `/api/controls`, and `/api/command` and reports offline status when the bridge cannot be reached.

## Manual setup

1. Start the existing JARVIS core/dashboard so it listens on `http://127.0.0.1:8000`.
2. Obtain a dashboard bearer token using the existing dashboard pairing/login flow. Do not commit the token.
3. Open this `vscode-extension` folder in VS Code.
4. Run `npm install`, then `npm run compile`.
5. Press `F5` (Run Extension) from the extension folder, or install a VSIX produced by your preferred VS Code packaging tool.
6. In VS Code Settings, set **JARVIS: Core Endpoint** if the bridge is not on the default URL and set **JARVIS: Auth Token** to the bearer token.
7. Open the JARVIS activity-bar icon and select **Assistant**. The sidebar displays only state/events actually returned by the bridge; offline means no bridge response was received.

## Commands

Use Command Palette and search `JARVIS:` for workspace analysis, selection/file/change review, diagnostics fixes, tests/build, project context, task continuation/stop, and assistant opening. File/workspace/diagnostic/Git context is gathered through native VS Code APIs and sent concisely with each command.

## Development

- `npm run compile` performs strict TypeScript validation.
- `npm run watch` watches the source.
- Source is split into lifecycle (`src/extension.ts`), HTTP bridge, context provider, incremental indexer, commands, and webview modules.
