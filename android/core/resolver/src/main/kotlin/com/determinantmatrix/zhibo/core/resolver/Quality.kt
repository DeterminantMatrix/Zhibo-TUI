package com.determinantmatrix.zhibo.core.resolver

/**
 * 画质归一 — 对齐桌面 quality_options.py / bilibili_quality.py / streamget 内置表。
 */
object Quality {

    val BILIBILI_QN = mapOf(
        "best" to 10000, "od" to 10000, "source" to 10000, "原画" to 10000,
        "最高" to 10000, "最高画质" to 10000,
        "4k" to 20000,
        "蓝光" to 400, "bd" to 400,
        "超清" to 250, "uhd" to 250,
        "高清" to 150, "hd" to 150,
        "流畅" to 80, "sd" to 80,
    )

    val BILIBILI_QN_LABEL = mapOf(
        30000 to "杜比", 20000 to "4K", 10000 to "原画", 400 to "蓝光",
        250 to "超清", 150 to "高清", 80 to "流畅",
    )

    // streamget 通用档位：OD/BD/UHD/HD/SD/LD
    val STREAMGET_KEYS = listOf("OD", "BD", "UHD", "HD", "SD", "LD")

    /** CSV 画质串 → streamget 通用档位（斗鱼/虎牙用）。 */
    fun toStreamget(quality: String): String {
        val key = quality.trim().lowercase()
        return when {
            key.isEmpty() || key == "best" || key == "最优画质" -> "OD"
            key in STREAMGET_KEYS.map { it.lowercase() } -> key.uppercase()
            key.toIntOrNull() != null -> {
                val idx = key.toInt().coerceIn(0, STREAMGET_KEYS.size - 1)
                STREAMGET_KEYS[idx]
            }
            else -> key.uppercase()
        }
    }

    /** 斗鱼 rate 档位。 */
    fun douyuRate(quality: String): String = when (toStreamget(quality)) {
        "UHD" -> "3"
        "HD" -> "2"
        "SD", "LD" -> "1"
        else -> "0" // OD/BD
    }

    /** B 站 qn；未知串按 best 处理。 */
    fun bilibiliQn(quality: String): Int =
        BILIBILI_QN[quality.trim().lowercase()] ?: 10000
}
