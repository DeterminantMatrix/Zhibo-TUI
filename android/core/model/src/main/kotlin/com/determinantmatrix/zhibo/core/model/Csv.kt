package com.determinantmatrix.zhibo.core.model

/**
 * RFC 4180 风格 CSV 编解码，行为对齐 Python csv 模块默认值：
 * QUOTE_MINIMAL（字段含 , " \r \n 时加引号）、引号内双写转义、\r\n 行尾。
 */
object Csv {

    fun parseRows(text: String): List<List<String>> {
        val clean = text.removePrefix("\uFEFF")
        val rows = mutableListOf<List<String>>()
        var field = StringBuilder()
        var row = mutableListOf<String>()
        var inQuotes = false
        var i = 0
        val n = clean.length
        while (i < n) {
            val c = clean[i]
            when {
                inQuotes -> when {
                    c == '"' -> {
                        if (i + 1 < n && clean[i + 1] == '"') {
                            field.append('"')
                            i++
                        } else {
                            inQuotes = false
                        }
                    }
                    else -> field.append(c)
                }
                c == '"' && field.isEmpty() -> inQuotes = true
                c == ',' -> {
                    row.add(field.toString())
                    field = StringBuilder()
                }
                c == '\r' -> {
                    if (i + 1 < n && clean[i + 1] == '\n') i++
                    row.add(field.toString())
                    field = StringBuilder()
                    rows.add(row)
                    row = mutableListOf()
                }
                c == '\n' -> {
                    row.add(field.toString())
                    field = StringBuilder()
                    rows.add(row)
                    row = mutableListOf()
                }
                else -> field.append(c)
            }
            i++
        }
        if (field.isNotEmpty() || row.isNotEmpty()) {
            row.add(field.toString())
            rows.add(row)
        }
        return rows
    }

    fun encodeField(value: String): String =
        if (value.any { it == ',' || it == '"' || it == '\r' || it == '\n' }) {
            "\"" + value.replace("\"", "\"\"") + "\""
        } else {
            value
        }

    /** 产出与桌面 csv.writer 一致的字节序列：CRLF 行尾、结尾换行、不含 BOM。 */
    fun encodeRows(rows: List<List<String>>): String =
        rows.joinToString("\r\n") { row -> row.joinToString(",") { encodeField(it) } } + "\r\n"

    /** 桌面版以 utf-8-sig 写盘；安卓导出保持同格式以便双向互通。 */
    fun encodeRowsWithBom(rows: List<List<String>>): String = "\uFEFF" + encodeRows(rows)
}
