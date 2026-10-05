package com.determinantmatrix.zhibo.core.model

import kotlinx.serialization.json.JsonObject

/** 直播检测结果 — 对应桌面 plugins/base.py LiveInfo。 */
data class LiveInfo(
    val isLive: Boolean,
    val anchorName: String = "",
    val title: String = "",
    val streamUrl: String = "",
    val m3u8Url: String = "",
    val flvUrl: String = "",
    val qualityName: String = "",
    val platform: String = "",
    val candidates: List<String> = emptyList(),
    val extra: JsonObject = JsonObject(emptyMap()),
)
