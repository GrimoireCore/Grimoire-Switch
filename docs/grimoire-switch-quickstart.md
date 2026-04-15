# Grimoire Switch 快速使用说明

如果你正在使用 `Codex`，现在需要切换到当前目标 `provider`，请使用 `Grimoire Switch`。

`Grimoire Switch` 会帮你把本地 Codex 当前可见线程切换到目标 provider，并同步修正继续历史线程时需要用到的本地状态。

## 适用范围

- 仅支持 `macOS`
- 本机已安装 `Codex`
- 本机已安装 `python3` 和 `curl`

## 安装命令

安装最新稳定版：

```bash
tmp="$(mktemp)" && curl -fsSL https://raw.githubusercontent.com/GrimoireCore/Grimoire-Switch/main/install.sh -o "$tmp" && bash "$tmp" && rm -f "$tmp"
```

安装指定版本：

```bash
tmp="$(mktemp)" && curl -fsSL https://raw.githubusercontent.com/GrimoireCore/Grimoire-Switch/main/install.sh -o "$tmp" && bash "$tmp" --version v0.1.2 && rm -f "$tmp"
```

安装完成后，可以用下面命令确认版本：

```bash
grimoire-switch --version
```

## 切换命令

切换到任意目标 provider：

```bash
grimoire-switch <provider-name>
```

只预览切换结果，不真正修改本地文件：

```bash
grimoire-switch <provider-name> --dry-run
```

只切换指定线程：

```bash
grimoire-switch <provider-name> --thread-id <thread-id>
```

## 回滚命令

如果切换后想恢复到之前状态：

```bash
grimoire-switch --restore /absolute/path/to/backup-dir
```

## 常见问题

如果执行后提示 `grimoire-switch: command not found`，通常是因为 `~/.local/bin` 还没有加入 `PATH`。

可以先执行：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

然后重新打开终端或重新执行命令。

如果提示 `Provider 'xxx' is not available`，请先确认该 provider 已经配置在 `~/.codex/config.toml` 中，且名称拼写正确。
