# Grimoire Switch

Grimoire Switch is a local utility that switches Codex between configured providers while rewriting active local thread ownership so the current provider shows the union of both sides.

## Files

- Launcher: `/Users/handong/GithubProduct/Grimoire Switch/scripts/grimoire-switch.command`
- Helper: `/Users/handong/GithubProduct/Grimoire Switch/scripts/grimoire_switch.py`

## Usage

```bash
grimoire-switch <provider-name>
grimoire-switch <provider-name> --dry-run
grimoire-switch --restore /absolute/path/to/backup-dir
```

If you are working directly inside this repository, the local launcher is still available:

```bash
./scripts/grimoire-switch.command <provider-name>
```

`/absolute/path/to/backup-dir` is a placeholder. Replace it with a real directory under `~/.codex/provider-switch-backups/`, for example `~/.codex/provider-switch-backups/20260410T052825Z`.

## What It Changes

1. Verifies the target provider is available for Codex.
2. Backs up `~/.codex/config.toml`, `state_5.sqlite`, `state_5.sqlite-wal`, `state_5.sqlite-shm`, and every active thread rollout file that may be rewritten.
3. Rewrites the top-level `model_provider` in `config.toml`.
4. Rewrites `threads.model_provider` for all non-archived threads in `~/.codex/state_5.sqlite`.
5. Updates the first `session_meta` record in each active thread rollout file so Codex sees the new provider consistently.
6. Removes old encrypted reasoning blocks from threads that are being moved across providers, preventing `invalid_encrypted_content` when the new provider resumes the thread.
7. Restarts Codex after switching or restoring.

## Backup And Restore

Backups are created under:

```text
~/.codex/provider-switch-backups/<timestamp>/
```

Each backup contains a `backup-manifest.json` file with the target provider, affected thread ids, original provider map, rollout paths, rollout backup paths, and archived thread count.

Use the restore command if a switch fails or if you want to go back to a prior local Codex state.

## Known Risks

- This is a local Codex state workaround, not an officially supported Codex feature.
- It intentionally edits `~/.codex` internals and may need updates after Codex schema or rollout format changes.
- Archived threads are left untouched on purpose.
- `openai` is treated as a built-in provider even if it is not declared in `config.toml`, because Codex can still use ChatGPT auth without an explicit `[model_providers.openai]` section.
- Other providers are supported as long as they are already declared in `~/.codex/config.toml`.
- Recent Codex builds may delete the top-level `model_provider = "openai"` line after relaunch; the switcher now reinserts the line when needed before switching back to `azure`.
- Cross-provider migration keeps visible chat history but drops old encrypted reasoning payloads, because those payloads are provider-bound and cannot be resumed safely after the switch.
