# FACUT VLOG roadmap

FACUT 的近期主线是日常 VLOG、旅行、City Walk、亲子出行与周末记录。演唱会专项能力暂不占用主路线优先级。

## 0.5.3 — 本机人声采集

- `voice record` 本机浏览器录音室：麦克风选择、电平、逐句提示、试听、重录、保存时 QC。
- Windows 声音库规范为 `%LOCALAPPDATA%\facut\voices`，旧目录非破坏迁移。
- AI 可发现交互式录音动作，同时继续通过 `voice profile import` 确定性导入已有 WAV。

## 0.5.2 — 多声音档案底座

- 多说话人/多风格档案、独立授权、私有样本复制和内容哈希去重。
- 录音提示计划、PCM WAV 质量检查、可恢复删除/恢复。
- 本地 `facut-voice-provider/1.0` 接口；尚未捆绑具体声音克隆模型。

## 0.5.1 — 可相信的确定性底座

- 修复主体重构图关键帧的实际 FFmpeg 渲染。
- 最终 H.264 强制 `yuv420p`，交付预设写入 BT.709，QC 检查像素格式、色彩、VFR 和声道。
- 公开 JSON 保留有界 FFmpeg 错误详情。
- 语义观察 ID 唯一，B-roll 诊断直接消费语义 `a-roll` 标签。
- `narration suggest` 根据视觉观察和时间线生成口播角度、证据、置信度与可审阅初稿。

## 0.6 — 常见 VLOG 自动精剪

- `weekend-vlog`、`citywalk`、`family-trip`、`food-vlog`、`daily-life`、`talking-vlog` 模板。
- 按日期、地点、设备、远中近景、人物反应和现场声整理素材。
- 先组织口播/现场讲话，再自动寻找 B-roll 覆盖；诊断重复镜头、口播过长和连续性缺口。
- 对话增强、风噪/低频处理、环境声延续、音乐闪避、J-cut/L-cut 和响度交付。
- 口播草稿可由外部 Agent 或显式配置的本地语言模型改写；所有句子保留画面证据和置信度，不编造地点、人物、日期和价格。
- 增加 `narration generate`：Agent/本地语言模型根据画面、故事结构、已有口播和用户偏好生成多个可比较版本，默认只形成计划，不直接写入时间线。

## 0.7 — 本地视觉与授权人声

- 可插拔本地视觉 Worker：场景、镜头类型、人物反应、质量、重复镜头和主体轨迹。
- 支持多个数字人声音档案；一个人可建立自然 VLOG、纪录片、轻快、沉稳等不同风格，不同获授权成员也可各自建立档案。
- `voice profile create/import/validate/list/delete/restore` 与 `voice record-plan` 仅接受用户拥有权利的人声样本，并要求每个说话人单独记录授权。
- `narration synthesize --voice-profile <ID>` 先生成逐句试听，可单句重生成、调整语速/情绪，再由用户确认写入时间线。
- 人声配置和样本默认只保存在本机，可删除、可审计；未授权的第三方声音不建立克隆配置。
- 没有个人声音配置时降级到系统/本地通用 TTS，不伪装成用户本人。
- 完整录入、存储、Agent Schema 和质量门槛见 [VOICE_PROFILES.md](VOICE_PROFILES.md)。

## 0.8 — 旅行包装和多平台派生

- GPX/KML 路线、Day/地点卡、照片缓推和旅程数据动画。
- 一个主序列派生 YouTube/B站横版、Shorts 竖版、预告、无字幕母版和字幕文件。
- 自动生成章节、封面候选和交付 QC 包。
