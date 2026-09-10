package com.determinantmatrix.zhibo

import android.app.Application
import com.determinantmatrix.zhibo.core.database.toDomain
import com.determinantmatrix.zhibo.core.database.ZhiboDatabase
import com.determinantmatrix.zhibo.core.datastore.CredentialStore
import com.determinantmatrix.zhibo.core.datastore.SettingsRepository
import com.determinantmatrix.zhibo.core.model.AppConfig
import com.determinantmatrix.zhibo.core.model.Follower
import com.determinantmatrix.zhibo.core.monitor.EngineConfig
import com.determinantmatrix.zhibo.core.monitor.MonitorEngine
import com.determinantmatrix.zhibo.core.network.Http
import com.determinantmatrix.zhibo.core.resolver.BilibiliResolver
import com.determinantmatrix.zhibo.core.resolver.DouyuResolver
import com.determinantmatrix.zhibo.core.resolver.Fs1Resolver
import com.determinantmatrix.zhibo.core.resolver.HuyaResolver
import com.determinantmatrix.zhibo.core.resolver.ResolverRegistry
import com.determinantmatrix.zhibo.core.resolver.StreamgetResolver
import com.determinantmatrix.zhibo.core.resolver.UnsupportedResolver
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

/** 浏览器打开请求 — 内置浏览器的三种姿态。 */
sealed interface BrowserRequest {
    data object BilibiliLogin : BrowserRequest
    data object Fs1Auth : BrowserRequest
    data class Sniff(val url: String, val name: String) : BrowserRequest
}

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
        credentials = CredentialStore(this)
        val registry = buildRegistry()
        engineController = EngineController(registry)
        playerManager = PlayerManager(this, registry)
        browserRequest = MutableStateFlow(null)
    }

    lateinit var credentials: CredentialStore
        private set
    lateinit var browserRequest: MutableStateFlow<BrowserRequest?>
        private set

    private fun buildRegistry(): ResolverRegistry {
        val http = Http()
        // FS1 API 服务器漏发 LE YE1 中间证书，需要内置信任锚（桌面靠 Windows AIA 补链）
        val fs1Http = Http(extraTrustedCertificates = listOf("/certs/fs1_ye1_intermediate.der"))
        return ResolverRegistry(
            listOf(
                StreamgetResolver(
                    bilibili = BilibiliResolver(http) { credentials.bilibiliCookie() },
                    douyu = DouyuResolver(http),
                    huya = HuyaResolver(http),
                ),
                Fs1Resolver(fs1Http) { credentials.current() },
                UnsupportedResolver("streamlink", "安卓端 streamlink 插件开发中"),
                UnsupportedResolver("yt_dlp", "安卓端 yt-dlp 插件开发中"),
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
