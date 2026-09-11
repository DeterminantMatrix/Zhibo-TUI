package com.determinantmatrix.zhibo.core.resolver

/**
 * 画质选项 — 直译桌面 quality_options.py：B 站独立档位，其余 streamget 通用档。
 * 返回值 = 写回关注项 quality 列的字符串（与桌面 CSV 兼容）。
 */
object QualityOptions {

    data class Option(val label: String, val value: String)

    val BEST = Option("最优画质", "best")

    val STREAMGET_OPTIONS = listOf(
        BEST,
        Option("超清（UHD）", "UHD"),
        Option("高清（HD）", "HD"),
        Option("流畅（LD）", "LD"),
    )

    val BILIBILI_OPTIONS = listOf(
        BEST,
        Option("蓝光", "蓝光"),
        Option("高清", "高清"),
        Option("流畅", "流畅"),
    )

    val FS1_OPTIONS = listOf(
        BEST,
        Option("蓝光真码", "lgzm"),
        Option("高清真码", "gqzm"),
        Option("标清真码", "bqzm"),
    )

    fun forPlatform(platform: String): List<Option> = when (platform.trim().lowercase()) {
        "bilibili" -> BILIBILI_OPTIONS
        "douyu", "huya" -> STREAMGET_OPTIONS
        "fs1" -> FS1_OPTIONS
        else -> listOf(BEST)
    }
}
