package com.determinantmatrix.zhibo

import com.determinantmatrix.zhibo.core.model.Follower
import com.determinantmatrix.zhibo.core.monitor.CheckState
import com.determinantmatrix.zhibo.core.monitor.FollowerStatus
import com.determinantmatrix.zhibo.core.monitor.MonitorEngine
import com.determinantmatrix.zhibo.core.network.Http
import com.determinantmatrix.zhibo.core.resolver.FallbackChain
import com.determinantmatrix.zhibo.core.resolver.ResolverRegistry
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext

/**
 * 直播源诊断：对全部在线关注项执行「取流 → 真实拉流采样」，
 * 报告每路源的可达性、类型与耗时。这是采集能力的直接验收。
 */
object StreamDiagnostics {

    data class Row(
        val name: String,
        val platform: String,
        val plugin: String,
        val statusState: String,      // LIVE / OFFLINE / ERROR / 未检测
        val streamOk: Boolean?,       // null = 未尝试（未在线）
        val probeCode: Int,
        val contentType: String,
        val sampleBytes: Int,
        val ms: Long,
        val error: String,
    )

    private val CONCURRENCY = Semaphore(4)

    suspend fun sweep(
        registry: ResolverRegistry,
        followers: List<Follower>,
        statuses: Map<String, FollowerStatus>,
        checkTimeoutMillis: Long = 15_000,
    ): List<Row> = withContext(Dispatchers.IO) {
        val enabled = followers.filter { it.enabled }
        val semaphore = Semaphore(4)
        coroutineScope {
            enabled.map { f ->
                async {
                    semaphore.withPermit { diagnose(registry, f) }
                }
            }.awaitAll()
        }.sortedBy { it.platform }
    }

    private suspend fun diagnose(
        registry: ResolverRegistry,
        f: Follower,
    ): Row = withContext(Dispatchers.IO) {
        val chain = com.determinantmatrix.zhibo.core.resolver.FallbackChain
            .forFollower(f.plugin, f.fallbackPlugins, f.platform)
        var lastError = ""

        var info: com.determinantmatrix.zhibo.core.model.LiveInfo? = null
        var usedPlugin = ""
        for (pluginName in chain) {
            val resolver = registry.get(pluginName)
            if (resolver == null) { lastError = "未知插件 $pluginName"; continue }
            try {
                info = kotlinx.coroutines.withTimeout(15_000) {
                    resolver.checkLive(f.url, f.quality, f.extra)
                }
                usedPlugin = pluginName
                lastError = ""   // 链上后续插件成功即清除前面的失败记录
            } catch (t: Throwable) {
                if (t is kotlinx.coroutines.CancellationException) throw t
                lastError = t.message ?: t.toString()
            }
        }

        val live = info?.isLive == true
        if (!live) {
            return@withContext Row(
                name = f.name, platform = f.platform, plugin = usedPlugin.ifEmpty { chain.firstOrNull() ?: "?" },
                statusState = "离线/未播", streamOk = null, probeCode = 0, contentType = "",
                sampleBytes = 0, ms = 0, error = lastError,
            )
        }

        // 2) 在线 → 取流
        var streamUrl = info?.streamUrl.orEmpty()
        if (streamUrl.isEmpty()) {
            // 逐插件尝试取流
            for (pluginName in chain) {
                val resolver = registry.get(pluginName) ?: continue
                try {
                    streamUrl = kotlinx.coroutines.withTimeout(15_000) {
                        resolver.getStreamUrl(f.url, f.quality)
                    }
                    break
                } catch (t: Throwable) {
                    if (t is kotlinx.coroutines.CancellationException) throw t
                    streamUrl = ""
                    lastError = t.message ?: t.toString()
                }
            }
        }
        if (streamUrl.isEmpty()) {
            return@withContext Row(
                name = f.name, platform = f.platform, plugin = usedPlugin,
                statusState = "在线", streamOk = false, probeCode = 0, contentType = "",
                sampleBytes = 0, ms = 0, error = lastError.ifEmpty { "取流失败" },
            )
        }

        // 3) 真实拉流采样
        val t0 = System.currentTimeMillis()
        val probe = runCatching { Http().probe(streamUrl) }.getOrElse {
            return@withContext Row(
                name = f.name, platform = f.platform, plugin = usedPlugin,
                statusState = "在线", streamOk = false, probeCode = 0, contentType = "",
                sampleBytes = 0, ms = System.currentTimeMillis() - t0,
                error = "拉流失败：${it.message?.take(60)}",
            )
        }
        Row(
            name = f.name, platform = f.platform, plugin = usedPlugin,
            statusState = "在线", streamOk = probe.ok, probeCode = probe.code,
            contentType = probe.contentType, sampleBytes = probe.sampleBytes,
            ms = System.currentTimeMillis() - t0, error = lastError,
        )
    }
}
