# facut

[![tests](https://github.com/SystemZion/facut/actions/workflows/tests.yml/badge.svg)](https://github.com/SystemZion/facut/actions/workflows/tests.yml)
[![release](https://img.shields.io/github/v/release/SystemZion/facut)](https://github.com/SystemZion/facut/releases/latest)

**facut**（Fast AI Cut）是一款面向 AI Agent、自动化脚本与高级用户的非破坏性命令行视频编辑器。它使用稳定素材 ID、结构化工程、可组合子命令及统一 JSON 返回值，让复杂剪辑既能由人操作，也能可靠地被程序调用。

当前版本 `0.1.0` 是首个可用版本：工程、素材探测、单视频轨时间线、帧级时间表示、原子批处理、基础转场、快速预览、H.264/AAC 渲染和撤销/重做均可实际执行。Windows 单文件发行版内置 FFmpeg/FFprobe，不依赖系统 Python。

## 安装

Windows 普通用户可从 [GitHub Releases](https://github.com/SystemZion/facut/releases/latest) 下载单文件 `facut.exe`。源码开发需要 Python 3.11 或更高版本，以及可在 `PATH` 中找到的 FFmpeg/FFprobe。

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
facut --help
facut doctor
```

macOS / Linux：

```bash
source .venv/bin/activate
python -m pip install -e ".[dev]"
facut --help
facut doctor
```

如果工具不在 `PATH` 中，可以设置 `FACUT_FFMPEG`、`FACUT_FFPROBE` 环境变量。`FACUT_CONFIG` 可覆盖全局配置文件位置。

## 命令概览

当前可执行的公共接口：

```text
facut init/import/inspect
facut timeline/clip/transition
facut preview/render
facut project/history
facut undo/redo/run/doctor
```

全局参数：

```text
--json                 仅输出统一 JSON
--quiet, -q            减少非必要输出
--verbose, -v          启用详细日志
--project, -p PATH     指定工程文件或目录
```

可执行的环境诊断：

```bash
facut doctor
facut doctor --json
facut doctor --sample-render
```

`--sample-render` 会使用 FFmpeg 的生成源创建一个临时的一秒小视频，不读取用户素材。

## JSON 协议

成功响应：

```json
{
  "status": "success",
  "command": "doctor",
  "data": {},
  "warnings": [],
  "errors": [],
  "project_revision": null
}
```

失败响应中的错误包含稳定的 `code`、面向用户的 `message` 和可执行的 `suggestion`。退出码约定为：成功 `0`、参数错误 `2`、文件不存在 `3`、媒体解析失败 `4`、时间线冲突 `5`、渲染失败 `6`、依赖缺失 `7`。

## 配置和日志

配置采用 TOML。Windows 使用 AppData 标准目录；macOS/Linux 使用平台标准配置目录（一般为 `~/.config/facut/config.toml`）。默认项包括编码器、硬件策略、预览高度/帧率、缓存、FFmpeg 路径、日志级别与临时目录。保存使用同目录临时文件和原子替换。

日志写入平台标准日志目录中的 `facut.log`，自动轮转，不把普通诊断输出和 JSON 混在一起。

## 工程与时间

工程将使用 `facut.json` 和 JSON Schema，媒体通过稳定 ID 引用，原始文件不会被修改。时间参数统一支持秒、毫秒、时间码和帧：

```text
12.5
12.5s
1500ms
00:00:12.500
450f
```

输出同时提供秒、时间码和帧编号。最终渲染使用原始素材，代理只服务于快速预览。

## 转场、效果与插件

转场和效果采用注册表：每个实现声明唯一名称、别名、分类、参数模型、默认时长、渲染实现、预览能力及降级策略。首批转场为 `fade-in`、`fade-out`、`dissolve`、`fade-black`、`fade-white`、`slide`、`wipe`、`zoom` 和 `blur`。首批效果覆盖 transform、透明度、基础调色、LUT、文字和字幕。

插件代码不应直接拼接 shell 字符串。渲染后端接收参数列表和经过验证的滤镜图节点。

## AI Agent 调用

查询状态：

```bash
facut --project demo project snapshot --output state.json
```

预演并应用原子批处理：

```bash
facut --project demo run ai-edit-plan.json --dry-run --json
facut --project demo run ai-edit-plan.json --json
```

也可从标准输入读取：

```powershell
Get-Content -Raw ai-edit-plan.json | facut --project demo run - --json
```

## 完整可运行示例

裁切并合并：

```bash
facut init demo
facut --project demo import a.mp4 b.mp4
facut --project demo timeline track add --type video --name V1
facut --project demo timeline add <MEDIA_A_ID> --track V1 --at 0 --in 2s --out 10s
facut --project demo timeline add <MEDIA_B_ID> --track V1 --at 7.5s --in 0 --out 6s
facut --project demo transition add --from <CLIP_A_ID> --to <CLIP_B_ID> --type dissolve --duration 0.5s
facut --project demo preview range --from 7s --to 10s --output preview.mp4
facut --project demo render --preset youtube-4k --output final.mp4
```

原子 AI 批处理：

```bash
facut --project demo project snapshot --output state.json
facut --project demo run ai-edit-plan.json --dry-run --json
facut --project demo run ai-edit-plan.json --json
facut --project demo render --preset youtube-1080p --output result.mp4
```

### 当前明确限制

- 渲染器当前支持一个启用的视频/图片轨；多视频轨叠加不会伪造成功，而会明确返回 `NOT_IMPLEMENTED`。
- 首版最终编码为 H.264/AAC；H.265、AV1、ProRes 等已保留后端扩展边界，但尚未开放。
- 背景音乐专用音轨、字幕烧录、文字、关键帧 CLI、高级分析和自动剪辑属于后续版本。
- `fade-in`/`fade-out` 已注册，首版成对片段间的稳定转场重点为 `dissolve`、`fade-black`、`fade-white`、`slide`、`wipe`、`zoom`、`blur`。

## 开发与测试

```bash
python -m pip install -e ".[dev]"
pytest
```

测试中的媒体由 FFmpeg 动态生成，不把大型二进制素材提交到仓库。业务模块使用类型注解，外部程序一律以参数列表和 `shell=False` 启动。

## 架构

```text
src/facut/
├── cli/              Typer 命令和输出适配
├── core/             工程、时间线、命令及历史
├── media/            探测、代理、缩略图和波形
├── render/           滤镜图、缓存、硬件和 FFmpeg 后端
├── transitions/      插件式转场
├── effects/          插件式视频/音频效果
├── config.py         跨平台配置
├── exceptions.py     领域错误和稳定退出码
├── logging_config.py 轮转日志
└── responses.py      JSON 响应协议
```

Windows 发行物通过 PyInstaller 打包为单文件 `facut.exe`，并内置 FFmpeg/FFprobe 8.1.2；源码安装方式用于开发和测试。
