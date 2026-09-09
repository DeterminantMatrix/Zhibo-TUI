package com.determinantmatrix.zhibo.core.model

/**
 * settings.csv 编解码 — 键名、安全钳制与桌面 config.py 完全一致。
 */
object SettingsCsv {

    const val KEY_POLL_INTERVAL = "poll_interval"
    const val KEY_MAX_CONCURRENT_CHECKS = "max_concurrent_checks"
    const val KEY_FAILURE_BACKOFF_AFTER = "failure_backoff_after"
    const val KEY_FAILURE_BACKOFF_POLLS = "failure_backoff_polls"
    const val KEY_NOTIFICATIONS_ENABLED = "notifications_enabled"
    const val KEY_PLATFORM_PROXY_PREFIX = "platform_proxy."

    /** 与桌面 MONITORING_SETTINGS_LIMITS 相同的安全范围。 */
    val LIMITS = mapOf(
        KEY_POLL_INTERVAL to (5..3600),
        KEY_MAX_CONCURRENT_CHECKS to (1..16),
        KEY_FAILURE_BACKOFF_AFTER to (1..20),
        KEY_FAILURE_BACKOFF_POLLS to (1..60),
    )

    private val DEFAULTS = mapOf(
        KEY_POLL_INTERVAL to 60,
        KEY_MAX_CONCURRENT_CHECKS to 8,
        KEY_FAILURE_BACKOFF_AFTER to 3,
        KEY_FAILURE_BACKOFF_POLLS to 2,
    )

    fun decode(text: String): AppConfig {
        val rows = Csv.parseRows(text)
        if (rows.isEmpty()) return AppConfig()
        val header = rows.first().map { it.trim() }
        val keyIdx = header.indexOf("key")
        val valueIdx = header.indexOf("value")
        if (keyIdx < 0 || valueIdx < 0) return AppConfig()

        val data = mutableMapOf<String, String>()
        for (row in rows.drop(1)) {
            if (row.all { it.isBlank() }) continue
            val key = row.getOrNull(keyIdx)?.trim().orEmpty()
            if (key.isNotEmpty()) data[key] = row.getOrNull(valueIdx) ?: ""
        }
        return AppConfig(
            pollInterval = intOf(data, KEY_POLL_INTERVAL),
            maxConcurrentChecks = intOf(data, KEY_MAX_CONCURRENT_CHECKS),
            failureBackoffAfter = intOf(data, KEY_FAILURE_BACKOFF_AFTER),
            failureBackoffPolls = intOf(data, KEY_FAILURE_BACKOFF_POLLS),
            notificationsEnabled = FollowersCsv.cleanBool(
                data[KEY_NOTIFICATIONS_ENABLED].orEmpty(),
                default = true,
            ),
            platformProxies = data.entries
                .filter { it.key.startsWith(KEY_PLATFORM_PROXY_PREFIX) }
                .mapNotNull { (key, value) ->
                    val platform = key.removePrefix(KEY_PLATFORM_PROXY_PREFIX).trim().lowercase()
                    val proxy = value.trim()
                    if (platform.isEmpty() || proxy.isEmpty()) null else platform to proxy
                }
                .toMap(),
        )
    }

    fun encode(cfg: AppConfig): String {
        val rows = mutableListOf(listOf("key", "value"))
        rows.add(listOf(KEY_POLL_INTERVAL, cfg.pollInterval.toString()))
        rows.add(listOf(KEY_MAX_CONCURRENT_CHECKS, cfg.maxConcurrentChecks.toString()))
        rows.add(listOf(KEY_FAILURE_BACKOFF_AFTER, cfg.failureBackoffAfter.toString()))
        rows.add(listOf(KEY_FAILURE_BACKOFF_POLLS, cfg.failureBackoffPolls.toString()))
        rows.add(listOf(KEY_NOTIFICATIONS_ENABLED, cfg.notificationsEnabled.toString()))
        for ((platform, proxy) in cfg.platformProxies.toSortedMap()) {
            if (platform.isBlank() || proxy.isBlank()) continue
            rows.add(listOf(KEY_PLATFORM_PROXY_PREFIX + platform, proxy))
        }
        return Csv.encodeRowsWithBom(rows)
    }

    private fun intOf(data: Map<String, String>, key: String): Int {
        val text = data[key]?.trim().orEmpty()
        val default = DEFAULTS.getValue(key)
        if (text.isEmpty() || !REGEX_DIGITS.matches(text)) return default
        val limits = LIMITS.getValue(key)
        return text.toInt().coerceIn(limits.first, limits.last)
    }

    private val REGEX_DIGITS = Regex("[0-9]+")
}
