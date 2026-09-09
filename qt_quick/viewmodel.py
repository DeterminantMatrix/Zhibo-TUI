"""兼容层：视图模型纯函数已迁至 zhibo.viewmodel（webui 共用）。"""
from zhibo.viewmodel import (  # noqa: F401
    PLATFORM_DISPLAY_NAMES,
    SORTABLE_COLUMNS,
    matches_snapshot,
    sort_snapshots,
    status_snapshot,
    web_url_for_snapshot,
)
