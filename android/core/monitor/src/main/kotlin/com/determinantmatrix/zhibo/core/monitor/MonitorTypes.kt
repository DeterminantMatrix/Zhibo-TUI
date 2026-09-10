package com.determinantmatrix.zhibo.core.monitor

import com.determinantmatrix.zhibo.core.model.AppConfig

enum class CheckState { LIVE, OFFLINE, ERROR }

/**
 * 关注项检测结果。ERROR 且 stale=true 表示"上次确认在线、当前检测异常"，
 * 沿用桌面语义：保留上次标题/在线痕迹，不重复触发开播通知，不可用于播放。
 */
data class FollowerStatus(
    val state: CheckState,
    val title: String = "",
    val anchorName: String = "",
    val qualityName: String = "",
    val error: String = "",
    val stale: Boolean = false,
    val checkedAtMillis: Long = 0,
)

/** 开播事件 — 仅 OFFLINE/ERROR/初始 → LIVE 的跳变触发。 */
data class LiveEvent(
    val name: String,
    val url: String,
    val platform: String,
    val anchorName: String,
    val title: String,
)

data class EngineConfig(
    val pollIntervalSeconds: Int = 60,
    val maxConcurrentChecks: Int = 8,
    val failureBackoffAfter: Int = 3,
    val failureBackoffPolls: Int = 2,
) {
    companion object {
        fun from(cfg: AppConfig): EngineConfig = EngineConfig(
            pollIntervalSeconds = cfg.pollInterval,
            maxConcurrentChecks = cfg.maxConcurrentChecks,
            failureBackoffAfter = cfg.failureBackoffAfter,
            failureBackoffPolls = cfg.failureBackoffPolls,
        )
    }
}
