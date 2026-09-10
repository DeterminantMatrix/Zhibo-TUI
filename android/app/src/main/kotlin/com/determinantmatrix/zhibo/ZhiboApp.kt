package com.determinantmatrix.zhibo

import android.app.Application
import com.determinantmatrix.zhibo.core.database.toDomain
import com.determinantmatrix.zhibo.core.database.ZhiboDatabase
import com.determinantmatrix.zhibo.core.datastore.SettingsRepository
import com.determinantmatrix.zhibo.core.model.AppConfig
import com.determinantmatrix.zhibo.core.model.Follower
import com.determinantmatrix.zhibo.core.monitor.EngineConfig
import com.determinantmatrix.zhibo.core.monitor.MonitorEngine
import com.determinantmatrix.zhibo.core.network.Http
import com.determinantmatrix.zhibo.core.resolver.BilibiliResolver
import com.determinantmatrix.zhibo.core.resolver.DouyuResolver
import com.determinantmatrix.zhibo.core.resolver.HuyaResolver
import com.determinantmatrix.zhibo.core.resolver.ResolverRegistry
import com.determinantmatrix.zhibo.core.resolver.StreamgetResolver
import com.determinantmatrix.zhibo.core.resolver.UnsupportedResolver
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/** M0 手工依赖注入；后续模块化时替换为 Hilt。 */
class ZhiboApp : Application() {

    lateinit var database: ZhiboDatabase
        private set
    lateinit var settings: SettingsRepository
        private set
    lateinit var engineController: EngineController
        private set
    lateinit var playerManager: PlayerManager
        private set

    override fun onCreate() {
        super.onCreate()
        instance = this
        database = ZhiboDatabase.build(this)
        settings = SettingsRepository(this)
        val registry = buildRegistry()
        engineController = EngineController(registry)
        playerManager = PlayerManager(this, registry)
    }

    private fun buildRegistry(): ResolverRegistry {
        val http = Http()
        return ResolverRegistry(
            listOf(
                StreamgetResolver(
                    bilibili = BilibiliResolver(http),
                    douyu = DouyuResolver(http),
                    huya = HuyaResolver(http),
                ),
                UnsupportedResolver("streamlink", "安卓端 streamlink 插件开发中"),
                UnsupportedResolver("yt_dlp", "安卓端 yt-dlp 插件开发中"),
                UnsupportedResolver("fs1", "安卓端 FS1 插件开发中（M3 随浏览器授权一并落地）"),
            ),
        )
    }

    companion object {
        lateinit var instance: ZhiboApp
            private set
    }
}

/** 持有监控引擎与应用级作用域；前台服务只负责保活与通知。 */
class EngineController(private val registry: ResolverRegistry) {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    @Volatile
    private var latestFollowers: List<Follower> = emptyList()

    @Volatile
    private var latestConfig: AppConfig = AppConfig()

    val running = kotlinx.coroutines.flow.MutableStateFlow(false)

    suspend fun latestNotificationsEnabled(): Boolean = latestConfig.notificationsEnabled

    val engine = MonitorEngine(
        scope = scope,
        registry = registry,
        followersProvider = { latestFollowers },
        configProvider = { EngineConfig.from(latestConfig) },
    )

    init {
        scope.launch {
            ZhiboApp.instance.database.followerDao().observeAll()
                .collect { entities -> latestFollowers = entities.map { it.toDomain() } }
        }
        scope.launch {
            ZhiboApp.instance.settings.config.collect { latestConfig = it }
        }
    }
}
