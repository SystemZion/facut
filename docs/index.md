# Documentation

[English home](../README.md) · [中文首页](../README.zh-CN.md)

| Guide | What it covers |
|---|---|
| [Getting started](getting-started.md) | Windows portable installation; Python installation; an explicit first edit |
| [Director Studio](director-studio.md) | Loopback Review Room, requests, patches, real before/after renders |
| [Agent API](agent-api.md) | Schema discovery, batches, JSON-RPC, evidence-first travel workflows |
| [Limitations](limitations.md) | Models, codecs, HDR, exchange fidelity, rendering fallbacks |
| [Architecture](architecture.md) | Modules, tests, safe subprocess execution |
| [Reproducible demo](../examples/homepage-demo/README.md) | Synthetic input, actual CLI renders, approved patch |
| [Full Chinese handbook](handbook.zh-CN.md) | Original detailed documentation; historical examples and recipes |

## Chinese handbook sections

[Installation](handbook.zh-CN.md#安装) ·
[Commands](handbook.zh-CN.md#命令概览) ·
[JSON protocol](handbook.zh-CN.md#json-协议) ·
[Voice](handbook.zh-CN.md#多数字人声音档案) ·
[Recipes](handbook.zh-CN.md#声明式-recipe) ·
[Exchange](handbook.zh-CN.md#otio--fcpxml-交换) ·
[Proxies and rendering](handbook.zh-CN.md#代理增量渲染和进度) ·
[CutGraph](handbook.zh-CN.md#cutgraph-分支剪辑历史) ·
[QC](handbook.zh-CN.md#qc分析和标记)

Use the installed version's `facut schema commands`, per-action schemas, and
`--help` as the parameter authority. CLI commands and the Agent action registry
are not assumed to be identical: when an action is not advertised, use the
documented CLI rather than inventing a JSON-RPC action.
