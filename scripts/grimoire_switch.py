#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence, Set


PROGRAM_NAME = "grimoire-switch"
VERSION = "v0.1.0"
APP_BUNDLE_ID = "com.openai.codex"
CONFIG_FILENAME = "config.toml"
STATE_DB_FILENAME = "state_5.sqlite"
STATE_DB_AUX_FILENAMES = ("state_5.sqlite-wal", "state_5.sqlite-shm")
BACKUP_ROOT_NAME = "provider-switch-backups"
MANIFEST_FILENAME = "backup-manifest.json"
ROLLOUT_BACKUP_DIRNAME = "rollouts"
MODEL_PROVIDER_LINE = re.compile(r'^(model_provider\s*=\s*)"(.*)"(\s*)$')
PROVIDER_SECTION_LINE = re.compile(r"^\[model_providers\.([A-Za-z0-9_-]+)\]\s*$")


class SwitcherError(RuntimeError):
    pass


@dataclass(frozen=True)
class ThreadRecord:
    thread_id: str
    rollout_path: str
    original_provider: str
    title: str


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grimoire Switch: switch Codex between providers while keeping active threads visible."
    )
    parser.add_argument("target", nargs="?", choices=("openai", "azure"))
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROGRAM_NAME} {VERSION}",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the actions without modifying files.")
    parser.add_argument(
        "--thread-id",
        help="Only switch the specified active thread ID. Other active threads keep their current provider.",
    )
    parser.add_argument(
        "--restore",
        metavar="BACKUP_DIR",
        help="Restore Codex state from a backup directory created by Grimoire Switch.",
    )
    parser.add_argument(
        "--codex-home",
        default=str(Path.home() / ".codex"),
        help=argparse.SUPPRESS,
    )

    args = parser.parse_args(argv)
    if args.restore and args.target:
        parser.error("target and --restore cannot be used together")
    if args.restore and args.thread_id:
        parser.error("--thread-id cannot be used with --restore")
    if not args.restore and not args.target:
        parser.error("either a target provider or --restore is required")
    return args


def get_configured_provider_names(config_path: Path) -> Set[str]:
    provider_names: Set[str] = set()
    for line in config_path.read_text(encoding="utf-8").splitlines():
        match = PROVIDER_SECTION_LINE.match(line.strip())
        if match:
            provider_names.add(match.group(1))
    current_provider = read_top_level_model_provider(config_path, required=False)
    if current_provider:
        provider_names.add(current_provider)
    provider_names.add("openai")
    return provider_names


def read_top_level_model_provider(config_path: Path, required: bool = True) -> str | None:
    for line in config_path.read_text(encoding="utf-8").splitlines():
        match = MODEL_PROVIDER_LINE.match(line)
        if match:
            return match.group(2)
    if required:
        raise SwitcherError(f"Could not find a top-level model_provider entry in {config_path}")
    return None


def rewrite_top_level_model_provider(config_path: Path, target_provider: str) -> None:
    original_text = config_path.read_text(encoding="utf-8")
    lines = original_text.splitlines()
    replaced = False
    rewritten: List[str] = []
    for line in lines:
        match = MODEL_PROVIDER_LINE.match(line)
        if match and not replaced:
            rewritten.append(f'{match.group(1)}"{target_provider}"{match.group(3)}')
            replaced = True
            continue
        rewritten.append(line)

    if not replaced:
        insert_index = len(lines)
        for index, line in enumerate(lines):
            if line.lstrip().startswith("["):
                insert_index = index
                while insert_index > 0 and lines[insert_index - 1].strip() == "":
                    insert_index -= 1
                break

        rewritten = lines[:insert_index]
        rewritten.append(f'model_provider = "{target_provider}"')
        if insert_index < len(lines):
            rewritten.append("")
        rewritten.extend(lines[insert_index:])

    new_text = "\n".join(rewritten)
    if original_text.endswith("\n"):
        new_text += "\n"
    config_path.write_text(new_text, encoding="utf-8")


def validate_target_provider_available(config_path: Path, target_provider: str) -> None:
    provider_names = get_configured_provider_names(config_path)
    if target_provider not in provider_names:
        raise SwitcherError(
            f"Provider '{target_provider}' is not available in {config_path}. "
            f"Available providers: {', '.join(sorted(provider_names))}"
        )


def read_launchd_environment(name: str) -> str | None:
    result = subprocess.run(
        ["launchctl", "getenv", name],
        check=False,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        return None
    return value


def validate_target_environment(target_provider: str, env: Mapping[str, str]) -> None:
    if target_provider == "azure" and not env.get("AZURE_OPENAI_API_KEY") and not read_launchd_environment(
        "AZURE_OPENAI_API_KEY"
    ):
        raise SwitcherError("Missing environment variable: AZURE_OPENAI_API_KEY")


def ensure_thread_schema(db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("PRAGMA table_info(threads)").fetchall()
    columns = {row[1] for row in rows}
    required = {"id", "rollout_path", "model_provider", "title", "archived"}
    missing = sorted(required - columns)
    if missing:
        raise SwitcherError(
            f"Unexpected threads schema in {db_path}; missing columns: {', '.join(missing)}"
        )


def load_switchable_threads(db_path: Path) -> List[ThreadRecord]:
    ensure_thread_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, rollout_path, model_provider, title
            FROM threads
            WHERE archived = 0
            ORDER BY created_at, id
            """
        ).fetchall()
    return [
        ThreadRecord(
            thread_id=row[0],
            rollout_path=row[1],
            original_provider=row[2],
            title=row[3],
        )
        for row in rows
    ]


def select_threads_for_switch(
    db_path: Path,
    threads: Sequence[ThreadRecord],
    thread_id: str | None,
) -> List[ThreadRecord]:
    if not thread_id:
        return list(threads)

    selected = [thread for thread in threads if thread.thread_id == thread_id]
    if selected:
        return selected

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT title, archived FROM threads WHERE id = ?",
            (thread_id,),
        ).fetchone()

    if row:
        title, archived = row
        if int(archived or 0) != 0:
            raise SwitcherError(
                f"Thread '{thread_id}' is archived and cannot be switched with this command: {title}"
            )
        raise SwitcherError(
            f"Thread '{thread_id}' exists but is not currently switchable: {title}"
        )

    active_thread_text = format_thread_summary(threads)
    raise SwitcherError(
        f"Active thread not found: {thread_id}\n"
        f"Available active threads:\n{active_thread_text}"
    )


def count_archived_threads(db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM threads WHERE archived != 0").fetchone()
    return int(row[0] if row else 0)


def rewrite_thread_providers(
    db_path: Path,
    target_provider: str,
    thread_ids: Sequence[str] | None = None,
) -> int:
    ensure_thread_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        if thread_ids is None:
            row = connection.execute("SELECT COUNT(*) FROM threads WHERE archived = 0").fetchone()
            count = int(row[0] if row else 0)
            connection.execute(
                "UPDATE threads SET model_provider = ? WHERE archived = 0",
                (target_provider,),
            )
        else:
            selected_ids = list(thread_ids)
            if not selected_ids:
                return 0

            placeholders = ", ".join("?" for _ in selected_ids)
            row = connection.execute(
                f"SELECT COUNT(*) FROM threads WHERE archived = 0 AND id IN ({placeholders})",
                selected_ids,
            ).fetchone()
            count = int(row[0] if row else 0)
            connection.execute(
                f"UPDATE threads SET model_provider = ? WHERE archived = 0 AND id IN ({placeholders})",
                [target_provider, *selected_ids],
            )
        connection.commit()
    return count


def rewrite_active_thread_providers(db_path: Path, target_provider: str) -> int:
    return rewrite_thread_providers(db_path, target_provider)


def patch_rollout_session_meta(
    rollout_path: Path,
    target_provider: str,
    scrub_encrypted_reasoning: bool = False,
) -> bool:
    if not rollout_path.exists():
        raise SwitcherError(f"Rollout file does not exist: {rollout_path}")

    lines = rollout_path.read_text(encoding="utf-8").splitlines()
    header_found = False
    changed = False
    rewritten_lines: List[str] = []

    for line in lines:
        payload = json.loads(line)

        if not header_found:
            if payload.get("type") == "session_meta":
                session_payload = payload.setdefault("payload", {})
                if session_payload.get("model_provider") != target_provider:
                    session_payload["model_provider"] = target_provider
                    line = json.dumps(payload, ensure_ascii=False)
                    changed = True
                header_found = True

        if scrub_encrypted_reasoning:
            response_payload = payload.get("payload", {})
            if payload.get("type") == "response_item" and response_payload.get("type") == "reasoning":
                if response_payload.get("encrypted_content"):
                    changed = True
                    continue

        rewritten_lines.append(line)

    if not header_found:
        raise SwitcherError(f"No session_meta header found in {rollout_path}")

    if not changed:
        return False

    rollout_path.write_text("\n".join(rewritten_lines) + "\n", encoding="utf-8")
    return True


def backup_rollout_files(backup_dir: Path, threads: Sequence[ThreadRecord]) -> Mapping[str, str]:
    rollout_backup_dir = backup_dir / ROLLOUT_BACKUP_DIRNAME
    rollout_backup_dir.mkdir(parents=True, exist_ok=True)

    backup_paths: dict[str, str] = {}
    for thread in threads:
        source = Path(thread.rollout_path)
        suffix = "".join(source.suffixes) or ".jsonl"
        backup_path = rollout_backup_dir / f"{thread.thread_id}{suffix}"
        shutil.copy2(source, backup_path)
        backup_paths[thread.thread_id] = str(backup_path.relative_to(backup_dir))
    return backup_paths


def write_backup_manifest(
    backup_dir: Path,
    target_provider: str,
    threads: Sequence[ThreadRecord],
    archived_count: int,
    rollout_backup_paths: Mapping[str, str] | None = None,
) -> Path:
    manifest_path = backup_dir / MANIFEST_FILENAME
    manifest = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_provider": target_provider,
        "affected_threads": [thread.thread_id for thread in threads],
        "original_provider_map": {
            thread.thread_id: thread.original_provider for thread in threads
        },
        "rollout_paths": {thread.thread_id: thread.rollout_path for thread in threads},
        "archived_count": archived_count,
    }
    if rollout_backup_paths:
        manifest["rollout_backup_paths"] = dict(rollout_backup_paths)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest_path


def restore_backup_files(backup_dir: Path, codex_home: Path) -> List[Path]:
    files_to_restore = [CONFIG_FILENAME, STATE_DB_FILENAME, *STATE_DB_AUX_FILENAMES]
    restored: List[Path] = []
    codex_home.mkdir(parents=True, exist_ok=True)

    for filename in files_to_restore:
        source = backup_dir / filename
        if not source.exists():
            continue
        destination = codex_home / filename
        shutil.copy2(source, destination)
        restored.append(destination)

    if not restored:
        raise SwitcherError(f"No backup files found in {backup_dir}")

    manifest_path = backup_dir / MANIFEST_FILENAME
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rollout_paths = manifest.get("rollout_paths", {})
        rollout_backup_paths = manifest.get("rollout_backup_paths", {})
        for thread_id, destination_path in rollout_paths.items():
            backup_relpath = rollout_backup_paths.get(thread_id)
            if not backup_relpath:
                continue
            source = backup_dir / backup_relpath
            destination = Path(destination_path)
            if not source.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            restored.append(destination)
    return restored


def list_available_backups(codex_home: Path) -> List[Path]:
    backup_root = codex_home / BACKUP_ROOT_NAME
    if not backup_root.exists():
        return []
    return sorted((path for path in backup_root.iterdir() if path.is_dir()), reverse=True)


def ensure_rollouts_exist(threads: Sequence[ThreadRecord]) -> None:
    missing = [thread.rollout_path for thread in threads if not Path(thread.rollout_path).exists()]
    if missing:
        raise SwitcherError(
            "Missing rollout files:\n" + "\n".join(f"- {path}" for path in missing)
        )


def backup_codex_state(
    codex_home: Path,
    target_provider: str,
    threads: Sequence[ThreadRecord],
    archived_count: int,
) -> Path:
    backup_root = codex_home / BACKUP_ROOT_NAME
    backup_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = backup_root / timestamp
    index = 1
    while backup_dir.exists():
        backup_dir = backup_root / f"{timestamp}-{index}"
        index += 1
    backup_dir.mkdir(parents=True, exist_ok=False)

    files_to_copy = [CONFIG_FILENAME, STATE_DB_FILENAME, *STATE_DB_AUX_FILENAMES]
    copied_any = False
    for filename in files_to_copy:
        source = codex_home / filename
        if not source.exists():
            if filename in (CONFIG_FILENAME, STATE_DB_FILENAME):
                raise SwitcherError(f"Required file does not exist: {source}")
            continue
        shutil.copy2(source, backup_dir / filename)
        copied_any = True

    if not copied_any:
        raise SwitcherError(f"No files were copied into {backup_dir}")

    rollout_backup_paths = backup_rollout_files(backup_dir, threads)
    write_backup_manifest(
        backup_dir,
        target_provider,
        threads,
        archived_count,
        rollout_backup_paths=rollout_backup_paths,
    )
    return backup_dir


def detect_codex_app() -> None:
    result = subprocess.run(
        ["osascript", "-e", 'id of app "Codex"'],
        check=False,
        capture_output=True,
        text=True,
    )
    bundle_id = result.stdout.strip()
    if result.returncode != 0 or bundle_id != APP_BUNDLE_ID:
        raise SwitcherError(
            f"Could not detect Codex app with bundle id {APP_BUNDLE_ID}"
        )


def gracefully_quit_codex() -> None:
    subprocess.run(
        ["osascript", "-e", f'tell application id "{APP_BUNDLE_ID}" to quit'],
        check=False,
        capture_output=True,
        text=True,
    )
    time.sleep(1.5)


def relaunch_codex() -> None:
    subprocess.run(["open", "-b", APP_BUNDLE_ID], check=True)


def format_thread_summary(threads: Sequence[ThreadRecord]) -> str:
    if not threads:
        return "  (no active threads found)"
    return "\n".join(
        f"  - {thread.thread_id} [{thread.original_provider}] {thread.title}"
        for thread in threads
    )


def perform_switch(
    target_provider: str,
    codex_home: Path,
    dry_run: bool,
    thread_id: str | None = None,
) -> int:
    config_path = codex_home / CONFIG_FILENAME
    db_path = codex_home / STATE_DB_FILENAME

    if not config_path.exists():
        raise SwitcherError(f"Missing config file: {config_path}")
    if not db_path.exists():
        raise SwitcherError(f"Missing state database: {db_path}")

    validate_target_provider_available(config_path, target_provider)
    validate_target_environment(target_provider, os.environ)
    ensure_thread_schema(db_path)

    current_provider = read_top_level_model_provider(config_path, required=False) or "(unspecified)"
    switchable_threads = load_switchable_threads(db_path)
    archived_count = count_archived_threads(db_path)
    threads = select_threads_for_switch(db_path, switchable_threads, thread_id)

    other_active_count = len(switchable_threads) - len(threads)
    ensure_rollouts_exist(threads)

    if dry_run:
        print(f"Dry run: {current_provider} -> {target_provider}")
        if thread_id:
            print(f"Selected active thread: {thread_id}")
        print(f"Active threads to switch: {len(threads)}")
        if thread_id:
            print(f"Other active threads left unchanged: {other_active_count}")
        print(f"Archived threads left unchanged: {archived_count}")
        print("Threads:")
        print(format_thread_summary(threads))
        return 0

    detect_codex_app()
    gracefully_quit_codex()
    backup_dir = backup_codex_state(codex_home, target_provider, threads, archived_count)

    try:
        rewrite_top_level_model_provider(config_path, target_provider)
        switched_count = rewrite_thread_providers(
            db_path,
            target_provider,
            [thread.thread_id for thread in threads] if thread_id else None,
        )
        for thread in threads:
            patch_rollout_session_meta(
                Path(thread.rollout_path),
                target_provider,
                scrub_encrypted_reasoning=thread.original_provider != target_provider,
            )
    except Exception as exc:
        restore_backup_files(backup_dir, codex_home)
        raise SwitcherError(
            f"Switch failed and the original files were restored from {backup_dir}: {exc}"
        ) from exc

    relaunch_codex()

    print(f"Switched Codex provider: {current_provider} -> {target_provider}")
    print(f"Switched active threads: {switched_count}")
    if thread_id:
        print(f"Other active threads left unchanged: {other_active_count}")
    print(f"Archived threads left unchanged: {archived_count}")
    print(f"Backup created at: {backup_dir}")
    return 0


def perform_restore(backup_dir: Path, codex_home: Path) -> int:
    if not backup_dir.exists() or not backup_dir.is_dir():
        available_backups = list_available_backups(codex_home)
        if available_backups:
            available_text = "\n".join(f"- {path}" for path in available_backups)
            raise SwitcherError(
                f"Backup directory does not exist: {backup_dir}\n"
                f"Available backups:\n{available_text}"
            )
        raise SwitcherError(f"Backup directory does not exist: {backup_dir}")

    detect_codex_app()
    gracefully_quit_codex()
    restored = restore_backup_files(backup_dir, codex_home)
    relaunch_codex()

    print(f"Restored Codex state from: {backup_dir}")
    print("Restored files:")
    for path in restored:
        print(f"  - {path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    codex_home = Path(args.codex_home).expanduser().resolve()

    try:
        if args.restore:
            return perform_restore(Path(args.restore).expanduser().resolve(), codex_home)
        return perform_switch(args.target, codex_home, args.dry_run, args.thread_id)
    except SwitcherError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"Error: command failed with exit code {exc.returncode}: {exc.cmd}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
