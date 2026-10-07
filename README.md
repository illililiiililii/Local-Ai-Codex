# Local Codex AI (Windows x64)

Clone or copy the source repository to a Windows x64 computer with PowerShell available, then double-click `Start-Codex.cmd`. The launcher installs missing Node.js LTS, Codex CLI, Ollama, and project dependencies automatically. The selected Ollama model is downloaded on first use. Internet access is required; model downloads can use several gigabytes of disk space.

At startup, choose:

- **Normal mode** to check/install requirements and open the project menu. Normal launches keep Codex approval and sandbox protections enabled.
- **Install mode** to install/repair requirements and the selected model without starting Codex.
- **Force mode** to install requirements and request a launch with approval prompts and sandbox protections disabled. You must type `FORCE` to confirm.

The project menu can clone a GitHub repository, select/resume a project, change the model, or explicitly force-start a workspace. The model menu offers recommended models and accepts any Ollama model name. The default recommendation is `gpt-oss:20b`; available memory, speed, and quality vary by model and hardware. Model choice is saved per Windows user under `%LOCALAPPDATA%\LocalCodexAI`.

Runtime downloads and local projects are excluded from Git by `.gitignore`; a fresh clone reinstalls them. Codex conversation history remains in the current Windows user's Codex data directory. On an error, details are written to `bootstrap-error.log`.
