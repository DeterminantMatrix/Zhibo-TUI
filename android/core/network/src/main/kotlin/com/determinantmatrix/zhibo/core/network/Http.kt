package com.determinantmatrix.zhibo.core.network

import java.io.IOException
import java.util.concurrent.TimeUnit
import okhttp3.FormBody
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * 极简同步 HTTP 封装。调用方负责切到 IO 调度器。
 * 桌面 streamget 的 async_req 默认 20s 超时；此处对齐。
 */
class Http(
    connectTimeoutMillis: Long = 10_000,
    readTimeoutMillis: Long = 20_000,
) {

    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(connectTimeoutMillis, TimeUnit.MILLISECONDS)
        .readTimeout(readTimeoutMillis, TimeUnit.MILLISECONDS)
        .writeTimeout(20_000, TimeUnit.MILLISECONDS)
        .build()

    fun get(url: String, headers: Map<String, String> = emptyMap()): String =
        execute(newBuilder(url, headers).get())

    fun postForm(url: String, headers: Map<String, String> = emptyMap(), form: Map<String, String>): String {
        val body = FormBody.Builder().apply {
            form.forEach { (k, v) -> add(k, v) }
        }.build()
        return execute(newBuilder(url, headers).post(body))
    }

    private fun newBuilder(url: String, headers: Map<String, String>): Request.Builder {
        val builder = Request.Builder().url(url)
        headers.forEach { (k, v) -> builder.header(k, v) }
        return builder
    }

    private fun execute(builder: Request.Builder): String {
        client.newCall(builder.build()).execute().use { response ->
            val body = response.body?.string() ?: ""
            if (!response.isSuccessful) {
                throw IOException("HTTP ${response.code}: ${body.take(200)}")
            }
            return body
        }
    }
}
