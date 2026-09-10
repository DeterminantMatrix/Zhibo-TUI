package com.determinantmatrix.zhibo.core.monitor

import com.determinantmatrix.zhibo.core.model.Follower
import com.determinantmatrix.zhibo.core.model.LiveInfo
import com.determinantmatrix.zhibo.core.resolver.LiveResolver
import com.determinantmatrix.zhibo.core.resolver.ResolverRegistry
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MonitorEngineTest {

    private class FakeResolver(var live: Boolean, var fail: Boolean = false) : LiveResolver {
        override val name = "streamget"
        var checks = 0
        override suspend fun checkLive(url: String, quality: String): LiveInfo {
            checks++
            if (fail) throw java.io.IOException("connection timed out")
            return LiveInfo(
                isLive = live,
                anchorName = "测试主播",
                title = if (live) "测试标题" else "",
                platform = "bilibili",
            )
        }

        override suspend fun getStreamUrl(url: String, quality: String): String = "http://stream"
    }

    private fun follower(url: String = "https://live.bilibili.com/1", enabled: Boolean = true) = Follower(
        name = "测试", plugin = "streamget", url = url,
        platform = "bilibili", enabled = enabled,
    )

    @Test
    fun `backoff delay grows exponentially and caps`() {
        val tracker = PlatformHealthTracker(nowMillis = { 0L }, random = { 0.5 })
        val first = tracker.backoffDelay(1)
        val second = tracker.backoffDelay(2)
        val huge = tracker.backoffDelay(20)
        assertTrue(first in 12.0..18.0) // 15s ± 20%
        assertTrue(second in 24.0..36.0) // 30s ± 20%
        assertEquals(900.0, huge, 0.001) // 封顶
    }

    @Test
    fun `bilibili single source failure does not trip platform gate`() {
        var now = 0L
        val tracker = PlatformHealthTracker(nowMillis = { now })
        tracker.recordFailure("bilibili", source = "roomA", error = "timeout")
        assertTrue(!tracker.health("bilibili").isGated(now))
        // 第二个不同房间失败 → 触发退避
        tracker.recordFailure("bilibili", source = "roomB", error = "timeout")
        assertTrue(tracker.health("bilibili").isGated(now))
        // 过了退避窗口后恢复
        now += 20_000
        assertTrue(!tracker.health("bilibili").isGated(now))
        tracker.markHealthy("bilibili")
        assertEquals(0, tracker.health("bilibili").consecutiveFailures)
    }

    @Test
    fun `douyu style platform failure gates immediately`() {
        val tracker = PlatformHealthTracker(nowMillis = { 0L })
        tracker.recordFailure("douyu", source = null, error = "connection reset")
        assertTrue(tracker.health("douyu").isGated(0L))
    }

    @Test
    fun `connectivity classification matches desktop markers`() {
        assertTrue(PlatformHealthTracker.isConnectivityError("检测超时"))
        assertTrue(PlatformHealthTracker.isConnectivityError("DNS lookup failed"))
        assertTrue(!PlatformHealthTracker.isConnectivityError("房间不存在"))
    }

    @Test
    fun `engine reports live offline and emits live event once`() = runTest {
        val resolver = FakeResolver(live = false)
        val engine = MonitorEngine(
            scope = backgroundScope,
            registry = ResolverRegistry(listOf(resolver)),
            followersProvider = { listOf(follower()) },
            configProvider = { EngineConfig() },
            nowMillis = { 1_000L },
        )
        engine.runRound(EngineConfig(), manual = true)
        advanceUntilIdle()
        assertEquals(CheckState.OFFLINE, engine.statuses.value.values.single().state)
        assertTrue(engine.liveEvents.replayCache.isEmpty())

        resolver.live = true
        engine.runRound(EngineConfig(), manual = true)
        advanceUntilIdle()
        val status = engine.statuses.value.values.single()
        assertEquals(CheckState.LIVE, status.state)
        assertEquals("测试标题", status.title)
        assertEquals(1, engine.liveEvents.replayCache.size)

        // 已在线再检不再重复通知
        engine.runRound(EngineConfig(), manual = true)
        advanceUntilIdle()
        assertEquals(1, engine.liveEvents.replayCache.size)
    }

    @Test
    fun `error keeps stale live info and does not emit event`() = runTest {
        val resolver = FakeResolver(live = true)
        val engine = MonitorEngine(
            scope = backgroundScope,
            registry = ResolverRegistry(listOf(resolver)),
            followersProvider = { listOf(follower()) },
            configProvider = { EngineConfig() },
            nowMillis = { 1_000L },
        )
        engine.runRound(EngineConfig(), manual = true)
        assertEquals(1, engine.liveEvents.replayCache.size)

        resolver.fail = true
        engine.runRound(EngineConfig(), manual = true)
        val status = engine.statuses.value.values.single()
        assertEquals(CheckState.ERROR, status.state)
        assertTrue(status.stale)
        assertEquals("测试标题", status.title) // 上次在线信息保留
        assertEquals(1, engine.liveEvents.replayCache.size) // 不重复开播通知
        assertTrue(engine.platformHealth.value.isNotEmpty()) // 连接性错误 → 平台退避被记录
    }

    @Test
    fun `unknown plugin falls through chain and errors`() = runTest {
        val resolver = FakeResolver(live = false)
        val engine = MonitorEngine(
            scope = backgroundScope,
            registry = ResolverRegistry(listOf(resolver)),
            followersProvider = { listOf(follower().copy(plugin = "fs1", fallbackPlugins = listOf("streamget"))) },
            configProvider = { EngineConfig() },
            nowMillis = { 1_000L },
        )
        engine.runRound(EngineConfig(), manual = true)
        // fs1 未知 → 回退到 streamget 成功
        assertEquals(CheckState.OFFLINE, engine.statuses.value.values.single().state)
    }
}
