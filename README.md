# Grimoire Switch

`Grimoire Switch` 是一个面向 macOS 的本地工具，用来在 Codex 的 `openai` / `azure` provider 之间切换当前可见线程，并修正继续历史线程时需要同步的本地状态。

它不是 Codex 官方功能，会直接修改 `~/.codex` 下的本地配置、SQLite 和 rollout 文件。使用前请先理解风险。

## 适用范围

- 仅支持 `macOS`
- 依赖本机已安装 `Codex`
- 依赖 `python3` 与 `curl`
- 默认面向已经在本机使用 Codex 的用户

## 安装

默认安装最新 GitHub Release，对外命令名是 `grimoire-switch`。

```bash
tmp="$(mktemp)" && curl -fsSL https://raw.githubusercontent.com/GrimoireCore/Grimoire-Switch/main/install.sh -o "$tmp" && bash "$tmp" && rm -f "$tmp"
```

如果你想安装指定版本，把版本号追加到安装器后面：

```bash
tmp="$(mktemp)" && curl -fsSL https://raw.githubusercontent.com/GrimoireCore/Grimoire-Switch/main/install.sh -o "$tmp" && bash "$tmp" --version v0.1.1 && rm -f "$tmp"
```

安装完成后，二进制会放到 `~/.local/bin/grimoire-switch`。如果你的 `PATH` 里还没有 `~/.local/bin`，安装器会直接提示下一步命令。

## 升级

升级方式就是重跑安装命令。

- 升级到最新 Release：重跑默认安装命令
- 升级或回退到指定版本：重跑并带上 `--version <tag>`

## 卸载

```bash
rm -f ~/.local/bin/grimoire-switch
```

卸载不会回滚你之前对 `~/.codex` 做过的 provider 切换；如需回滚，请使用工具自己的 `--restore`。

## 常用示例

查看帮助：

```bash
grimoire-switch --help
```

查看当前安装版本：

```bash
grimoire-switch --version
```

切到 `openai`：

```bash
grimoire-switch openai
```

切到 `azure`：

```bash
grimoire-switch azure
```

只预览，不真正改文件：

```bash
grimoire-switch azure --dry-run
```

只切换一个活跃线程：

```bash
grimoire-switch azure --thread-id <thread-id>
```

从备份回滚：

```bash
grimoire-switch --restore /absolute/path/to/backup-dir
```

## 风险说明

- 这不是官方支持的 Codex 特性，未来 Codex 本地 schema 或 rollout 格式变化后，脚本可能需要同步调整。
- 工具会修改 `~/.codex/config.toml`、`state_5.sqlite`、`state_5.sqlite-wal`、`state_5.sqlite-shm`，以及被切换线程对应的 rollout 文件。
- 跨 provider 切换时，会删除旧 provider 残留的加密 reasoning 内容，避免继续历史线程时报 `invalid_encrypted_content`。
- 默认只改活跃线程，归档线程不会被一起迁移。

## 故障排查

`grimoire-switch: command not found`

- 说明 `~/.local/bin` 还不在 `PATH` 里。
- 先执行安装器输出的 `export PATH="~/.local/bin:$PATH"`，再重开一个终端验证。

`Missing environment variable: AZURE_OPENAI_API_KEY`

- 切到 `azure` 时必须能拿到 `AZURE_OPENAI_API_KEY`。
- 工具会先查当前 shell，再查 `launchctl getenv AZURE_OPENAI_API_KEY`。

`Could not detect Codex app with bundle id com.openai.codex`

- 说明本机没有正常安装可识别的 Codex app，或者 app bundle id 和当前脚本假设不一致。

`Backup directory does not exist`

- 回滚时请使用真实备份目录，而不是文档里的占位路径。
- 备份默认在 `~/.codex/provider-switch-backups/`。

## 仓库内使用

如果你是在本仓库里开发或调试，也可以继续直接用仓库内入口：

```bash
./scripts/grimoire-switch.command azure --dry-run
```

## 发布说明（维护者）

对外发布流程固定为 GitHub Release 驱动：

1. 修改 `scripts/grimoire_switch.py` 里的 `VERSION`
2. 运行测试
3. 创建并推送 Git tag
4. 在 GitHub 上发布同名 Release
5. 给该 Release 上传名为 `grimoire_switch.py` 的 asset（内容来自仓库里的 `scripts/grimoire_switch.py`）
6. 之后安装器默认会解析最新 Release，并下载对应 Release asset 安装到 `~/.local/bin/grimoire-switch`
