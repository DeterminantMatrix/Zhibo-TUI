package com.determinantmatrix.zhibo.core.model

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * 关注项领域模型 — 与桌面版 zhibo/models.py Follower 一一对应。
 */
data class Follower(
    val name: String,
    val plugin: String,
    val url: String,
    val platform: String = "",
    val quality: String = "best",
    val tags: List<String> = listOf(DEFAULT_TAG),
    val extra: JsonObject = JsonObject(emptyMap()),
    val enabled: Boolean = true,
    val fallbackPlugins: List<String> = emptyList(),
) {
    val sportId: String
        get() = (extra[EXTRA_SPORT_ID] as? JsonPrimitive)?.content ?: ""

    companion object {
        const val DEFAULT_TAG = "未分类"
        const val EXTRA_SPORT_ID = "sport_id"
    }
}

/**
 * 全局监控设置 — 键名与桌面 settings.csv 完全一致。
 */
data class AppConfig(
    val pollInterval: Int = 60,
    val maxConcurrentChecks: Int = 8,
    val failureBackoffAfter: Int = 3,
    val failureBackoffPolls: Int = 2,
    val notificationsEnabled: Boolean = true,
    val platformProxies: Map<String, String> = emptyMap(),
)
