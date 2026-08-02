"""Deterministic recording prompt plans for local voice profiles."""

from __future__ import annotations

from typing import Any

from .models import VoiceProfile


_ZH_PROMPTS = [
    ("opening", "大家好，今天我们从城市的清晨出发，慢慢看看沿途发生的故事。"),
    ("observation", "刚走出车站的时候，天气比预想中更舒服，风也没有那么大。"),
    ("date-time", "现在是二零二六年八月二日，下午三点四十五分。"),
    ("price-number", "这顿饭一共一百二十八元，三个人吃刚刚好。"),
    ("navigation", "前方两百米向右转，穿过路口以后就能看到入口。"),
    ("question", "你觉得这里最值得停下来的地方是什么？"),
    ("reflection", "如果时间充足，我会更愿意沿着河边继续走一段。"),
    ("explanation", "从远景到人物近景，画面的情绪在这一刻发生了变化。"),
    ("ambient-sound", "有些瞬间不需要太多解释，保留现场的声音反而更真实。"),
    ("family-reaction", "孩子第一次看到雪的时候很安静，过了几秒才开心地跑过去。"),
    ("room-review", "房间不算大，但采光、收纳和窗外的景色都很舒服。"),
    ("food-description", "这一口先是微甜，随后有一点酸，最后留下淡淡的香味。"),
    ("transport", "我们乘坐高铁、地铁和出租车，终于在晚上八点抵达酒店。"),
    ("itinerary-list", "今天的计划包括博物馆、老街、咖啡店和日落观景台。"),
    ("sound-list", "镜头里的笑声、脚步声和风声，都是这趟旅行的一部分。"),
    ("english", "Please keep the original frame rate and export the final video in 4K."),
    ("mixed-alphabet", "A、B、C三个方案里，我更喜欢节奏自然、信息清楚的第二个。"),
    ("counting", "从一到十依次是：一、二、三、四、五、六、七、八、九、十。"),
    ("place-names", "上海、北京、广州、成都、西安，每座城市都有不同的生活节奏。"),
    ("closing", "最后我们在天黑之前回到车里，也给今天的行程留下一点余韵。"),
]

_DELIVERIES = ("neutral", "conversational", "informative", "warm", "restrained")

_STYLE_CAPSULES = [
    (
        "natural-vlog",
        "natural",
        "今天其实没有安排特别具体的行程，我们就沿着这条路慢慢走。风不算大，路边也挺安静，偶尔停下来看看，感觉这样反而更舒服。",
    ),
    (
        "clear-information",
        "broadcast",
        "现在是下午四点二十分，我们已经到达今天的第二个目的地。接下来会先参观主展厅，然后沿着东侧通道前往观景平台。",
    ),
    (
        "friendly-chat",
        "chat",
        "我跟你说，刚才从外面看还觉得这里挺普通的，结果一走进来才发现里面真的很大。你看前面那个位置，视野是不是特别好？",
    ),
    (
        "light-comedy",
        "comedy",
        "出发之前我还信心满满，觉得今天肯定不会走错路。结果十分钟以后，我们三个人站在同一个路口，拿着三部手机，指出了三个不同的方向。",
    ),
    (
        "real-excitement",
        "excited",
        "快看前面，真的到了！刚才转过那个弯的时候还什么都看不见，结果一下子整个景色都出来了。这个比照片里壮观多了，今天真的没有白来。",
    ),
]


def build_recording_plan(
    profile: VoiceProfile, *, target_minutes: int = 10, script: str = "mandarin-balanced-v1"
) -> dict[str, Any]:
    if script not in {"mandarin-balanced-v1", "vlog-style-capsules-v1"}:
        raise ValueError(f'Unknown recording script "{script}".')
    if target_minutes < 1 or target_minutes > 60:
        raise ValueError("target_minutes must be between 1 and 60.")
    prompts = []
    if script == "vlog-style-capsules-v1":
        target_minutes = 2
        for index, (category, delivery, prompt_text) in enumerate(_STYLE_CAPSULES):
            prompts.append(
                {
                    "id": f"style_{delivery}",
                    "text": prompt_text,
                    "category": category,
                    "delivery": delivery,
                    "take": 1,
                }
            )
    else:
        target_prompts = max(10, target_minutes * 10)
        for index in range(target_prompts):
            category, prompt_text = _ZH_PROMPTS[index % len(_ZH_PROMPTS)]
            prompts.append(
                {
                    "id": f"prompt_{index + 1:03d}",
                    "text": prompt_text,
                    "category": category,
                    "delivery": _DELIVERIES[index % len(_DELIVERIES)],
                    "take": index // len(_ZH_PROMPTS) + 1,
                }
            )
    return {
        "version": "1.0",
        "profile_id": profile.id,
        "language": profile.language,
        "style": profile.style,
        "script": script,
        "target_minutes": target_minutes,
        "recording": {
            "sample_rate": 48000,
            "sample_width_bits": 16,
            "channels": 1,
            "format": "wav-pcm",
        },
        "coverage": {
            "categories": list(dict.fromkeys(item["category"] for item in prompts)),
            "delivery_modes": list(dict.fromkeys(item["delivery"] for item in prompts)),
            "balanced_short_plan": script == "mandarin-balanced-v1" and len(prompts) == 10,
            "style_capsules": script == "vlog-style-capsules-v1",
        },
        "prompts": prompts,
        "instructions": [
            "Use a quiet, low-reverb room and keep microphone distance fixed.",
            "Record one prompt per file; repeat a prompt if clipping or interruption occurs.",
            "Do not include music, another speaker, or synthetic denoise artifacts.",
            "For style capsules, understand the sentence first and perform it naturally instead of reading every word evenly.",
        ],
    }
