package com.determinantmatrix.zhibo.core.resolver

import com.determinantmatrix.zhibo.core.network.Http
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ResolverLogicTest {

    @Test
    fun `bilibili qn mapping matches desktop alias table`() {
        assertEquals(10000, Quality.bilibiliQn("best"))
        assertEquals(10000, Quality.bilibiliQn("原画"))
        assertEquals(400, Quality.bilibiliQn("蓝光"))
        assertEquals(400, Quality.bilibiliQn("BD"))
        assertEquals(150, Quality.bilibiliQn("高清"))
        assertEquals(80, Quality.bilibiliQn("流畅"))
        assertEquals(10000, Quality.bilibiliQn("乱写的"))
    }

    @Test
    fun `douyu rate mapping matches streamget options`() {
        assertEquals("0", Quality.douyuRate("best"))
        assertEquals("3", Quality.douyuRate("UHD"))
        assertEquals("2", Quality.douyuRate("HD"))
        assertEquals("1", Quality.douyuRate("LD"))
    }

    @Test
    fun `fallback chain dedupes and appends platform defaults`() {
        assertEquals(
            listOf("streamget", "streamlink"),
            FallbackChain.forFollower("streamget", listOf("streamlink"), "twitch"),
        )
        assertEquals(
            listOf("yt_dlp", "streamget", "streamlink"),
            FallbackChain.forFollower("yt_dlp", emptyList(), "youtube"),
        )
        assertEquals(
            listOf("streamget", "fs1"),
            FallbackChain.forFollower("streamget", listOf("FS1"), "bilibili"),
        )
    }

    @Test
    fun `platform inferred from url when column empty`() {
        assertEquals("bilibili", StreamgetResolver.platformOf("https://live.bilibili.com/6?b=1"))
        assertEquals("douyu", StreamgetResolver.platformOf("https://www.douyu.com/74960"))
        assertEquals("huya", StreamgetResolver.platformOf("https://www.huya.com/11342412"))
        assertEquals("twitch", StreamgetResolver.platformOf("https://www.twitch.tv/x"))
        assertEquals("", StreamgetResolver.platformOf("https://example.com/x"))
    }

    @Test
    fun `huya anticode rebuild is deterministic in shape`() {
        val resolver = HuyaResolver(Http())
        val anti = resolver.rebuildAntiCode(
            "wsSecret=4021e92aa5e4ab8c933b28f041b4d358&wsTime=668f2f17&ctype=tars_mp" +
                "&fs=bhct&fm=RFdxOEJjSzNoNkRKNlRZXzAkMF8kMV8kMl8kMw%3D%3D&sprop=91lv",
            "11342412-11342412-5177582992-2262263-10057-A-0-1",
        )
        assertTrue(anti.startsWith("wsSecret="))
        assertTrue("wsTime=" in anti && "seqid=" in anti && "ctype=tars_mp" in anti)
        assertTrue("codec=264" in anti)
        assertFalse(anti.contains("fm="))
    }
}
