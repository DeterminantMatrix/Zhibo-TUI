package com.determinantmatrix.zhibo.core.model

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * followers.csv 编解码 — 列名、默认值、归一化规则与桌面 zhibo/config.py 保持一致。
 */
object FollowersCsv {

    val COLUMNS = listOf(
        "enabled", "name", "tags", "plugin", "fallback_plugins",
        "platform", "url", "quality", "sport_id", "extra",
    )

    /** 与桌面 BUILTIN_STREAM_PLUGINS 一致：仅这些插件的平台列做小写归一。 */
    private val BUILTIN_STREAM_PLUGINS = setOf("streamget", "streamlink")

    data class DecodeResult(
        val followers: List<Follower>,
        val warnings: List<String>,
    )

    fun decode(text: String): DecodeResult {
        val rows = Csv.parseRows(text)
        if (rows.isEmpty()) return DecodeResult(emptyList(), listOf("关注列表缺少 CSV 表头"))

        val header = rows.first()
        val index = COLUMNS.associateWith { column -> header.indexOfFirst { it.trim() == column } }
        val warnings = mutableListOf<String>()
        val followers = mutableListOf<Follower>()

        for ((offset, row) in rows.drop(1).withIndex()) {
            val lineNumber = offset + 2
            if (row.all { it.isBlank() }) continue
            fun cell(column: String): String {
                val i = index.getValue(column)
                return if (i >= 0 && i < row.size) row[i] else ""
            }
            val name = cell("name").trim()
            val plugin = cell("plugin").trim().lowercase()
            val url = cell("url").trim()
            if (name.isEmpty() || plugin.isEmpty() || url.isEmpty()) {
                warnings.add("第 $lineNumber 行缺少 name/plugin/url，已跳过")
                continue
            }
            var extra = parseExtra(cell("extra"))
            cell("sport_id").trim().takeIf { it.isNotEmpty() }?.let { sportId ->
                extra = JsonObject(extra + (Follower.EXTRA_SPORT_ID to JsonPrimitive(sportId)))
            }
            followers.add(
                Follower(
                    name = name,
                    plugin = plugin,
                    url = url,
                    platform = normalizePlatform(plugin, cell("platform")),
                    quality = cell("quality").trim().ifEmpty { "best" },
                    tags = splitCell(cell("tags")).ifEmpty { listOf(Follower.DEFAULT_TAG) },
                    extra = extra,
                    enabled = cleanBool(cell("enabled"), default = true),
                    fallbackPlugins = splitCell(cell("fallback_plugins")).map { it.lowercase() },
                ),
            )
        }
        return DecodeResult(followers, warnings)
    }

    fun encode(followers: List<Follower>): String {
        val rows = mutableListOf(COLUMNS)
        for (f in followers) {
            val csvExtra = JsonObject(f.extra.filterKeys { it != Follower.EXTRA_SPORT_ID })
            rows.add(
                listOf(
                    f.enabled.toString(),
                    f.name,
                    f.tags.joinToString("|"),
                    f.plugin,
                    f.fallbackPlugins.joinToString("|"),
                    f.platform,
                    f.url,
                    f.quality,
                    f.sportId,
                    if (csvExtra.isEmpty()) "" else PythonJson.dumps(csvExtra),
                ),
            )
        }
        return Csv.encodeRowsWithBom(rows)
    }

    private fun parseExtra(cell: String): JsonObject {
        val text = cell.trim()
        if (text.isEmpty()) return JsonObject(emptyMap())
        return runCatching { PythonJson.parse(text) as JsonObject }.getOrDefault(JsonObject(emptyMap()))
    }

    /** 对齐桌面 _normalize_platform：仅内建流插件的平台列做 casefold。 */
    fun normalizePlatform(plugin: String, value: String): String {
        val platform = value.trim()
        return if (plugin in BUILTIN_STREAM_PLUGINS) platform.lowercase() else platform
    }

    /** 对齐桌面 _split_cell：中英文分号统一视为 | 分隔。 */
    fun splitCell(value: String): List<String> =
        value.replace("；", "|").replace(";", "|")
            .split("|")
            .map { it.trim() }
            .filter { it.isNotEmpty() }

    /** 对齐桌面 _clean_bool 的真值词表；无法识别的非空文本按 true 处理。 */
    fun cleanBool(value: String, default: Boolean): Boolean {
        return when (value.trim().lowercase()) {
            "" -> default
            "1", "true", "yes", "y", "on", "是", "开" -> true
            "0", "false", "no", "n", "off", "否", "关" -> false
            else -> value.trim().isNotEmpty()
        }
    }
}
