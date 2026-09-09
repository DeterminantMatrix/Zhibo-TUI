"""P0 骨架的假数据 — 44 个虚构关注项 + 状态模拟器素材。

全部为虚构名称，不对应任何真实关注列表；接后端时整个模块被
MonitorService 快照替换。
"""
from __future__ import annotations

import random

TAGS = ["全部", "游戏", "体育", "LOL", "键政", "冒险岛", "ASMR", "Dance", "POE", "音乐"]
PLATFORMS = ["douyu", "huya", "bilibili", "twitch", "youtube", "douyin", "fs1"]
PLUGIN_BY_PLATFORM = {
    "douyu": "streamlink",
    "huya": "streamget",
    "bilibili": "streamlink",
    "twitch": "streamlink",
    "youtube": "yt-dlp",
    "douyin": "streamget",
    "fs1": "fs1",
}
QUALITIES = ["best", "原画", "蓝光8M", "高清"]

_NAMES = [
    "夜色", "小蘑菇", "阿柴", "挂月", "泡面头", "星野", "老K", "路过的观众",
    "碎星", "南音", "薄荷糖", "铁憨憨", "云游四海", "九尾", "半仙", "橘子汽水",
    "深夜食堂", "北风", "青柠", "黑巧克力", "老白", "六月", "清欢", "阿泽",
    "松鼠", "老周", "灯下黑", "一曲长歌", "竹林听雨", "老猫", "星辰大海", "糖霜",
    "野火", "阿默", "长夜", "白鹭", "拾光", "老徐", "南风知我意", "茶余饭后",
    "折耳根", "老唐", "星河", "一叶知秋",
]

_TITLES = [
    "【 infinite 】冲冲冲", "闲聊杂谈，来了就是朋友", "排位上分，目标王者",
    "欧冠：皇家马德里 VS 国际米兰", "怀旧服开荒", "ASMR | 助眠 | 轻语",
    "新图速通 全收集", "周末歌回，点歌箱已开", "peaceful night chill",
    "每日一练，菜就多练", "重开一局", "光影与远方",
]


def build_followers(count: int = 44) -> list[dict]:
    """构造 count 个虚构关注项；live 状态随机，供表格与模拟器使用。"""
    random.seed(20260909)  # 固定种子：骨架演示效果每次一致
    followers: list[dict] = []
    platforms = random.choices(PLATFORMS, k=count)
    for idx in range(count):
        platform = platforms[idx]
        tag_pool = [t for t in TAGS if t != "全部"]
        tags = random.sample(tag_pool, k=random.randint(1, 2))
        live = random.random() < 0.22
        followers.append(
            {
                "idx": idx,
                "name": _NAMES[idx % len(_NAMES)],
                "platform": platform,
                "tags": tags,
                "title": random.choice(_TITLES) if live else "",
                "quality": random.choice(QUALITIES),
                "plugin": PLUGIN_BY_PLATFORM[platform],
                "live": live,
                "last_check": "--:--:--",
                "enabled": random.random() > 0.08,
            }
        )
    return followers


def fake_title(platform: str) -> str:
    return random.choice(_TITLES)
