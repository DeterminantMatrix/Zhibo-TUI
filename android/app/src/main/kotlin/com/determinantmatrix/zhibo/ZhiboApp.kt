package com.determinantmatrix.zhibo

import android.app.Application
import com.determinantmatrix.zhibo.core.database.ZhiboDatabase
import com.determinantmatrix.zhibo.core.datastore.SettingsRepository

/** M0 手工依赖注入；后续模块化时替换为 Hilt。 */
class ZhiboApp : Application() {

    lateinit var database: ZhiboDatabase
        private set
    lateinit var settings: SettingsRepository
        private set

    override fun onCreate() {
        super.onCreate()
        instance = this
        database = ZhiboDatabase.build(this)
        settings = SettingsRepository(this)
    }

    companion object {
        lateinit var instance: ZhiboApp
            private set
    }
}
