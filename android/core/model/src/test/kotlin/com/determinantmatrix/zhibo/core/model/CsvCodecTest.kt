package com.determinantmatrix.zhibo.core.model

import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CsvCodecTest {

    @Test
    fun `parses quoted fields with commas quotes and newlines`() {
        val text = "a,\"b,c\",\"d\"\"e\",\"f\ng\"\r\nh,i,j\r\n"
        val rows = Csv.parseRows(text)
        assertEquals(listOf("a", "b,c", "d\"e", "f\ng"), rows[0])
        assertEquals(listOf("h", "i", "j"), rows[1])
    }

    @Test
    fun `strips bom and handles lf endings`() {
        val rows = Csv.parseRows("﻿k,v\n1,2\n")
        assertEquals(listOf("k", "v"), rows[0])
        assertEquals(listOf("1", "2"), rows[1])
    }

    @Test
    fun `encodes minimal quoting with crlf`() {
        val encoded = Csv.encodeRows(listOf(listOf("plain", "with,comma", "with\"quote")))
        assertEquals("plain,\"with,comma\",\"with\"\"quote\"\r\n", encoded)
    }

    @Test
    fun `python json dumps matches dumps with sort_keys`() {
        val obj = PythonJson.parse("""{"b": 2, "a": "文本", "c": {"z": true, "y": null}}""")
        assertEquals("""{"a": "文本", "b": 2, "c": {"y": null, "z": true}}""", PythonJson.dumps(obj))
    }

    @Test
    fun `python json escapes control chars like python`() {
        val obj = JsonObject(mapOf("s" to JsonPrimitive("ab".replace("ab", "ab"))))
        assertEquals("{\"s\": \"a\\u0001b\"}", PythonJson.dumps(obj))
    }
}

class FollowersCsvTest {

    // 与桌面版 README 示例同构的样例：BOM 头、CRLF、CSV 引号包裹的 extra JSON（引号双写）
    private val sample = "\uFEFFenabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id,extra\r\n" +
        "true,示例主播,游戏|主机,streamget,streamlink,twitch,https://www.twitch.tv/example,best,,\r\n" +
        "0,低频主播,,streamlink,,bilibili,https://live.bilibili.com/6,,12345,\"{\"\"poll_interval\"\": \"\"600\"\"}\"\r\n" +
        "true,LPL,LOL,streamget,streamget;bilibili,B站,https://live.bilibili.com/6,best,,\r\n"

    @Test
    fun `decodes rows with desktop normalization rules`() {
        val result = FollowersCsv.decode(sample)
        assertTrue(result.warnings.isEmpty())
        val followers = result.followers
        assertEquals(3, followers.size)

        assertEquals("示例主播", followers[0].name)
        assertEquals(listOf("游戏", "主机"), followers[0].tags)
        assertEquals(listOf("streamlink"), followers[0].fallbackPlugins)
        assertEquals("twitch", followers[0].platform)
        assertTrue(followers[0].enabled)

        assertEquals(false, followers[1].enabled)
        assertEquals(listOf("未分类"), followers[1].tags)
        assertEquals("12345", followers[1].sportId)
        assertEquals("600", (followers[1].extra["poll_interval"] as JsonPrimitive).content)
        assertEquals("bilibili", followers[1].platform)

        // streamget 插件 → 平台列 casefold；英文分号也当分隔符
        assertEquals("b站", followers[2].platform)
        assertEquals(listOf("streamget", "bilibili"), followers[2].fallbackPlugins)
    }

    @Test
    fun `encode decode roundtrip is stable`() {
        val result = FollowersCsv.decode(sample)
        val decodedAgain = FollowersCsv.decode(FollowersCsv.encode(result.followers))
        assertEquals(result.followers, decodedAgain.followers)
    }

    @Test
    fun `encode output matches desktop canonical serialization`() {
        val encoded = FollowersCsv.encode(FollowersCsv.decode(sample).followers)
        val expectedHeader = "\uFEFFenabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id,extra\r\n"
        assertTrue(encoded.startsWith(expectedHeader))
        // extra 列与 python json.dumps(sort_keys=True) 一致：冒号后带空格，CSV 引号双写
        val quotedJson = "\"{\"\"poll_interval\"\": \"\"600\"\"}\""
        assertTrue(encoded.contains(",12345," + quotedJson + "\r\n"))
    }

    @Test
    fun `rows missing required fields are skipped with warning`() {
        val text = "enabled,name,plugin,url\r\ntrue,只有名字,streamget,\r\n"
        val result = FollowersCsv.decode(text)
        assertEquals(0, result.followers.size)
        assertEquals(1, result.warnings.size)
    }
}

class SettingsCsvTest {

    @Test
    fun `decodes with clamping and defaults`() {
        val text = "key,value\r\npoll_interval,2\r\nmax_concurrent_checks,99\r\n" +
            "notifications_enabled,off\r\nplatform_proxy.twitch,http://127.0.0.1:7897\r\n" +
            "platform_proxy.Twitch,direct\r\n"
        val cfg = SettingsCsv.decode(text)
        assertEquals(5, cfg.pollInterval) // 钳制到下限
        assertEquals(16, cfg.maxConcurrentChecks) // 钳制到上限
        assertEquals(3, cfg.failureBackoffAfter) // 缺省
        assertEquals(false, cfg.notificationsEnabled)
        assertEquals(mapOf("twitch" to "direct"), cfg.platformProxies)
    }

    @Test
    fun `encode roundtrips`() {
        val cfg = AppConfig(
            pollInterval = 300,
            maxConcurrentChecks = 4,
            failureBackoffAfter = 5,
            failureBackoffPolls = 6,
            notificationsEnabled = false,
            platformProxies = mapOf("youtube" to "http://127.0.0.1:7890"),
        )
        assertEquals(cfg, SettingsCsv.decode(SettingsCsv.encode(cfg)))
    }

    @Test
    fun `encode writes desktop key order`() {
        val encoded = SettingsCsv.encode(AppConfig())
        val expected = "\uFEFFkey,value\r\n" +
            "poll_interval,60\r\n" +
            "max_concurrent_checks,8\r\n" +
            "failure_backoff_after,3\r\n" +
            "failure_backoff_polls,2\r\n" +
            "notifications_enabled,true\r\n"
        assertEquals(expected, encoded)
    }
}
