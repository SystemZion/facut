# Opt-in phase timing

StageTimer uses monotonic perf_counter elapsed wall time. The caller explicitly
opts into output and persistence; the helper has no file or CLI side effects.
Use `with timer.stage("probe"):` around real work, then `timer.report()`.
Schema: facut.performance.v1. Reports are detached snapshots at call time.
Total includes inter-stage gaps. Durations are inclusive: never sum nested or
overlapping stages. Failed stages record elapsed time and re-raise the original
exception. Use one timer per operation/thread.

mark(name, status, reason=...) supports not_measured, not_applicable and cached;
wall_seconds is null, not a fabricated zero. Cached means reused work was not
timed; measure cache lookup separately when useful. An in-flight stage is
running with null duration until exit. ffmpeg_metrics is null: child CPU and
FFmpeg benchmark metrics are not collected. Subprocess wall time includes
startup, decoding, filters, encoding and I/O; it is NOT isolated AI inference.

Use synthetic fixtures, record exact commands/FFmpeg version and cache/hardware
assumptions. A tiny synthetic sample is not a production 4K speedup benchmark.
