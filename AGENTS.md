# Repository Guidelines

## Working Rules

- 每次执行任务前，先确认需求与用户要求一致，再开始实现。
- 所有迭代改动允许破坏性更新，因为项目尚未上线部署。
- 每次出现问题并修复后，更新本文件，避免后续重复出现相同问题。

## Fix Log

- 2026-04-15: 新增 GitHub-first 安装入口与公开版本号。
  - 根目录新增 `install.sh`，默认解析 `GrimoireCore/Grimoire-Switch` 的最新 GitHub Release tag，并安装对应 tag 下的 `scripts/grimoire_switch.py` 到 `~/.local/bin/grimoire-switch`。
  - `install.sh` 需要先校验 `macOS`、`curl`、`python3`，成功安装后如果 `~/.local/bin` 不在 `PATH` 里，要直接打印下一步提示。
  - CLI 新增公开 `--version`，发布时记得同步更新 `scripts/grimoire_switch.py` 里的 `VERSION` 常量，再创建同名 Git tag / GitHub Release。
  - 根目录 README 现在是对外主入口，优先写安装、升级、卸载、风险和故障排查；仓库内 `.command` 入口保留给本地开发/调试。
- 2026-04-15: 测试环境兼容系统 Python 3.9。
  - 这个仓库里的测试文件不要直接写 Python 3.10+ 才支持的 `list[str] | None` 这类联合类型语法，除非文件显式启用了兼容处理；当前 macOS 系统自带 `python3` 仍可能是 3.9。
- 2026-04-15: 项目改名为 Grimoire Switch，并补充路径带空格的执行约束。
  - 仓库内对项目入口、脚本、模块、测试与文档的引用统一改为 `Grimoire Switch` / `grimoire-switch` / `grimoire_switch`，避免继续混用旧项目名。
  - 入口脚本更新为 `/Users/handong/GithubProduct/Grimoire Switch/scripts/grimoire-switch.command`，Python helper 更新为 `/Users/handong/GithubProduct/Grimoire Switch/scripts/grimoire_switch.py`。
  - 运行 shell 命令时，凡是包含 `/Users/handong/GithubProduct/Grimoire Switch` 这类带空格的路径，都必须整体加引号，避免路径被 shell 拆分导致命令失败。
  - 脚本文件重命名后要重新确认可执行权限仍在；如果直接执行 `.command` 报 `permission denied`，先恢复 `chmod +x ./scripts/grimoire-switch.command`。
- 2026-04-14: 支持只切换指定线程。
  - 新增 `--thread-id <thread-id>` 参数，按线程 ID 精确匹配单个活跃线程；未指定时仍保持“切换全部活跃线程”的原行为。
  - 单线程切换仍会把顶层 `model_provider` 改成目标 provider；其他活跃线程只是不迁移，通常会在切到目标 provider 后暂时不可见，这属于预期行为。
  - 单线程切换时，数据库更新、rollout 改写、备份 manifest 与 rollout 备份都只覆盖被选中的线程，避免误改其他活跃线程。
  - 已存在但 `archived = 1` 的线程不会被 `--thread-id` 切换；如果传入归档线程 ID，脚本需要直接报错而不是偷偷改库。
- 2026-04-10: 新增 Codex 跨 provider 线程切换工具。
  - 入口脚本是 `/Users/handong/GithubProduct/Grimoire Switch/scripts/grimoire-switch.command`。
  - 切换前必须先备份 `~/.codex/config.toml`、`state_5.sqlite`、`state_5.sqlite-wal`、`state_5.sqlite-shm`。
  - 活跃线程会统一改到目标 provider，归档线程默认不改。
  - 如果未来 Codex 升级导致本地 schema 或 rollout 格式变化，先重新确认 `threads.model_provider` 和 rollout 首条 `session_meta.payload.model_provider` 仍然存在，再运行脚本。
  - 需要回滚时使用 `./scripts/grimoire-switch.command --restore <backup-dir>`。
  - 修复过一次 Azure key 检测问题：有些 macOS 环境下 `AZURE_OPENAI_API_KEY` 不在当前 shell 里，但存在于 `launchctl getenv`。脚本现在会先查 shell，再查 `launchctl`，后续不要回退成只查 `os.environ`。
- 2026-04-10: 修复 Codex 跨 provider 切换后继续历史线程时报 `invalid_encrypted_content`。
  - 根因是旧 provider 线程里的 `response_item.payload.encrypted_content` 会跟随历史一起发给新 provider，导致新 provider 无法校验旧加密块。
  - 切换脚本在跨 provider 改写 rollout 时，必须删除旧 provider 残留的加密 reasoning 记录，只保留可继续复用的可见对话历史。
  - 备份必须覆盖被改写的 rollout 文件，不能只备份 `config.toml` 和 SQLite，否则 `--restore` 无法真正回滚线程内容。
  - 新版 Codex 可能在 relaunch 后自动移除顶层 `model_provider = "openai"`；脚本需要在缺失时补回目标 provider，而不是假设该配置项永远存在。
- 2026-04-10: 优化 `--restore` 误用占位路径时的报错提示。
  - 文档中的 `/absolute/path/to/backup-dir` 只是示例占位符，不是真实可执行路径。
  - `perform_restore` 在备份目录不存在时，需要把当前 `~/.codex/provider-switch-backups/` 下的可用目录一起列出来，方便直接复制执行。
