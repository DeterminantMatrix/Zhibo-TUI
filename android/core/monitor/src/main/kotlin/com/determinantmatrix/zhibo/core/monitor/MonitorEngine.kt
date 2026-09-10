package com.determinantmatrix.zhibo.core.monitor

import com.determinantmatrix.zhibo.core.model.Follower
import com.determinantmatrix.zhibo.core.model.LiveInfo
import com.determinantmatrix.zhibo.core.resolver.FallbackChain
import com.determinantmatrix.zhibo.core.resolver.ResolverRegistry
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonPrimitive

/**
 * 监控引擎 — 桌面 MonitorService 的协程直译：
 * 轮询循环 + 并发闸 + 单项超时 + 平台熔断 + 关注项失败退避 + 开播事件流。
 * 纯 Kotlin（不依赖 Android），可 JVM 单测。
 */
class MonitorEngine(
    private val scope: CoroutineScope,
    private val registry: ResolverRegistry,
    private val followersProvider: suspend () -> List<Follower>,
    private val configProvider: suspend () -> EngineConfig,
    private val nowMillis: () -> Long = { System.currentTimeMillis() },
    private val checkTimeoutMillis: Long = 15_000,
) {

    private val _statuses = MutableStateFlow<Map<String, FollowerStatus>>(emptyMap())
    val statuses: StateFlow<Map<String, FollowerStatus>> = _statuses

    private val _liveEvents = MutableSharedFlow<LiveEvent>(replay = 16, extraBufferCapacity = 32)
    val liveEvents: SharedFlow<LiveEvent> = _liveEvents

    private val _platformHealth = MutableStateFlow<Map<String, PlatformHealthTracker.Health>>(emptyMap())
    val platformHealth: StateFlow<Map<String, PlatformHealthTracker.Health>> = _platformHealth

    private val tracker = PlatformHealthTracker(nowMillis)
    private val wake = Channel<Unit>(Channel.CONFLATED)

    /** follower.url → 下次应检时间（毫秒）。 */
    private val nextDue = HashMap<String, Long>()
    /** follower.url → 连续失败计数（关注项级失败退避）。 */
    private val errorCounts = HashMap<String, Int>()
    /** follower.url → 剩余跳过轮数。 */
    private val skipRounds = HashMap<String, Int>()

    private var loopJob: kotlinx.coroutines.Job? = null

    fun start() {
        if (loopJob?.isActive == true) return
        loopJob = scope.launch { loop() }
    }

    fun stop() {
        loopJob?.cancel()
        loopJob = null
    }

    /** 手动刷新：中断休眠立即开一轮（全量检测，无视关注项间隔）。 */
    fun refreshNow() {
        wake.trySend(Unit)
    }

    private suspend fun loop() {
        while (true) {
            val cfg = configProvider()
            runRound(cfg, manual = false)
            val sleepMillis = sleepMillis(cfg)
            withTimeoutOrNull(sleepMillis) { wake.receive() }
        }
    }

    /** 距下一次有人应检的毫秒数；无记录时为全局间隔。 */
    private fun sleepMillis(cfg: EngineConfig): Long {
        val now = nowMillis()
        val global = cfg.pollIntervalSeconds * 1000L
        if (nextDue.isEmpty()) return global
        val earliest = nextDue.values.minOrNull() ?: return global
        return (earliest - now).coerceIn(1000L, global)
    }

    /** 执行一轮检测。manual=true 时跳过关注项间隔与跳过轮限制（对齐桌面手动刷新语义）。 */
    suspend fun runRound(cfg: EngineConfig, manual: Boolean) {
        val followers = followersProvider().filter { it.enabled }
        val now = nowMillis()
        val semaphore = Semaphore(cfg.maxConcurrentChecks)
        coroutineScope {
            followers.map { f ->
                async {
                    if (!manual) {
                        if ((nextDue[f.url] ?: 0L) > now) return@async
                        if ((skipRounds[f.url] ?: 0) > 0) {
                            skipRounds[f.url] = (skipRounds[f.url] ?: 0) - 1
                            return@async
                        }
                    }
                    if (tracker.health(f.platform).isGated(nowMillis())) return@async
                    semaphore.withPermit { checkOne(f, cfg) }
                }
            }.awaitAll()
        }
        _platformHealth.value = tracker.snapshot()
    }

    private suspend fun checkOne(f: Follower, cfg: EngineConfig) {
        val chain = FallbackChain.forFollower(f.plugin, f.fallbackPlugins, f.platform)
        var lastError: Throwable? = null
        for (pluginName in chain) {
            val resolver = registry.get(pluginName)
            if (resolver == null) {
                lastError = IllegalStateException("未知插件: $pluginName")
                continue
            }
            try {
                val info = withTimeout(checkTimeoutMillis) {
                    resolver.checkLive(f.url, f.quality, f.extra)
                }
                onCheckOk(f, info)
                return
            } catch (t: Throwable) {
                if (t is kotlinx.coroutines.CancellationException) throw t
                lastError = t
            }
        }
        onCheckError(f, lastError ?: IllegalStateException("检测失败"), cfg)
    }

    private fun onCheckOk(f: Follower, info: LiveInfo) {
        tracker.markHealthy(f.platform)
        errorCounts.remove(f.url)
        skipRounds.remove(f.url)
        scheduleNext(f)
        val prev = _statuses.value[f.url]
        val newState = if (info.isLive) CheckState.LIVE else CheckState.OFFLINE
        _statuses.value = _statuses.value + (f.url to FollowerStatus(
            state = newState,
            title = info.title,
            anchorName = info.anchorName,
            qualityName = info.qualityName,
            checkedAtMillis = nowMillis(),
        ))
        if (info.isLive && prev?.state != CheckState.LIVE) {
            _liveEvents.tryEmit(
                LiveEvent(
                    name = f.name,
                    url = f.url,
                    platform = f.platform,
                    anchorName = info.anchorName,
                    title = info.title,
                ),
            )
        }
    }

    private fun onCheckError(f: Follower, error: Throwable, cfg: EngineConfig) {
        val message = error.message ?: error.toString()
        if (PlatformHealthTracker.isConnectivityError(message)) {
            tracker.recordFailure(f.platform, source = f.url, error = message)
        }
        val prev = _statuses.value[f.url]
        val count = (errorCounts[f.url] ?: 0) + 1
        errorCounts[f.url] = count
        if (count >= cfg.failureBackoffAfter) {
            skipRounds[f.url] = cfg.failureBackoffPolls
            errorCounts[f.url] = 0
        }
        scheduleNext(f)
        // 沿用桌面"红色状态"语义：保留上一次在线信息
        val carried = prev?.takeIf { it.state == CheckState.LIVE }
        _statuses.value = _statuses.value + (f.url to FollowerStatus(
            state = CheckState.ERROR,
            title = carried?.title.orEmpty(),
            anchorName = carried?.anchorName.orEmpty(),
            qualityName = carried?.qualityName.orEmpty(),
            error = message,
            stale = carried != null,
            checkedAtMillis = nowMillis(),
        ))
    }

    private fun scheduleNext(f: Follower) {
        val override = (f.extra["poll_interval"] as? JsonPrimitive)?.content?.toIntOrNull()
            ?.coerceIn(5, 3600)
        val intervalMillis = (override ?: 0) * 1000L
        nextDue[f.url] = nowMillis() + if (intervalMillis > 0) intervalMillis else 0L
    }

    private fun snapshotHealth(): Map<String, PlatformHealthTracker.Health> = tracker.snapshot()
}
