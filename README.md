# facut

[![tests](https://github.com/SystemZion/facut/actions/workflows/tests.yml/badge.svg)](https://github.com/SystemZion/facut/actions/workflows/tests.yml)
[![release](https://img.shields.io/github/v/release/SystemZion/facut)](https://github.com/SystemZion/facut/releases/latest)

**facut**（Fast AI Cut）是一款面向 AI Agent、自动化脚本与高级用户的非破坏性命令行视频编辑器。它使用稳定素材 ID、结构化工程、可组合子命令及统一 JSON 返回值，让复杂剪辑既能由人操作，也能可靠地被程序调用。

当前版本 `0.4.0` 在代理—精剪—声音—QC 链路上增加专业旅拍底座：混合设备摄取、相机/GPS/时区/HDR 元数据、内容哈希分析缓存、带证据和置信度的保守分类、单主序列多平台交付，以及从“临港两日”成片提炼的电影感中英双层标题模板。Windows 单文件发行版内置 FFmpeg/FFprobe，不依赖系统 Python。

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
facut init/import/ingest/inspect/help
facut timeline/clip/transition/effect/audio
facut proxy/analyze/qc/marker
facut subtitle/text
facut preview/render/deliver/delivery-presets/sequence
facut project/history
facut undo/redo/run/serve/doctor
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

`--sample-render` 会使用 FFmpeg 的生成源创建一个临时的一秒小视频，不读取用户素材。诊断结果会返回 FFmpeg/FFprobe 绝对路径，并把硬件编码器分别标记为 `detected`、`usable`、`implemented`；NVENC、QSV、AMF、VideoToolbox 均进行短片实测，而非只检查名称。

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

输出同时提供秒、时间码和帧编号。`proxy create/link/relink/status` 在同一个工程内维护原片/代理关系；预览自动使用在线代理，最终渲染自动回到原片。`proxy relink <MEDIA_ID> --search <DIR>` 可识别同名 `_LRF` 与 `_proxy` 文件。

## 转场、效果与插件

转场和效果采用注册表：每个实现声明唯一名称、分类、参数模型与渲染实现。转场包含 `fade-in`、`fade-out`、`dissolve`、`fade-black`、`fade-white`、`slide`、`wipe`、`zoom` 和 `blur`。效果包含亮度、对比度、饱和度、Gamma、模糊、锐化、黑白、暗角、像素化和 LUT。

```bash
facut --project demo clip transform <CLIP_ID> --x 120 --y 60 --scale 0.5 --opacity 0.8 --no-autorotate
facut --project demo clip freeze <CLIP_ID> --at 2s --duration 1.5s --ripple
facut --project demo effect add <CLIP_ID> --type brightness --param value=0.1
facut --project demo clip composite <CLIP_ID> --blend-mode screen
facut --project demo effect adjustment-add --track ADJ1 --type vignette --at 3s --duration 5s
```

高层视频轨会作为真实画中画叠加，支持位置、缩放、旋转、透明度、灰度遮罩以及 normal/screen/multiply/overlay/addition/difference 混合模式。调整层可按时间范围作用于合成结果。

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

高频 Agent 操作可使用常驻会话，避免每条细粒度命令都支付 EXE 冷启动成本：

```powershell
facut --project demo serve
```

它通过 stdin/stdout 交换逐行 JSON-RPC，支持 `ping`、`project.snapshot`、`timeline.show`、原子 `run` 和 `shutdown`，同一进程复用已载入工程。

## 专业旅拍摄取与素材理解

```powershell
facut ingest D:\JapanTrip --trip "日本旅行2026" --verify sha256 --proxy auto
facut --project 日本旅行2026 inspect <MEDIA_ID> --json
facut --project 日本旅行2026 analyze travel <MEDIA_ID> --save --json
```

`ingest` 递归导入手机、相机、运动相机、无人机、录音与字幕素材，逐文件流式计算 SHA-256，并可先查找同名/LRF 代理、缺失时生成 540p 代理。默认新建 3840×2160、48kHz 工程；也可配合全局 `--project` 写入已有工程。

FFprobe 元数据会保留旋转、VFR、创建时间、时区、时间码、相机厂商/型号、镜头、GPS、色彩空间和 HDR-PQ/HDR-HLG/Log 线索。`analyze travel` 第一版只根据路径和元数据产生可解释候选，每项含 `confidence` 与 `evidence`；文件夹名并不被当作画面事实。需要人物、动作、地点或镜头语义时会明确返回 `plugin_required`。同一内容哈希和参数的分析结果写入工程缓存，后续调用直接复用。

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

字幕与文字：

```bash
facut --project demo timeline track add --type subtitle --name S1
facut --project demo subtitle import captions.srt --track S1
facut --project demo subtitle shift S1 --offset +500ms
facut --project demo subtitle export S1 --format vtt --output captions.vtt
facut --project demo text add --text "临港两日" --at 1s --duration 3s --y 20%
facut --project demo text add --text "抬头，是更大的尺度" --subtitle "LOOK UP · THE SCALE CHANGES" --at 4s --duration 3s --template lingang-cinematic-panel
facut --project demo text add --text "再来" --subtitle "同一条雪道，重新滑下" --at 48s --duration 2s --template lingang-cinematic-mint
facut --project demo subtitle compile-ass --output render-text.ass
facut --project demo render --output subtitled.mp4
```

渲染和预览会自动将工程中的字幕与文字编译为临时 ASS 并烧录。`compile-ass` 仍可生成独立、可检查的 ASS 侧车文件。临港系列模板不是简单的颜色预设：编译器会生成半透明圆角矢量面板、细描边、强调色竖线和独立主副标题图层，并按 1080p/4K 画布等比例缩放。内置 `lingang-cinematic-panel`、`lingang-cinematic-mint`、`lingang-cinematic-warm`、`lingang-day-card` 和 `lingang-main-title`；可用 `--accent-color` 覆盖强调色。软件不捆绑第三方字体文件，默认使用系统字体以避免授权问题。

背景音乐与原声混合：

```bash
facut --project demo import music.mp3
facut --project demo timeline track add --type audio --name A1
facut --project demo audio add <MUSIC_ID> --track A1 --at 0 --loop --volume-db -12 --fade-in 1s --fade-out 2s
facut --project demo render --output mixed.mp4
```

独立音频轨会与视频素材自带的相机原声混合；`--loop` 默认循环到视频时间线末尾，也可配合 `--duration` 指定长度。

原视频音轨与独立音轨共用一套非破坏音频链：增益/静音、淡入淡出、高通、FFT 降噪、压缩、限制器、响度统一、单声道修复、声像和交叉淡化。

```bash
facut --project demo audio process <CLIP_ID> --highpass-hz 80 --denoise-strength 0.35 --compressor --limiter-db -1 --loudnorm-lufs -14
facut --project demo audio mute <CLIP_ID>
facut --project demo audio crossfade --from <LEFT_ID> --to <RIGHT_ID> --duration 700ms
```

## 代理、增量渲染和进度

```bash
facut --project demo proxy create <MEDIA_ID> --height 540
facut --project demo proxy status --json
facut --project demo preview timeline
facut --project demo render --output final.mp4 --incremental --jsonl-progress
```

增量渲染以素材状态和片段参数哈希缓存中间段。当前安全优化范围是单个连续主视频轨、无转场/字幕/文字/额外音频；复杂工程会明确警告并降级为完整渲染，不会给出虚假的缓存命中。渲染器强制以时间线终点封装并裁掉音视频尾巴。JSONL 进度包含阶段、百分比、输出时间、帧率、速度、ETA 与当前片段。

## QC、分析和标记

```bash
facut --json --project demo qc --report reports/qc.md --contact-sheet reports/contact.jpg
facut --project demo analyze quality <MEDIA_ID> --save
facut --project demo analyze scenes <MEDIA_ID> --save
facut --project demo analyze beats <MUSIC_ID> --save
facut --project demo marker add --at 32.5s --label "副歌" --category music --rating 5 --recommended
facut --project demo marker export --format xlsx --output markers.xlsx
```

`qc` 执行完整解码、黑帧、静音、EBU R128 响度与审片联系表检查，可同时输出 JSON/Markdown。`analyze quality/scenes/beats` 为本地实现；转录要求显式提供本地 faster-whisper 模型，绝不静默联网下载。歌曲识别在没有本地指纹库时明确返回插件需求。

## 多序列与标题模板

```bash
facut --project demo sequence save main
facut --project demo sequence duplicate main highlight
facut --project demo sequence render-all --preset youtube-4k --output-dir renders/sequences
facut text presets
facut --project demo text import-csv titles.csv --template documentary-lower-third
```

命名 sequence 保存时间线快照并共享素材/代理池，可在一个工程维护主片、精华版等交付版本。文字模板支持阴影、半透明底、安全区、对齐、字距和模板专属矢量图层。

一个主序列可一次生成横版、竖版与方形派生文件：

```bash
facut delivery-presets --json
facut --project demo deliver --preset youtube-4k-sdr --also bilibili-4k,shorts-9x16,community-1x1 --output-dir renders/delivery --hardware auto
```

YouTube 预设保留工程原帧率，标准帧率 4K SDR 使用 45Mbps、高帧率使用 68Mbps，输出 AAC 384kbps、BT.709、4:2:0 和 MP4 Fast Start。B站预设明确标记为 FACUT 创作者工作流默认值，不伪称平台官方上限。竖版输出当前使用确定性画面适配并发出警告；主体跟踪式自动重构图尚未实现。

### 当前明确限制

- 最终编码主路径为 H.264/AAC；H.265、AV1、ProRes 等后端仍未开放。
- 叠加轨间转场、带位置偏移的非 normal 混合、混合模式与自定义 mask 同时使用会明确返回 `NOT_IMPLEMENTED`。
- 调整层当前接受单 FFmpeg 节点效果；像素化这种多节点效果暂不接受。
- 画面关键帧当前为线性 x/y/scale/rotation；光流补帧和高级稳定模型尚未实现。
- ASR 需要用户提供本地模型；歌曲识别需要本地指纹数据库插件。
- 旅拍语义分类目前以元数据/路径规则为确定性底座；视觉语义搜索、人物识别、故事生成和 B-roll 覆盖诊断仍需后续本地视觉索引插件。
- GPX/KML 路线动画、主体跟踪自动重构图、OpenTimelineIO/FCPXML 交换尚未实现。
- 增量缓存会对复杂工程安全降级为整片渲染；跨转场区间复用是后续优化。

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
├── analysis/         质量、场景、节拍和可选本地 ASR
├── qc/               解码、黑帧、静音、响度及审片板
├── render/           滤镜图、片段缓存、硬件和 FFmpeg 后端
├── transitions/      插件式转场
├── effects/          插件式视频/音频效果
├── subtitles/        SRT/VTT 解析、编辑与 ASS 编译
├── config.py         跨平台配置
├── exceptions.py     领域错误和稳定退出码
├── logging_config.py 轮转日志
└── responses.py      JSON 响应协议
```

Windows 发行物通过 PyInstaller 打包为单文件 `facut.exe`，并内置 FFmpeg/FFprobe；源码安装方式用于开发和测试。
