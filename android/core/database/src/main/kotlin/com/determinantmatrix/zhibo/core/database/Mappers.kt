package com.determinantmatrix.zhibo.core.database

import com.determinantmatrix.zhibo.core.model.Follower
import com.determinantmatrix.zhibo.core.model.FollowersCsv
import kotlinx.serialization.json.Json

private val json = Json

fun FollowerEntity.toDomain(): Follower {
    var extra = runCatching {
        json.parseToJsonElement(extraJson.ifEmpty { "{}" }) as kotlinx.serialization.json.JsonObject
    }.getOrDefault(kotlinx.serialization.json.JsonObject(emptyMap()))
    if (sportId.isNotEmpty()) {
        extra = kotlinx.serialization.json.JsonObject(
            extra + (Follower.EXTRA_SPORT_ID to kotlinx.serialization.json.JsonPrimitive(sportId)),
        )
    }
    return Follower(
        name = name,
        plugin = plugin,
        url = url,
        platform = platform,
        quality = quality,
        tags = FollowersCsv.splitCell(tags).ifEmpty { listOf(Follower.DEFAULT_TAG) },
        extra = extra,
        enabled = enabled,
        fallbackPlugins = FollowersCsv.splitCell(fallbackPlugins).map { it.lowercase() },
    )
}

fun Follower.toEntity(position: Int): FollowerEntity {
    val csvExtra = extra.filterKeys { it != Follower.EXTRA_SPORT_ID }
    return FollowerEntity(
        position = position,
        name = name,
        plugin = plugin,
        url = url,
        platform = platform,
        quality = quality,
        tags = tags.joinToString("|"),
        sportId = sportId,
        extraJson = if (csvExtra.isEmpty()) "" else com.determinantmatrix.zhibo.core.model.PythonJson.dumps(
            kotlinx.serialization.json.JsonObject(csvExtra),
        ),
        enabled = enabled,
        fallbackPlugins = fallbackPlugins.joinToString("|"),
    )
}
