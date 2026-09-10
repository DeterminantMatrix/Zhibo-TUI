package com.determinantmatrix.zhibo.core.resolver

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** JsonElement 取值小工具，容忍缺键/类型不符。 */
object Jsonx {

    val json = Json

    fun parse(text: String): JsonElement = json.parseToJsonElement(text)

    fun obj(element: JsonElement?): JsonObject? =
        element as? JsonObject

    fun at(element: JsonElement?, vararg path: String): JsonElement? {
        var current: JsonElement? = element
        for (key in path) {
            current = (current as? JsonObject)?.get(key) ?: return null
        }
        return current
    }

    fun str(element: JsonElement?): String = when (element) {
        is JsonPrimitive -> element.content
        else -> ""
    }

    fun int(element: JsonElement?): Int = when (val e = element) {
        is JsonPrimitive -> e.content.toIntOrNull() ?: 0
        else -> 0
    }

    fun bool(element: JsonElement?): Boolean = when (val e = element) {
        is JsonPrimitive -> e.content == "1" || e.content == "true"
        else -> false
    }

    fun array(element: JsonElement?): JsonArray = element as? JsonArray ?: JsonArray(emptyList())

    fun isNull(element: JsonElement?): Boolean = element == null || element is JsonNull
}
