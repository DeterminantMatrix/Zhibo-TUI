package com.determinantmatrix.zhibo.core.monitor

import java.util.concurrent.ConcurrentHashMap
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow

/**
 * 平台熔断 — 直译桌面 monitor.py：
 * 基数 15s、指数封顶 900s、±20% 抖动、连续 2 次失败进入 outage；
 * B 站特殊：不同房间（source）失败满 2 个才算一次平台故障，单房间抖动不熔断。
 */
class PlatformHealthTracker(
    private val nowMillis: () -> Long,
    private val random: () -> Double = { Math.random() },
) {

    class Health internal constructor() {
        var consecutiveFailures: Int = 0
            internal set
        var nextAllowedMillis: Long = 0L
            internal set
        var lastError: String = ""
            internal set
        internal val failureSources = mutableSetOf<String>()

        fun isGated(now: Long): Boolean = now < nextAllowedMillis

        fun isHealthy(): Boolean = consecutiveFailures == 0
    }

    private val platforms = ConcurrentHashMap<String, Health>()

    fun health(platform: String): Health = platforms.getOrPut(key(platform)) { Health() }

    fun snapshot(): Map<String, Health> = platforms.toMap()

    fun recordFailure(platform: String, source: String?, error: String) {
        val h = health(platform)
        val key = key(platform)
        if (key == "bilibili" && source != null) {
            h.failureSources.add(source)
            h.lastError = error
            if (h.failureSources.size < 2) return
            // 两个不同房间都失败，计一次平台故障并进入普通退避
            h.failureSources.clear()
        }
        h.consecutiveFailures++
        val delay = backoffDelay(h.consecutiveFailures)
        h.nextAllowedMillis = nowMillis() + (delay * 1000).toLong()
        h.lastError = error
    }

    fun markHealthy(platform: String) {
        val h = health(platform)
        h.consecutiveFailures = 0
        h.nextAllowedMillis = 0L
        h.lastError = ""
        h.failureSources.clear()
    }

    /** 指数退避 + 抖动，单位秒。 */
    fun backoffDelay(failureCount: Int): Double {
        val exponent = min(max(failureCount - 1, 0), 16)
        val base = min(BASE_SECONDS * 2.0.pow(exponent), MAX_SECONDS)
        val jitter = (random() * 2 - 1) * base * JITTER_RATIO
        return min(MAX_SECONDS, max(0.0, base + jitter))
    }

    companion object {
        const val BASE_SECONDS = 15.0
        const val MAX_SECONDS = 900.0
        const val JITTER_RATIO = 0.20

        fun key(platform: String): String = platform.trim().lowercase()

        /** 连接性错误分类 — 直译桌面 _is_platform_connectivity_error（去掉代理专属词也保留）。 */
        fun isConnectivityError(error: String): Boolean {
            val text = error.lowercase()
            val markers = listOf(
                "超时", "检测超时", "任务超时", "获取流地址超时",
                "connecttimeout", "connection timed out", "timed out", "timeout",
                "dns", "name or service not known", "temporary failure",
                "network is unreachable", "connection refused", "connection reset",
                "proxyerror", "proxy error",
            )
            return markers.any { it in text }
        }
    }
}
