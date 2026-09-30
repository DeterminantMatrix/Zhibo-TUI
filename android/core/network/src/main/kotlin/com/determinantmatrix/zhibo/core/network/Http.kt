package com.determinantmatrix.zhibo.core.network

import java.io.IOException
import java.util.concurrent.TimeUnit
import okhttp3.FormBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.tls.HandshakeCertificates

/**
 * 极简同步 HTTP 封装。调用方负责切到 IO 调度器。
 * 桌面 streamget 的 async_req 默认 20s 超时；此处对齐。
 *
 * [extraTrustedCertificates]：classpath 上的 DER 证书，作为额外信任锚。
 * FS1 的 API 服务器漏发 Let's Encrypt YE1 中间证书（桌面靠 Windows AIA
 * 自动补链），Android 无此机制，故把官方 YE1 证书（指纹固定）内置为锚，
 * TLS 验证保持开启。
 */
class Http(
    connectTimeoutMillis: Long = 10_000,
    readTimeoutMillis: Long = 20_000,
    extraTrustedCertificates: List<String> = emptyList(),
    /** 形如 http://host:port 的代理地址（Twitch/YouTube 等需代理的平台用）。 */
    proxy: String? = null,
) {

    private val client: OkHttpClient = run {
        val builder = OkHttpClient.Builder()
            .connectTimeout(connectTimeoutMillis, TimeUnit.MILLISECONDS)
            .readTimeout(readTimeoutMillis, TimeUnit.MILLISECONDS)
            .writeTimeout(20_000, TimeUnit.MILLISECONDS)
        if (proxy != null) {
            runCatching {
                val uri = java.net.URI(proxy)
                builder.proxy(java.net.Proxy(java.net.Proxy.Type.HTTP, java.net.InetSocketAddress(uri.host, uri.port)))
            }
        }
        if (extraTrustedCertificates.isNotEmpty()) {
            val factory = java.security.cert.CertificateFactory.getInstance("X.509")
            val handshake = HandshakeCertificates.Builder().apply {
                addPlatformTrustedCertificates()
                extraTrustedCertificates.forEach { resource ->
                    val cert = Http::class.java.getResourceAsStream(resource)!!.use { stream ->
                        factory.generateCertificate(stream) as java.security.cert.X509Certificate
                    }
                    addTrustedCertificate(cert)
                }
            }.build()
            builder.sslSocketFactory(handshake.sslSocketFactory(), handshake.trustManager)
        }
        builder.build()
    }

    data class ProbeResult(val ok: Boolean, val code: Int, val contentType: String, val sampleBytes: Int)

    /**
     * 流地址可达性探测：GET + Range 采样前 4KB（直播 CDN 通常不支持 Range，
     * 会忽略并回全量——读取限长立即断开）。用于验证取到的地址真实可拉流。
     */
    fun probe(url: String, headers: Map<String, String> = emptyMap()): ProbeResult {
        val request = newBuilder(url, headers)
            .header("Range", "bytes=0-4095")
            .get()
        client.newCall(request.build()).execute().use { response ->
            val stream = response.body?.byteStream()
            var read = 0
            if (stream != null) {
                val buf = ByteArray(4096)
                read = stream.read(buf)
            }
            val code = response.code
            val contentType = response.header("Content-Type").orEmpty()
            return ProbeResult(code in 200..299 && read > 0, code, contentType, read)
        }
    }

    /** 供 media3 OkHttpDataSource 复用同一套超时配置。 */
    fun callFactory(): okhttp3.Call.Factory = client

    fun get(url: String, headers: Map<String, String> = emptyMap()): String =
        execute(newBuilder(url, headers).get())

    fun postForm(url: String, headers: Map<String, String> = emptyMap(), form: Map<String, String>): String {
        val body = FormBody.Builder().apply {
            form.forEach { (k, v) -> add(k, v) }
        }.build()
        return execute(newBuilder(url, headers).post(body))
    }

    fun postJson(url: String, headers: Map<String, String>, json: String): String =
        execute(
            newBuilder(url, headers + mapOf("Content-Type" to "application/json"))
                .post(json.toRequestBody("application/json".toMediaType())),
        )

    fun postXml(url: String, headers: Map<String, String>, xml: String): String =
        execute(
            newBuilder(url, headers + mapOf("Content-Type" to "text/xml; charset=\"utf-8\""))
                .post(xml.toRequestBody("text/xml; charset=utf-8".toMediaType())),
        )

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
