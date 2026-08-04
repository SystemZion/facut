# FACUT VLOG roadmap

FACUT 的主线是日常 VLOG、旅行、City Walk、亲子出行与周末记录。演唱会专项和花哨转场不占用近期优先级。

## 0.6.1 — AI 口播闭环

- 无 ID `voice studio`、任意命名、多档案、quick/recommended/styles 录制模式。
- ID/alias/唯一显示名解析、工程/全局默认声音，现有 WAV 非破坏保留。
- `voice serve` 常驻本地模型、CUDA 强制检查、`voice say` 自动风格与多候选试听。
- 严格 `narration generate/review/synthesize/apply`，证据约束、人工批准、单修订和撤销。
- 口播独立音频轨、原声保留、音乐闪避 attack/release 实际进入渲染图。
- Recipe validate/plan/build、JSON Schema、CLI 与 JSON-RPC Agent 接口。

## 0.7 — 本地视觉理解与 VLOG 故事

- 混合设备摄取与旋转、VFR、时区、GPS、HDR/Log 元数据统一。
- 可插拔本地视觉 Worker：场景、镜头类型、人物反应、质量、重复镜头和主体轨迹。
- faster-whisper ASR、说话人分离、可编辑中英字幕和文本剪辑。
- weekend-vlog、citywalk、family-trip、food-vlog、daily-life、talking-vlog 故事模板。
- 广播剪辑优先，随后进行 B-roll 覆盖、连续性和重复镜头诊断。

## 0.8 — 专业精修与增量效率

- 对话增强、风噪/低频/混响处理、环境声延续、J-cut/L-cut 与平台响度闭环。
- Log/HDR 色彩管理、镜头匹配、肤色保护、畸变与地平线处理。
- 跨转场、多轨和字幕的片段级增量缓存，只重渲变化区间。
- 代理、ASR、波形、缩略图和视觉分析的后台队列、暂停和恢复。

## 0.9 — 旅行包装与多平台派生

- GPX/KML 地图模板、Day/地点卡、旅程数据动画和照片缓推。
- 主体跟踪横竖自动重构图。
- 一个主序列派生 YouTube/B站、Shorts、预告、无字幕母版和字幕文件。
- 自动章节、封面候选和完整 QC/发布包。
