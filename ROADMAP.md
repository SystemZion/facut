# FACUT VLOG roadmap

FACUT 的主线是日常 VLOG、旅行、City Walk、亲子出行与周末记录。演唱会专项和花哨转场不占用近期优先级。

## 0.6.1 — AI 口播闭环

- 无 ID `voice studio`、任意命名、多档案、quick/recommended/styles 录制模式。
- ID/alias/唯一显示名解析、工程/全局默认声音，现有 WAV 非破坏保留。
- `voice serve` 常驻本地模型、CUDA 强制检查、`voice say` 自动风格与多候选试听。
- 严格 `narration generate/review/synthesize/apply`，证据约束、人工批准、单修订和撤销。
- 口播独立音频轨、原声保留、音乐闪避 attack/release 实际进入渲染图。
- Recipe validate/plan/build、JSON Schema、CLI 与 JSON-RPC Agent 接口。

## 0.7 — CutGraph 专业执行引擎

- 不自建、下载或集成本地视觉模型；画面理解由操作 FACUT 的 AI 完成。
- LRF 原生代理扫描、候选评分、歧义阻断、预览走代理、最终输出强制回原片。
- clip transform/motion/speed/freeze/reverse 与 step/linear 速度曲线。
- 原视频音轨处理、高通、降噪、压缩、限制、交叉淡化、两遍响度目标和终端波形。
- Git 式 CutGraph 分支历史、语义 diff、restore、快速前进 accept 与变化区间 A/B 预览。
- 多轨、字幕、文字与独立音轨进入片段级增量缓存；自动 QC 增加定格、时长与旋转检查。

## 0.7.2 — RenderGraph v2

- 将转场建模为独立边界节点，分离画面、原声、音乐、口播、字幕和文字缓存。
- 修改 25 分钟工程片尾 5 秒时，目标缓存复用率不低于 95%。
- 后台渲染队列、取消、恢复、失败节点重试和变化区间 A/B 审片。

## 0.8 — MixLab 与 VLOG 自动混音

- 审核优先的 `mix analyze/plan/apply`，逐轨增益、闪避曲线、理由和置信度。
- 对话增强、风噪/低频/混响处理、环境声延续与更完整的 J-cut/L-cut 工作流。
- 每个口播区间生成调整前/后 A/B 试听，明确 apply 后才写入可撤销分支。

## 0.9 — 旅行包装与多平台派生

- GPX/KML 地图模板、Day/地点卡、旅程数据动画和照片缓推。
- 主体跟踪横竖自动重构图。
- 一个主序列派生 YouTube/B站、Shorts、预告、无字幕母版和字幕文件。
- 自动章节、封面候选和完整 QC/发布包。
