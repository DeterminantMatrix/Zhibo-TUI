package com.determinantmatrix.zhibo.core.resolver

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DouyinAbSignTest {

    // 向量由桌面 streamget ab_sign.py 以固定时间 1715300000123 生成
    private val query = "aid=6383&app_name=douyin_web&browser_language=zh-CN&browser_platform=Win32" +
        "&browser_name=Chrome&browser_version=116.0.0.0&device_platform=web" +
        "&is_need_double_stream=false&language=zh-CN&live_id=1&msToken=&web_rid=335354047186"
    private val ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"

    @Test
    fun `ab sign matches desktop byte for byte`() {
        val sig = DouyinAbSign.abSign(query, ua, nowMillis = 1715300000123)
        assertEquals(
            "E7mhBmg6mEVNgf6X5UKLfY3q6WN3Y6cP0HViMD2fyxVWhy39HMTa9exonm0vH7jjLG/lIeYjy4hbO3xprQAjM36UHWwEUdQ2mgWkKl5Q5I0j53iruyRDntmF4vj3SFlm5XNAEOk0y75rKb70Woqe-vIlO62-zo0/9AS=",
            sig,
        )
    }

    @Test
    fun `ab sign output shape is stable`() {
        val sig = DouyinAbSign.abSign(query, ua)
        assertTrue(sig.endsWith("="))
        assertTrue(sig.length > 100)
    }
}
