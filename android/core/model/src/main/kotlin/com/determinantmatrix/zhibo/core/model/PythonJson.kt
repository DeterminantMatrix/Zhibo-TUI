package com.determinantmatrix.zhibo.core.model

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * 与 Python json.dumps(..., ensure_ascii=False, sort_keys=True) 字节兼容的序列化。
 * 桌面版 extra 列即该格式：键递归排序、逗号与冒号后带空格、非 ASCII 原样输出。
 */
object PythonJson {

    private val json = Json

    fun parse(text: String): JsonElement = json.parseToJsonElement(text)

    fun dumps(element: JsonElement): String = when (element) {
        is JsonObject -> element.entries
            .sortedBy { it.key }
            .joinToString(separator = ", ", prefix = "{", postfix = "}") { (key, value) ->
                "\"${escape(key)}\": ${dumps(value)}"
            }
        is JsonArray -> element.joinToString(separator = ",", prefix = "[", postfix = "]") { dumps(it) }
        is JsonPrimitive -> if (element.isString) "\"${escape(element.content)}\"" else element.content
        JsonNull -> "null"
    }

    private fun escape(text: String): String {
        val sb = StringBuilder(text.length + 8)
        for (c in text) {
            when {
                c == '"' -> sb.append("\\\"")
                c == '\\' -> sb.append("\\\\")
                c == '\b' -> sb.append("\\b")
                c == '' -> sb.append("\\f")
                c == '\n' -> sb.append("\\n")
                c == '\r' -> sb.append("\\r")
                c == '\t' -> sb.append("\\t")
                c < ' ' -> sb.append("\\u").append(String.format("%04x", c.code))
                else -> sb.append(c)
            }
        }
        return sb.toString()
    }
}
