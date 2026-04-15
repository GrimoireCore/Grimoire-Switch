from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
INSTALL_SCRIPT = PROJECT_ROOT / "install.sh"
sys.path.insert(0, str(SCRIPTS_DIR))

import grimoire_switch as cps  # noqa: E402


THREADS_SCHEMA = """
CREATE TABLE threads (
    id TEXT PRIMARY KEY,
    rollout_path TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    source TEXT NOT NULL,
    model_provider TEXT NOT NULL,
    cwd TEXT NOT NULL,
    title TEXT NOT NULL,
    sandbox_policy TEXT NOT NULL,
    approval_mode TEXT NOT NULL,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    has_user_event INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    archived_at INTEGER,
    git_sha TEXT,
    git_branch TEXT,
    git_origin_url TEXT,
    cli_version TEXT NOT NULL DEFAULT '',
    first_user_message TEXT NOT NULL DEFAULT '',
    agent_nickname TEXT,
    agent_role TEXT,
    memory_mode TEXT NOT NULL DEFAULT 'enabled',
    model TEXT,
    reasoning_effort TEXT,
    agent_path TEXT
);
"""


class GrimoireSwitchTests(unittest.TestCase):
    def test_cli_reports_public_version(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "grimoire_switch.py"), "--version"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "grimoire-switch v0.1.1")

    def test_parse_args_accepts_thread_id(self) -> None:
        args = cps.parse_args(["azure", "--thread-id", "thread-openai"])

        self.assertEqual(args.target, "azure")
        self.assertEqual(args.thread_id, "thread-openai")

    def test_get_configured_provider_names_and_rewrite_top_level_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(
                '\n'.join(
                    [
                        'model = "gpt-5.4"',
                        'model_provider = "azure"',
                        "",
                        "[model_providers.azure]",
                        'name = "Azure"',
                        "",
                        "[model_providers.openai]",
                        'name = "OpenAI"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            provider_names = cps.get_configured_provider_names(config_path)
            self.assertEqual(provider_names, {"azure", "openai"})

            cps.rewrite_top_level_model_provider(config_path, "openai")
            rewritten = config_path.read_text(encoding="utf-8")

            self.assertIn('model_provider = "openai"', rewritten)
            self.assertIn("[model_providers.azure]", rewritten)
            self.assertIn("[model_providers.openai]", rewritten)

    def test_builtin_openai_provider_is_allowed_without_explicit_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(
                '\n'.join(
                    [
                        'model = "gpt-5.4"',
                        'model_provider = "azure"',
                        "",
                        "[model_providers.azure]",
                        'name = "Azure"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            cps.validate_target_provider_available(config_path, "openai")
            cps.validate_target_provider_available(config_path, "azure")

            with self.assertRaises(cps.SwitcherError):
                cps.validate_target_provider_available(config_path, "unknown")

    def test_rewrite_top_level_provider_inserts_missing_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(
                '\n'.join(
                    [
                        'model = "gpt-5.4"',
                        'model_reasoning_effort = "xhigh"',
                        "",
                        "[features]",
                        "multi_agent = true",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            cps.rewrite_top_level_model_provider(config_path, "azure")

            rewritten = config_path.read_text(encoding="utf-8")
            self.assertIn('model_provider = "azure"', rewritten)
            self.assertEqual(cps.read_top_level_model_provider(config_path), "azure")

    def test_require_azure_key_for_target(self) -> None:
        cps.validate_target_environment("openai", {})

        with mock.patch.object(cps, "read_launchd_environment", return_value=None):
            with self.assertRaises(cps.SwitcherError):
                cps.validate_target_environment("azure", {})

        cps.validate_target_environment("azure", {"AZURE_OPENAI_API_KEY": "set"})

    def test_require_azure_key_accepts_launchctl_fallback(self) -> None:
        with mock.patch.object(cps, "read_launchd_environment", return_value="set-from-launchctl"):
            cps.validate_target_environment("azure", {})

    def test_collect_switchable_threads_skips_archived_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.sqlite"
            self._write_thread_db(db_path)

            threads = cps.load_switchable_threads(db_path)

            self.assertEqual([thread.thread_id for thread in threads], ["thread-openai", "thread-azure"])
            self.assertEqual([thread.original_provider for thread in threads], ["openai", "azure"])

    def test_select_threads_for_switch_returns_requested_active_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.sqlite"
            self._write_thread_db(db_path)

            threads = cps.load_switchable_threads(db_path)
            selected = cps.select_threads_for_switch(db_path, threads, "thread-azure")

            self.assertEqual([thread.thread_id for thread in selected], ["thread-azure"])

    def test_select_threads_for_switch_rejects_archived_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.sqlite"
            self._write_thread_db(db_path)

            threads = cps.load_switchable_threads(db_path)

            with self.assertRaises(cps.SwitcherError) as context:
                cps.select_threads_for_switch(db_path, threads, "thread-archived")

            self.assertIn("archived", str(context.exception))

    def test_rewrite_active_thread_providers_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.sqlite"
            self._write_thread_db(db_path)

            switched = cps.rewrite_active_thread_providers(db_path, "azure")

            self.assertEqual(switched, 2)
            with sqlite3.connect(db_path) as connection:
                rows = connection.execute(
                    "SELECT id, model_provider, archived FROM threads ORDER BY created_at"
                ).fetchall()

            self.assertEqual(
                rows,
                [
                    ("thread-openai", "azure", 0),
                    ("thread-azure", "azure", 0),
                    ("thread-archived", "openai", 1),
                ],
            )

    def test_rewrite_thread_providers_only_selected_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.sqlite"
            self._write_thread_db(db_path)

            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    """
                    INSERT INTO threads (
                        id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
                        sandbox_policy, approval_mode, tokens_used, has_user_event, archived, archived_at,
                        git_sha, git_branch, git_origin_url, cli_version, first_user_message, agent_nickname,
                        agent_role, memory_mode, model, reasoning_effort, agent_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "thread-openai-2",
                        "/tmp/rollout-openai-2.jsonl",
                        4,
                        8,
                        "desktop",
                        "openai",
                        "/tmp/project",
                        "OpenAI Thread 2",
                        "danger-full-access",
                        "never",
                        0,
                        1,
                        0,
                        None,
                        None,
                        None,
                        None,
                        "0.119.0",
                        "hello",
                        None,
                        None,
                        "enabled",
                        "gpt-5.4",
                        "high",
                        None,
                    ),
                )
                connection.commit()

            switched = cps.rewrite_thread_providers(db_path, "azure", ["thread-openai"])

            self.assertEqual(switched, 1)
            with sqlite3.connect(db_path) as connection:
                rows = connection.execute(
                    "SELECT id, model_provider, archived FROM threads ORDER BY created_at"
                ).fetchall()

            self.assertEqual(
                rows,
                [
                    ("thread-openai", "azure", 0),
                    ("thread-azure", "azure", 0),
                    ("thread-archived", "openai", 1),
                    ("thread-openai-2", "openai", 0),
                ],
            )

    def test_patch_rollout_session_meta_only_changes_first_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            rollout_path = Path(temp_dir) / "rollout.jsonl"
            rollout_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session_meta",
                                "payload": {"id": "thread-openai", "model_provider": "openai"},
                            }
                        ),
                        json.dumps({"type": "event_msg", "payload": {"type": "user_message"}}),
                        json.dumps(
                            {
                                "type": "session_meta",
                                "payload": {"id": "ignored", "model_provider": "openai"},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            updated = cps.patch_rollout_session_meta(rollout_path, "azure")

            self.assertTrue(updated)
            lines = [json.loads(line) for line in rollout_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(lines[0]["payload"]["model_provider"], "azure")
            self.assertEqual(lines[2]["payload"]["model_provider"], "openai")

    def test_patch_rollout_session_meta_removes_encrypted_reasoning_for_cross_provider_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            rollout_path = Path(temp_dir) / "rollout.jsonl"
            rollout_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session_meta",
                                "payload": {"id": "thread-azure", "model_provider": "azure"},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "response_item",
                                "payload": {
                                    "type": "reasoning",
                                    "summary": [],
                                    "content": None,
                                    "encrypted_content": "gAAAA-test",
                                },
                            }
                        ),
                        json.dumps({"type": "event_msg", "payload": {"type": "user_message"}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            updated = cps.patch_rollout_session_meta(
                rollout_path,
                "openai",
                scrub_encrypted_reasoning=True,
            )

            self.assertTrue(updated)
            lines = [json.loads(line) for line in rollout_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(lines[0]["payload"]["model_provider"], "openai")
            self.assertEqual(len(lines), 2)
            self.assertNotIn("encrypted_content", rollout_path.read_text(encoding="utf-8"))

    def test_write_manifest_records_switch_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backup_dir = Path(temp_dir) / "backup"
            backup_dir.mkdir()
            threads = [
                cps.ThreadRecord(
                    thread_id="thread-openai",
                    rollout_path="/tmp/rollout-openai.jsonl",
                    original_provider="openai",
                    title="OpenAI Thread",
                ),
                cps.ThreadRecord(
                    thread_id="thread-azure",
                    rollout_path="/tmp/rollout-azure.jsonl",
                    original_provider="azure",
                    title="Azure Thread",
                ),
            ]

            manifest_path = cps.write_backup_manifest(backup_dir, "azure", threads, archived_count=3)

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["target_provider"], "azure")
            self.assertEqual(manifest["archived_count"], 3)
            self.assertEqual(manifest["affected_threads"], ["thread-openai", "thread-azure"])
            self.assertEqual(
                manifest["original_provider_map"],
                {"thread-openai": "openai", "thread-azure": "azure"},
            )

    def test_restore_backup_copies_files_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            codex_home = temp_root / ".codex"
            backup_dir = temp_root / "backup"
            codex_home.mkdir()
            backup_dir.mkdir()

            for name, content in {
                "config.toml": 'model_provider = "azure"\n',
                "state_5.sqlite": "db-content",
                "state_5.sqlite-wal": "wal-content",
                "state_5.sqlite-shm": "shm-content",
            }.items():
                (backup_dir / name).write_text(content, encoding="utf-8")
                (codex_home / name).write_text("old", encoding="utf-8")

            restored = cps.restore_backup_files(backup_dir, codex_home)

            self.assertEqual(
                restored,
                [
                    codex_home / "config.toml",
                    codex_home / "state_5.sqlite",
                    codex_home / "state_5.sqlite-wal",
                    codex_home / "state_5.sqlite-shm",
                ],
            )
            self.assertEqual((codex_home / "config.toml").read_text(encoding="utf-8"), 'model_provider = "azure"\n')

    def test_backup_and_restore_include_rollout_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            codex_home = temp_root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "04" / "10"
            sessions_dir.mkdir(parents=True)
            (codex_home / "provider-switch-backups").mkdir(parents=True)

            config_path = codex_home / "config.toml"
            db_path = codex_home / "state_5.sqlite"
            config_path.write_text('model_provider = "azure"\n', encoding="utf-8")
            db_path.write_text("db-content", encoding="utf-8")

            rollout_path = sessions_dir / "rollout-thread.jsonl"
            rollout_path.write_text("before\n", encoding="utf-8")
            threads = [
                cps.ThreadRecord(
                    thread_id="thread-azure",
                    rollout_path=str(rollout_path),
                    original_provider="azure",
                    title="Azure Thread",
                )
            ]

            backup_dir = cps.backup_codex_state(
                codex_home,
                "openai",
                threads,
                archived_count=0,
            )

            rollout_path.write_text("after\n", encoding="utf-8")

            restored = cps.restore_backup_files(backup_dir, codex_home)

            self.assertIn(rollout_path, restored)
            self.assertEqual(rollout_path.read_text(encoding="utf-8"), "before\n")

    def test_restore_reports_available_backups_when_path_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            codex_home = temp_root / ".codex"
            backup_root = codex_home / "provider-switch-backups"
            backup_root.mkdir(parents=True)
            available_backup = backup_root / "20260410T052825Z"
            available_backup.mkdir()

            with self.assertRaises(cps.SwitcherError) as context:
                cps.perform_restore(temp_root / "missing-backup", codex_home)

            message = str(context.exception)
            self.assertIn("Backup directory does not exist", message)
            self.assertIn(str(available_backup), message)

    def test_perform_switch_with_thread_id_only_updates_selected_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            codex_home = temp_root / ".codex"
            codex_home.mkdir()

            config_path = codex_home / "config.toml"
            config_path.write_text(
                '\n'.join(
                    [
                        'model = "gpt-5.4"',
                        'model_provider = "openai"',
                        "",
                        "[model_providers.azure]",
                        'name = "Azure"',
                        "",
                        "[model_providers.openai]",
                        'name = "OpenAI"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            db_path = codex_home / "state_5.sqlite"
            self._write_thread_db(db_path)

            rollout_dir = temp_root / "sessions"
            rollout_dir.mkdir()
            rollout_paths = {
                "thread-openai": rollout_dir / "thread-openai.jsonl",
                "thread-azure": rollout_dir / "thread-azure.jsonl",
                "thread-archived": rollout_dir / "thread-archived.jsonl",
                "thread-openai-2": rollout_dir / "thread-openai-2.jsonl",
            }
            for thread_id, rollout_path in rollout_paths.items():
                provider = "azure" if "azure" in thread_id else "openai"
                rollout_path.write_text(
                    "\n".join(
                        [
                            json.dumps(
                                {
                                    "type": "session_meta",
                                    "payload": {"id": thread_id, "model_provider": provider},
                                }
                            ),
                            json.dumps({"type": "event_msg", "payload": {"type": "user_message"}}),
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )

            with sqlite3.connect(db_path) as connection:
                for thread_id, rollout_path in rollout_paths.items():
                    if thread_id == "thread-openai-2":
                        connection.execute(
                            """
                            INSERT INTO threads (
                                id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
                                sandbox_policy, approval_mode, tokens_used, has_user_event, archived, archived_at,
                                git_sha, git_branch, git_origin_url, cli_version, first_user_message, agent_nickname,
                                agent_role, memory_mode, model, reasoning_effort, agent_path
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                thread_id,
                                str(rollout_path),
                                4,
                                8,
                                "desktop",
                                "openai",
                                "/tmp/project",
                                "OpenAI Thread 2",
                                "danger-full-access",
                                "never",
                                0,
                                1,
                                0,
                                None,
                                None,
                                None,
                                None,
                                "0.119.0",
                                "hello",
                                None,
                                None,
                                "enabled",
                                "gpt-5.4",
                                "high",
                                None,
                            ),
                        )
                    else:
                        connection.execute(
                            "UPDATE threads SET rollout_path = ? WHERE id = ?",
                            (str(rollout_path), thread_id),
                        )
                connection.commit()

            with (
                mock.patch.object(cps, "validate_target_environment"),
                mock.patch.object(cps, "detect_codex_app"),
                mock.patch.object(cps, "gracefully_quit_codex"),
                mock.patch.object(cps, "relaunch_codex"),
            ):
                exit_code = cps.perform_switch("azure", codex_home, dry_run=False, thread_id="thread-openai")

            self.assertEqual(exit_code, 0)
            self.assertEqual(cps.read_top_level_model_provider(config_path), "azure")
            with sqlite3.connect(db_path) as connection:
                rows = connection.execute(
                    "SELECT id, model_provider, archived FROM threads ORDER BY created_at"
                ).fetchall()

            self.assertEqual(
                rows,
                [
                    ("thread-openai", "azure", 0),
                    ("thread-azure", "azure", 0),
                    ("thread-archived", "openai", 1),
                    ("thread-openai-2", "openai", 0),
                ],
            )

            selected_rollout = [
                json.loads(line)
                for line in rollout_paths["thread-openai"].read_text(encoding="utf-8").splitlines()
            ]
            untouched_rollout = [
                json.loads(line)
                for line in rollout_paths["thread-openai-2"].read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(selected_rollout[0]["payload"]["model_provider"], "azure")
            self.assertEqual(untouched_rollout[0]["payload"]["model_provider"], "openai")

            backup_root = codex_home / "provider-switch-backups"
            backup_dirs = [path for path in backup_root.iterdir() if path.is_dir()]
            self.assertEqual(len(backup_dirs), 1)
            manifest = json.loads((backup_dirs[0] / "backup-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["affected_threads"], ["thread-openai"])

    def test_install_script_installs_latest_release_and_smoke_tests_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            remote_root = temp_root / "remote"
            latest_tag = "v1.2.3"
            self._write_release_fixture(
                remote_root,
                latest_tag=latest_tag,
                versions={latest_tag: (SCRIPTS_DIR / "grimoire_switch.py").read_text(encoding="utf-8")},
            )
            home_dir = temp_root / "home"
            workdir = temp_root / "outside-repo"
            workdir.mkdir()

            result = self._run_install_script(
                home_dir=home_dir,
                workdir=workdir,
                extra_env={
                    "GRIMOIRE_SWITCH_RELEASE_API_URL": f"file://{remote_root / 'latest-release.json'}",
                    "GRIMOIRE_SWITCH_DOWNLOAD_BASE_URL": f"file://{remote_root / 'raw'}",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            installed_cli = home_dir / ".local" / "bin" / "grimoire-switch"
            self.assertTrue(installed_cli.exists())

            help_result = subprocess.run(
                [str(installed_cli), "--help"],
                check=False,
                capture_output=True,
                text=True,
                cwd=workdir,
            )
            version_result = subprocess.run(
                [str(installed_cli), "--version"],
                check=False,
                capture_output=True,
                text=True,
                cwd=workdir,
            )

            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("Grimoire Switch", help_result.stdout)
            self.assertEqual(version_result.returncode, 0, version_result.stderr)
            self.assertIn("grimoire-switch", version_result.stdout)

    def test_install_script_supports_explicit_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            remote_root = temp_root / "remote"
            self._write_release_fixture(
                remote_root,
                latest_tag="v2.0.0",
                versions={
                    "v1.0.0": self._fake_cli_source("v1.0.0"),
                    "v2.0.0": self._fake_cli_source("v2.0.0"),
                },
            )
            home_dir = temp_root / "home"

            result = self._run_install_script(
                home_dir=home_dir,
                extra_args=["--version", "v1.0.0"],
                extra_env={
                    "GRIMOIRE_SWITCH_RELEASE_API_URL": f"file://{remote_root / 'latest-release.json'}",
                    "GRIMOIRE_SWITCH_DOWNLOAD_BASE_URL": f"file://{remote_root / 'raw'}",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            installed_cli = home_dir / ".local" / "bin" / "grimoire-switch"
            version_result = subprocess.run(
                [str(installed_cli), "--version"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(version_result.returncode, 0, version_result.stderr)
            self.assertIn("v1.0.0", version_result.stdout)

    def test_install_script_rejects_non_macos(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            home_dir = temp_root / "home"

            result = self._run_install_script(
                home_dir=home_dir,
                extra_env={"GRIMOIRE_SWITCH_UNAME": "Linux"},
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("macOS", result.stderr)

    def test_install_script_reports_missing_python3(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            home_dir = temp_root / "home"

            result = self._run_install_script(
                home_dir=home_dir,
                extra_env={
                    "GRIMOIRE_SWITCH_PYTHON_BIN": "/definitely-missing/python3",
                },
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("python3", result.stderr)

    def test_install_script_warns_when_install_dir_is_not_on_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            remote_root = temp_root / "remote"
            latest_tag = "v1.2.3"
            self._write_release_fixture(
                remote_root,
                latest_tag=latest_tag,
                versions={latest_tag: self._fake_cli_source(latest_tag)},
            )
            home_dir = temp_root / "home"

            result = self._run_install_script(
                home_dir=home_dir,
                extra_env={
                    "GRIMOIRE_SWITCH_RELEASE_API_URL": f"file://{remote_root / 'latest-release.json'}",
                    "GRIMOIRE_SWITCH_DOWNLOAD_BASE_URL": f"file://{remote_root / 'raw'}",
                    "PATH": "/usr/bin:/bin",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PATH", result.stdout)
            self.assertIn(str(home_dir / ".local" / "bin"), result.stdout)

    def test_install_script_uses_timeout_and_retry_flags_for_curl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            home_dir = temp_root / "home"
            fake_curl = temp_root / "fake-curl.py"
            log_path = temp_root / "curl.log"
            fake_curl.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import os",
                        "import sys",
                        "from pathlib import Path",
                        "",
                        "args = sys.argv[1:]",
                        f"log_path = Path({str(log_path)!r})",
                        "log_path.write_text(' '.join(args) + '\\n', encoding='utf-8')",
                        "required = ['--connect-timeout', '15', '--max-time', '60', '--retry', '3']",
                        "for item in required:",
                        "    if item not in args:",
                        "        print(f'missing curl flag: {item}', file=sys.stderr)",
                        "        sys.exit(9)",
                        "output = None",
                        "for index, value in enumerate(args[:-1]):",
                        "    if value == '-o':",
                        "        output = args[index + 1]",
                        "url = next((value for value in reversed(args) if '://' in value), '')",
                        "if url.endswith('/releases/latest'):",
                        "    sys.stdout.write('{\"tag_name\": \"v0.1.1\"}')",
                        "elif output:",
                        "    Path(output).write_text('#!/usr/bin/env python3\\nprint(\"grimoire-switch v0.1.1\")\\n', encoding='utf-8')",
                        "else:",
                        "    print(f'unexpected curl invocation: {args}', file=sys.stderr)",
                        "    sys.exit(10)",
                    ]
                ),
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)

            result = self._run_install_script(
                home_dir=home_dir,
                extra_env={
                    "GRIMOIRE_SWITCH_CURL_BIN": str(fake_curl),
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            logged = log_path.read_text(encoding="utf-8")
            self.assertIn("--connect-timeout", logged)
            self.assertIn("--max-time", logged)
            self.assertIn("--retry", logged)
            self.assertIn(
                "https://github.com/GrimoireCore/Grimoire-Switch/releases/download/v0.1.1/grimoire_switch.py",
                logged,
            )

    def test_install_script_overwrites_existing_installation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            remote_root = temp_root / "remote"
            self._write_release_fixture(
                remote_root,
                latest_tag="v2.0.0",
                versions={
                    "v1.0.0": self._fake_cli_source("v1.0.0"),
                    "v2.0.0": self._fake_cli_source("v2.0.0"),
                },
            )
            home_dir = temp_root / "home"
            env = {
                "GRIMOIRE_SWITCH_RELEASE_API_URL": f"file://{remote_root / 'latest-release.json'}",
                "GRIMOIRE_SWITCH_DOWNLOAD_BASE_URL": f"file://{remote_root / 'raw'}",
            }

            first_result = self._run_install_script(
                home_dir=home_dir,
                extra_args=["--version", "v1.0.0"],
                extra_env=env,
            )
            second_result = self._run_install_script(
                home_dir=home_dir,
                extra_args=["--version", "v2.0.0"],
                extra_env=env,
            )

            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)

            installed_cli = home_dir / ".local" / "bin" / "grimoire-switch"
            version_result = subprocess.run(
                [str(installed_cli), "--version"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(version_result.returncode, 0, version_result.stderr)
            self.assertIn("v2.0.0", version_result.stdout)

    def _write_thread_db(self, db_path: Path) -> None:
        with sqlite3.connect(db_path) as connection:
            connection.executescript(THREADS_SCHEMA)
            connection.executemany(
                """
                INSERT INTO threads (
                    id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
                    sandbox_policy, approval_mode, tokens_used, has_user_event, archived, archived_at,
                    git_sha, git_branch, git_origin_url, cli_version, first_user_message, agent_nickname,
                    agent_role, memory_mode, model, reasoning_effort, agent_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "thread-openai",
                        "/tmp/rollout-openai.jsonl",
                        1,
                        5,
                        "desktop",
                        "openai",
                        "/tmp/project",
                        "OpenAI Thread",
                        "danger-full-access",
                        "never",
                        0,
                        1,
                        0,
                        None,
                        None,
                        None,
                        None,
                        "0.119.0",
                        "hello",
                        None,
                        None,
                        "enabled",
                        "gpt-5.4",
                        "high",
                        None,
                    ),
                    (
                        "thread-azure",
                        "/tmp/rollout-azure.jsonl",
                        2,
                        6,
                        "desktop",
                        "azure",
                        "/tmp/project",
                        "Azure Thread",
                        "danger-full-access",
                        "never",
                        0,
                        1,
                        0,
                        None,
                        None,
                        None,
                        None,
                        "0.119.0",
                        "hello",
                        None,
                        None,
                        "enabled",
                        "gpt-5.4",
                        "high",
                        None,
                    ),
                    (
                        "thread-archived",
                        "/tmp/rollout-archived.jsonl",
                        3,
                        7,
                        "desktop",
                        "openai",
                        "/tmp/project",
                        "Archived Thread",
                        "danger-full-access",
                        "never",
                        0,
                        1,
                        1,
                        11,
                        None,
                        None,
                        None,
                        "0.119.0",
                        "hello",
                        None,
                        None,
                        "enabled",
                        "gpt-5.4",
                        "high",
                        None,
                    ),
                ],
            )

    def _run_install_script(
        self,
        home_dir: Path,
        extra_args: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
        workdir: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(home_dir)
        env.setdefault("PATH", os.environ.get("PATH", ""))
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["/bin/bash", str(INSTALL_SCRIPT), *(extra_args or [])],
            check=False,
            capture_output=True,
            text=True,
            cwd=workdir or PROJECT_ROOT,
            env=env,
        )

    def _write_release_fixture(
        self,
        remote_root: Path,
        latest_tag: str,
        versions: dict[str, str],
    ) -> None:
        raw_root = remote_root / "raw"
        raw_root.mkdir(parents=True)
        (remote_root / "latest-release.json").write_text(
            json.dumps({"tag_name": latest_tag}),
            encoding="utf-8",
        )
        for version, script_source in versions.items():
            target = raw_root / version / "grimoire_switch.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(script_source, encoding="utf-8")

    def _fake_cli_source(self, version: str) -> str:
        return "\n".join(
            [
                "#!/usr/bin/env python3",
                "import sys",
                "",
                f"VERSION = {version!r}",
                "",
                "if '--version' in sys.argv:",
                "    print(f'grimoire-switch {VERSION}')",
                "elif '--help' in sys.argv:",
                "    print('Grimoire Switch help')",
                "else:",
                "    print(f'grimoire-switch {VERSION}')",
                "",
            ]
        )


if __name__ == "__main__":
    unittest.main()
