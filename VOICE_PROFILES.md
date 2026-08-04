# FACUT 多数字人声音档案设计

## 当前实现状态

0.6.1 已实现多档案、任意命名、ID/alias/唯一显示名解析、工程默认声音、三种短录制模式、断点续录、五种语气、多候选试听、常驻 GPU 服务及审核后时间线应用。CosyVoice3 模型通过 `facut download voice_model` 单独下载，不捆绑进 GitHub 或主 EXE。档案静态加密仍未实现。

## 目标

FACUT 将“口播文字生成”和“数字人声音合成”分成两个可检查阶段：

1. `narration generate` 根据画面证据生成多个口播文字候选。
2. `narration synthesize` 使用明确选择的声音档案逐句合成。
3. 用户试听并确认后，才把音频、字幕和闪避参数写入确定性时间线。

默认不调用在线服务，不把原始人声上传到网络，也不根据声音猜测身份。

## 多声音档案

每个档案使用稳定 ID，而不是文件名：

```text
voice_01_SELF_NATURAL
voice_02_SELF_DOCUMENTARY
voice_03_FAMILY_MEMBER_A
```

一个真实说话人可以有多个风格档案，例如自然 VLOG、轻快、美食、纪录片和沉稳旁白。不同说话人的档案必须分别录入样本和授权，不能共用一次授权记录。

档案至少保存：

- 显示名称、说话人 ID、语言和口音；
- 风格、建议语速和可用情绪范围；
- 模型提供方、模型版本和训练参数哈希；
- 样本文件哈希、录音质量结果和授权记录；
- 创建时间、最近验证时间和可删除状态；
- 本地存储位置，不把绝对路径暴露给普通 Agent 查询。

## 录入流程

建议命令：

```powershell
facut voice profile create "我的自然口播" --speaker self --language zh-CN --style natural-vlog --consent self --consent-statement "这是我的本人声音，我授权在本机用于 FACUT 口播合成。"
facut voice record <VOICE_ID> --target-minutes 10 --script mandarin-balanced-v1
facut voice profile import <VOICE_ID> D:\VoiceSamples\take-001.wav D:\VoiceSamples\take-002.wav
facut voice profile validate <VOICE_ID> --recommended-seconds 600
facut --json voice profile list
facut voice provider configure D:\FACUT\voice-runtime\.venv\Scripts\facut-cosyvoice-provider.exe
facut voice synthesize <VOICE_ID> "今天我们出去走走。" --output narration.wav
facut voice synthesize <VOICE_ID> "今天我们出去走走。" --delivery reflective --takes 3 --output narration.wav
facut voice styles --json
facut voice record <VOICE_ID> --script vlog-style-capsules-v1
facut voice synthesize <VOICE_ID> "今天我们出去走走。" --style natural,broadcast,chat,comedy,excited --output narration.wav
facut voice studio --mode recommended
facut voice alias set <VOICE_ID> zion
facut voice default set zion --scope project
facut voice serve start --device cuda --require-cuda
facut voice say "今天我们出去走走。" --voice zion --style auto --takes 2 --output narration.wav
```

`voice synthesize` 会根据语气选择匹配度更高的参考片段，去除参考音频首尾近静音，按语义切分长文并插入不同长度的句间停顿。默认 `natural-vlog` 使用 CosyVoice3 的自然语言指令控制；`--delivery reference` 可用于与旧式纯零样本生成进行 A/B 对比。

`vlog-style-capsules-v1` 是可选增量录音，不替换已有原始样本。它包含自然、播音、聊天、搞笑、激动五条自由度较高的长句，总目标约 2 分钟；录音导入时同时保存 `category` 与 `delivery`，供后续确定性参考选择使用。

`voice studio` 启动仅绑定 `127.0.0.1` 的本地录音页，提供麦克风选择、实时电平、逐句提示、试听、重录和保存时 QC。默认 `recommended` 只需约 90–120 秒；`quick` 为 3 条试用，`styles` 为 5 条风格增强。更多录音仍能提高稳定性，但不是使用 0.6.1 的前置条件，现有样本不会被要求重录。

录音规格：

- 当前版本接受 48 kHz、16-bit、单声道 PCM WAV；
- 安静且混响较低的房间，麦克风距离和增益保持一致；
- 包含常见数字、日期、中英文名称、疑问句、陈述句和不同句长；
- 不要混入背景音乐、降噪伪影或其他人的声音。

当前 FACUT 自动检查 PCM 格式、时长、采样率、声道、削波、RMS 电平和数字静音比例；出现硬错误的片段不进入档案。底噪频谱、混响、重复句、转录一致性和音素覆盖仍在后续 QC 计划中。

## 合成与时间线

```powershell
facut narration generate --style natural-vlog --variants 3 --output narration-plan.json
facut narration synthesize narration-plan.json --voice-profile <VOICE_ID> --preview-dir previews\voice
facut narration apply narration-plan.json --take approved-takes.json
```

每句可以独立选择声音档案、语速、停顿和表现强度。合成后自动执行：

- 逐句试听与单句重生成；
- 时长预测及与画面的对齐建议；
- 48 kHz 输出、响度统一、限制器和爆音检查；
- 音乐闪避、环境声延续、J-cut/L-cut；
- 同步生成可编辑字幕和来源清单。

默认不通过暴力变速把长口播塞进短镜头；优先建议缩短文字、延长 B-roll 或调整停顿。

## Agent 接口

计划增加以下可发现动作：

```text
voice.profile.create
voice.profile.record-plan
voice.record
voice.profile.import
voice.profile.validate
voice.profile.list
voice.profile.delete
narration.generate
narration.synthesize
narration.apply
```

所有生成动作支持 `--json`、`--dry-run`、稳定错误码和内容哈希。`narration.generate` 返回画面证据和置信度；`narration.synthesize` 返回声音档案 ID、模型版本、逐句音频哈希和质量报告。

## 授权与安全

- 仅允许录入本人声音，或已取得明确授权的家庭成员、员工和配音人员声音。
- 每个说话人单独确认授权范围、允许的平台和撤销方式。
- 声音档案与原始录音默认保存在本机用户数据目录；当前尚未实现静态加密。打包工程时默认不包含声音档案。
- 支持一键删除档案、样本、缓存和派生声音；历史工程保留“声音已不可用”的可审计状态。
- 不允许根据公开视频偷偷建立第三方声音档案，也不提供规避授权检查的开关。
- 导出清单记录“合成声音”、档案 ID 和模型版本，便于团队审片和平台合规。

## 第一版验收标准

- 同一台机器可保存、列出、选择和删除至少 10 个声音档案。
- 同一说话人的两个不同风格档案可以被明确区分和选择。
- 录音 QC 能阻止削波、严重噪声、严重混响和转录不匹配的样本。
- 口播计划的每一句可以单独试听、重生成、批准或拒绝。
- 未经 `apply` 不修改工程；批量应用只产生一次可撤销修订。
- 删除声音档案后不能继续合成，但已有成片和授权审计记录不被伪造或静默删除。
