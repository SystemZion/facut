# facut

[![tests](https://github.com/SystemZion/facut/actions/workflows/tests.yml/badge.svg)](https://github.com/SystemZion/facut/actions/workflows/tests.yml)
[![release](https://img.shields.io/github/v/release/SystemZion/facut)](https://github.com/SystemZion/facut/releases/latest)

**facut**（Fast AI Cut）是一款面向 AI Agent、自动化脚本与高级用户的非破坏性命令行视频编辑器。它使用稳定素材 ID、结构化工程、可组合子命令及统一 JSON 返回值，让复杂剪辑既能由人操作，也能可靠地被程序调用。

当前开发版本 `0.8.1` 稳定了质量优先的 Vlog Director：公开 Agent 可原子应用 StoryGraph，自动硬件选择会试跑编码器，事件链保持同一人物的“发生—恢复—结果”，标题模板遵循显式参数优先级，带转场时间线也能复用未变化渲染节点。它不会内置本地视觉模型，也不会用 Token 限制跳过有效素材。

最短工作流：

```powershell
facut vlog prepare D:\Trip --project D:\Trip-Project
facut --project D:\Trip-Project vlog inspect next --json
facut --project D:\Trip-Project vlog observe observations.json
facut --project D:\Trip-Project vlog plan --style comedy-vlog --target-duration 480
facut --project D:\Trip-Project vlog preview --all-candidates
facut --project D:\Trip-Project vlog refine candidate-narrative --auto
facut --project D:\Trip-Project vlog build candidate-narrative --preset youtube-4k -o D:\Trip-Final\final.mp4
```

`vlog run ... --auto` 也遵守同一质量门：缺少外部视觉观察或 ASR 中存在待复审词时返回 `REVIEW_REQUIRED`，不会伪造成功。字幕正文使用统一易读字体；科技、人文、风景、喜剧、家庭和美食标题通过逻辑字体角色匹配本机已授权字体，最终渲染前强制检查缺字。

## 安装

Windows 普通用户可从 [GitHub Releases](https://github.com/SystemZion/facut/releases/latest) 下载单文件 `facut.exe`。源码开发需要 Python 3.11 或更高版本，以及可在 `PATH` 中找到的 FFmpeg/FFprobe。

首次下载 EXE 后可让 FACUT 安装自身、替换旧版并写入当前用户 PATH：

```powershell
.\facut.exe install --exclude model
# 或同时下载语音与字幕模型：
.\facut.exe install --model-directory D:\FACUT\models
```

之后更新不需要手工覆盖文件：

```powershell
facut update --check
facut update
```

更新从 `SystemZion/facut` 最新 GitHub Release 断点续传 `facut.exe`；若正在运行的正是已安装 EXE，FACUT 会在当前进程退出后完成替换。

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

### 可选本地 AI 模型

大模型权重不会提交到 GitHub，也不会捆绑进 `facut.exe`。FACUT 会把模型单独下载到本机，校验固定的文件大小和 SHA-256；中断的传输保留为可续传的 `.part` 文件：

```powershell
facut download voice_model
facut download srt_model
facut models status
```

默认 `--source auto` 会先对官方源及镜像做小流量测速，拒绝 0 字节响应，并优先选择支持 HTTP Range 续传的最快来源。`--jsonl` 输出结构化测速/进度事件，`--directory D:\FACUT\models` 可把模型放到其他磁盘；网络中断后再次运行原命令即可继续。完整校验后会写入快速收据，日常检查无需再次哈希数 GB 权重；`--verify` 可强制重跑完整 SHA-256 审计。

已有模型无需移动或复制，可直接链接任意目录：

```powershell
facut models link srt_model D:\MediaModels\whisper-turbo
facut models link voice_model D:\FACUT\models\Fun-CosyVoice3-0.5B-2512
facut models path D:\FACUT\models
```

字幕识别会在导入 `faster-whisper` 前自动发现 pip 安装的 cuDNN/cuBLAS DLL，并在结果和 `doctor --json` 中报告实际 CUDA 设备数。模型始终使用本地目录和 `local_files_only=True`，不会因 Hugging Face 校验或项目目录清理而失效。

单文件 EXE 为控制安装体积不会内嵌 Python、CTranslate2、cuDNN 和模型权重；执行 ASR 时会自动桥接已经安装 `faster-whisper` 的外部 Python。可用 `FACUT_ANALYSIS_PYTHON=D:\Python311\python.exe` 或配置文件 `tools.analysis_python` 固定解释器，避免命中错误的 Python 环境。

安装 `srt_model` 后，字幕识别默认自动使用它：

```powershell
facut analyze transcript video.mp4 --language zh --save
```

## 命令概览

当前可执行的公共接口：

```text
facut init/import/ingest/inspect/install/update/download/models/help
facut timeline/clip/transition/effect/audio
facut proxy/analyze/qc/marker
facut subtitle/text
facut preview/render/deliver/delivery-presets/sequence
facut semantic/story/broll/map/reframe/exchange/schema
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

输出同时提供秒、时间码和帧编号。`proxy create/link/relink/scan/status` 在同一个工程内维护原片/代理关系；预览自动使用在线代理，最终渲染自动回到原片。`proxy scan --link` 会识别 `_LRF`、`-LRF`、`.LRF` 与 `_proxy`，拒绝零字节/损坏候选，并在歧义时返回 `PROXY_AMBIGUOUS`。

顺序组装可使用 `timeline add <MEDIA_ID> --track V1 --append`，无需每次查询总时长；显式 `--out` 超过探测时长不满一帧时会钳制，亚帧缝隙/重叠会自动吸附并写入片段警告。脚本只需素材 ID 时可用 `facut import ... --porcelain`。

画面和速度处理直接写入非破坏时间线：

```powershell
facut --project vlog clip transform clip_01 --crop 120:80:3600:2000 --scale 1.08
facut --project vlog clip motion clip_01 --preset slow-push --intensity 0.35
facut --project vlog clip speed clip_01 --rate 2
facut --project vlog clip speed clip_01 --reverse
facut --project vlog clip speed clip_01 --curve speed.json
```

速度曲线使用源片段相对秒数，支持 `step` 和确定性采样的 `linear`：`{"version":"1.0","mode":"linear","steps":8,"points":[{"at":0,"rate":1},{"at":2,"rate":2},{"at":4,"rate":0.75}]}`。曲线节点会进入缓存、Recipe 和 CutGraph；倒放同时反转画面与原音。

纯单轨、硬切、全文件、编码参数一致且无任何画面/声音处理的时间线，`render --fast-path auto` 会使用 FFmpeg concat stream-copy，零重编码、零画质损失；`--fast-path off` 可禁用。强制剪裁流复制必须显式使用 `--fast-path force`，结果会警告关键帧误差。

平台母带可直接闭环两遍响度和外部字幕烧录：

```powershell
facut render -o final.mp4 --loudness -14 --true-peak -1 --lra 11
facut render -o final.mp4 --burn-subtitle lyrics.ass
facut render -o clean-master.mp4 --no-project-subtitles
```

`--no-project-subtitles` 只影响本次输出，不修改工程中的字幕或标题。ASS 文件原样交给 libass，支持 `\\kf` 卡拉 OK 标签和系统用户字体。每次常规渲染都会把命令参数、滤镜图和 FFmpeg stderr 写到工程 `logs/render-*.log`；失败时终端显示末 20 行并返回日志路径。

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

它通过 stdin/stdout 交换逐行 JSON-RPC，同一进程复用已载入工程。握手会声明 `facut-agent/1.0`，AI 首先调用 `agent.capabilities` 获取稳定动作名，再用 `agent.schema` 读取单项参数约束：

```json
{"jsonrpc":"2.0","id":1,"method":"agent.capabilities"}
{"jsonrpc":"2.0","id":2,"method":"agent.schema","params":{"action":"semantic.search"}}
{"jsonrpc":"2.0","id":3,"method":"semantic.search","params":{"query":"一家人在海边看日落","limit":5}}
```

CLI 中相同信息可通过 `facut --json schema commands`、`facut --json schema action semantic.search` 获取。写工程的智能命令采用计划优先约定；`exchange.import`、`story.build` 和 `reframe.plan` 不带 `--apply` 时绝不修改工程。

## 视觉语义、故事与 B-roll

FACUT 接受任何视觉模型或 AI Agent 生成的结构化观察，不把大模型绑定进核心 EXE。观察必须引用已导入的稳定 `media_id`，并给出准确入出点、置信度和证据：

```json
{
  "provider": "local-vision-agent-v1",
  "observations": [{
    "media_id": "media_01ABC",
    "start": 12.4,
    "end": 18.8,
    "caption": "一家人在海边看日落",
    "tags": ["family", "sunset-window", "reaction"],
    "confidence": 0.93,
    "evidence": [{"type": "sampled_frames", "frames": [372, 465, 558]}]
  }]
}
```

```powershell
facut --project demo semantic index --observations vision-observations.json --json
facut --project demo semantic search "孩子第一次看到雪山的反应" --json
facut --project demo story build --style travel-documentary --target-duration 720 --json
facut --project demo story build --style travel-documentary --target-duration 720 --sequence youtube-main --apply --json
facut --project demo broll diagnose --maximum-aroll 8 --json
```

没有视觉观察文件时，索引仍可使用文件名和已保存元数据，但会明确返回限制。故事只选取有证据的区间；找不到某个叙事阶段时登记为 `missing_stages`，不会编造地点、人物或事件。

## GPX 路线与主体重构图

```powershell
facut map inspect route.gpx --json
facut map animate route.gpx --output route.mp4 --duration 8 --width 3840 --height 2160 --encoder h264_nvenc --json
facut --project demo reframe plan <CLIP_ID> subject-track.json --width 1080 --height 1920 --json
facut --project demo reframe plan <CLIP_ID> subject-track.json --width 1080 --height 1920 --apply --json
```

## 根据画面的口播建议

视觉观察完成索引后，FACUT 可以把画面证据映射到当前时间线，生成口播角度和可审阅初稿：

```bash
facut --json --project demo narration suggest --style natural-vlog --language zh-CN
```

每一句都返回时间范围、素材 ID、视觉摘要、建议角度、初稿、置信度、提供方和证据。0.6.1 可以把这些证据生成严格计划、通过授权的本地声音逐句合成候选，并在人工批准后写入时间线；不得加入证据中没有的人物、地点、日期或价格。未配置本地文本模型时只生成确定性的 evidence-grounded 草稿，其他 provider 返回 `PROVIDER_NOT_CONFIGURED`。完整说明见 [VOICE_PROFILES.md](VOICE_PROFILES.md)。

完整口播流程：

```powershell
facut voice studio --mode recommended
facut --project vlog voice alias set "Zion" zion
facut --project vlog voice default set zion --scope project
facut voice serve start --device cuda --require-cuda

facut --project vlog narration generate --style weekend-vlog --output narration.plan.json
facut --project vlog narration synthesize narration.plan.json --voice zion --style auto --takes 2 --preview-dir previews\narration
facut narration review narration.plan.json --line line_001 --status approved
facut --project vlog narration apply narration.plan.json --approved-only --duck-music --preserve-original
```

`narration apply` 在一个工程修订内增加独立口播轨。标记为 `role=music` 的音频轨会按口播区间编译带 attack/release 的音量闪避；相机原声不会被自动静音。整个工程修改可以用 `facut undo` 撤回。

## 多数字人声音档案

```powershell
facut --json voice profile create "我的自然口播" --speaker self --consent self --consent-statement "这是我的本人声音，我授权在本机用于 FACUT 口播合成。"
facut --json voice record-plan <VOICE_ID> --target-minutes 10 --output record-plan.json
facut voice record <VOICE_ID> --target-minutes 10
facut --json voice profile import <VOICE_ID> take-001.wav take-002.wav
facut --json voice profile validate <VOICE_ID>
facut --json voice profile list
facut --json voice provider configure D:\FACUT\voice-runtime\.venv\Scripts\facut-cosyvoice-provider.exe
facut --json voice provider status
facut --json voice synthesize <VOICE_ID> "今天我们出去走走。" --output narration.wav
facut --json voice synthesize <VOICE_ID> "今天我们出去走走。" --delivery natural-vlog --takes 3 --output narration.wav
facut --json voice styles
facut voice record <VOICE_ID> --script vlog-style-capsules-v1
facut --json voice synthesize <VOICE_ID> "今天我们出去走走。" --style natural,broadcast,chat,comedy,excited --output narration.wav
facut voice studio
facut voice studio --voice Zion --mode styles
facut voice alias set Zion zion
facut voice default set zion --scope project
facut voice say "今天我们出去走走。" --voice zion --style auto --takes 2 --output narration.wav
facut voice serve status --json
```

`voice record` 在 `127.0.0.1` 打开 FACUT 自带录音页，可选择麦克风、逐条朗读、试听、重录并在保存时执行 QC。浏览器把音频转换为 48 kHz、16-bit、单声道 PCM WAV，只发送给本机临时服务；完成后服务自动关闭。已有 PCM WAV 仍可通过 `voice profile import` 由 AI/CLI 批量导入。

本地 CosyVoice3 提供器默认使用自然版语气指令、按语义分句并加入自然停顿。面向用户和 Agent 的稳定版本为 `natural`（自然版）、`broadcast`（播音版）、`chat`（聊天版）、`comedy`（搞笑版）和 `excited`（激动版）；逗号分隔可一次生成多个版本。`--instruction` 可追加简短表演要求，`--takes 2` 或 `--takes 3` 可为每个版本生成多个候选。

原有平衡录音继续负责音色，不需要重录。可选的 `vlog-style-capsules-v1` 只增加 5 条、约 2 分钟风格参考；新样本会保存明确的风格标签，生成时优先匹配。未补录时五种版本仍可使用模型指令生成，但个性化语气相似度会较弱。

`voice studio` 不要求预先创建声音 ID。首次打开可直接输入任意名称并确认本人/已授权关系；`quick` 为 3 条快速试录，`recommended` 为 8 条常见 VLOG 句型，`styles` 为 5 条风格胶囊。档案内部继续使用稳定 ID，命令可使用唯一 alias 或唯一显示名称；重名时返回 `VOICE_AMBIGUOUS`，不会猜测。

`voice serve` 只绑定 `127.0.0.1`，使用随机令牌和本机状态文件。新版 CosyVoice provider 的 `--facut-voice-jsonl` 模式会在同一进程中保留模型；`--require-cuda` 下无法验证 CUDA 时直接失败，不静默回到 CPU。模型目录继续通过 `facut models link voice_model <path>` 独立配置，不进入 EXE 或 GitHub。

没有 NVIDIA GPU 时，可在用户配置的 `[voice]` 段设置外部 CPU PyTorch
`cpu_overlay`。此时 `--device auto` 会在一次性合成和常驻服务中统一选择
CPU，避免断开 eGPU 后导入 CUDA 运行时崩溃；需要 GPU 时使用
`--device cuda --require-cuda`，该组合绝不降级。`--device cpu` 与
`--require-cuda` 互相矛盾，会在启动模型前返回参数错误。overlay、模型和
个人声音样本始终位于用户配置的外部目录，不打入 EXE 或 GitHub。

## 声明式 Recipe

```powershell
facut --project vlog recipe validate vlog.json
facut --project vlog recipe plan vlog.json --output build-plan.json
facut --project vlog recipe build vlog.json --output final.mp4
```

Recipe 支持轨道及角色、素材区间、变换、效果、转场、口播计划、渲染和 QC 请求。`validate` 与 `plan` 不修改工程；`build` 把所有时间线命令作为一次原子修订执行，输出先写入同目录临时文件，渲染成功后才替换最终文件。机器契约可通过 `facut schema recipe`、`facut schema narration` 和 `facut schema action <name>` 查询。

`facut run` 同样可以调用上述 Agent action。纯时间线命令仍可使用 `atomic: true` 完整回滚；声音档案、试听文件和 Recipe 等跨域操作可能写入工程外部资源，因此批处理必须明确写 `atomic: false`。FACUT 会在一个常驻 JSON-RPC 子进程内依次执行，且不会把这些外部副作用伪装成可原子回滚。

0.5.3 支持多个独立授权档案、PCM WAV 样本复制和哈希去重、48k/单声道/削波/电平/静音 QC、可恢复删除和录音提示计划。Windows 默认声音库位于 `%LOCALAPPDATA%\facut\voices`；旧版重复目录会非破坏复制迁移并保留原文件。完整授权声明保存在本机私有档案中，普通 JSON 只返回声明哈希。声音合成使用显式配置的本地 `facut-voice-provider/1.0`；官方 CosyVoice3 提供器可离线调用已下载模型，未配置时返回 `NOT_IMPLEMENTED`，不会静默上传录音或伪造成功。

GPX 动画完全离线生成，不抓取或捆绑在线地图瓦片，输出真实可播放的视频。主体轨迹使用局部片段时间与归一化中心点 `cx/cy`；FACUT 对低置信度点过滤、平滑、限制裁切边界，并编译为确定性的 x/y 关键帧。主体检测和跟踪推理由外部视觉提供方完成，核心 EXE 负责验证和可重复执行。

## OTIO / FCPXML 交换

```powershell
facut --project demo exchange export --format otio --output edit.otio --json
facut --project demo exchange import edit.otio --json
facut --project demo exchange import edit.otio --apply --json
facut --project demo exchange export --format fcpxml --output edit.fcpxml --json
```

OTIO 是高保真主交换格式：FACUT 片段、变速、转场、文字、字幕和标记放在明确的 FACUT 元数据中并可往返。FCPXML 1.9 使用保守的 `asset-clip` 子集并返回损失报告；复杂 compound clip、generator 和 roles 不会被伪装成无损支持。

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
facut --project demo proxy scan --link
facut --project demo proxy status --json
facut --project demo preview timeline
facut --project demo render --output final.mp4 --incremental --jsonl-progress
```

增量渲染以素材状态和片段参数哈希缓存中间段。连续主视频轨可同时包含叠加视频、独立音频、字幕、文字和调整层；每个主片段只缓存与该区间相交的节点。转场目前仍会明确降级为完整渲染，不会给出虚假的缓存命中。渲染器强制以时间线终点封装并裁掉音视频尾巴。JSONL 进度包含阶段、百分比、输出时间、帧率、速度、ETA、当前片段和缓存命中。

## CutGraph 分支剪辑历史

```powershell
facut --project vlog history status
facut --project vlog history log --graph
facut --project vlog branch create faster-opening --switch
facut --project vlog history diff main..HEAD
facut --project vlog preview compare main HEAD --changed-only
facut --project vlog branch accept faster-opening --into main
```

撤销只移动当前分支指针，不删除未来版本；撤销后继续编辑会自动保存恢复分支。`restore` 把旧状态恢复成一个新提交。`branch accept` 仅允许安全快速前进，分叉时返回 `HISTORY_CONFLICT`，不会自动覆盖主版本。

`facut run`、JSON-RPC 时间线写操作、Recipe build 和口播应用默认从 `main` 自动进入 `agent/`、`recipe/` 或 `narration/` 实验分支。人工检查后再 `branch accept`；已在非主分支时继续沿当前实验分支工作。

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

YouTube 预设保留工程原帧率，标准帧率 4K SDR 使用 45Mbps、高帧率使用 68Mbps，输出 AAC 384kbps、BT.709、4:2:0 和 MP4 Fast Start。B站预设明确标记为 FACUT 创作者工作流默认值，不伪称平台官方上限。竖版输出可使用确定性画面适配；先应用 `reframe plan --apply` 后，会用主体轨迹关键帧保持主体构图。

### 当前明确限制

- 最终编码主路径为 H.264/AAC；H.265、AV1、ProRes 等后端仍未开放。
- 叠加轨间转场、带位置偏移的非 normal 混合、混合模式与自定义 mask 同时使用会明确返回 `NOT_IMPLEMENTED`。
- 调整层当前接受单 FFmpeg 节点效果；像素化这种多节点效果暂不接受。
- 画面关键帧当前为线性 x/y/scale/rotation；光流补帧和高级稳定模型尚未实现。
- ASR 需要用户提供本地模型；歌曲识别需要本地指纹数据库插件。
- FACUT 核心可验证和检索视觉观察，但当前不捆绑通用视觉模型；主体检测、人物识别和跟踪推理需要外部本地模型或 Agent 提供观察文件。
- HDR/PQ/HLG/Log 与 BT.2020 素材尚未实现自动 HDR→SDR tone-map；BT.709 交付预设会明确阻止这类素材，避免只改标签而产生错误颜色。
- GPX 已实现；KML、带在线地图瓦片或自动地名解析的地图样式尚未实现。
- OTIO 原生 JSON 支持高保真 FACUT 元数据往返；FCPXML 当前是明确标注损失的 1.9 保守子集。
- 增量缓存会在存在转场时安全降级为整片渲染；跨转场边界复用是后续优化。

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
├── intelligence/     语义索引、故事计划和 B-roll 诊断
├── travel/           GPX 路线视频和主体重构图
├── exchange/         OTIO/FCPXML 导入导出
├── agent/            能力发现和逐动作 JSON Schema
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
