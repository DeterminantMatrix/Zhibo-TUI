package com.determinantmatrix.zhibo

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import com.determinantmatrix.zhibo.core.monitor.CheckState
import com.determinantmatrix.zhibo.core.monitor.LiveEvent
import kotlinx.coroutines.launch

/**
 * 前台保活服务：Android 14+ 使用 specialUse 类型（侧载分发，不受 Play 审核
 * 与 dataSync 6 小时限制约束）。进程内引擎随应用创建，这里只负责通知。
 */
class MonitorService : Service() {

    private var collectJob: Job? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        startAsForeground()
        if (collectJob == null) {
            val controller = ZhiboApp.instance.engineController
            controller.engine.start()
            controller.running.value = true
            collectJob = scope.launch {
                launch {
                    controller.engine.statuses.collect { statuses ->
                        notifyStatus(statuses.values.count { it.state == CheckState.LIVE })
                    }
                }
                launch {
                    controller.engine.liveEvents.collect { event ->
                        // 对齐桌面：全局通知关闭时不弹开播通知
                        if (controller.latestNotificationsEnabled()) {
                            notifyLive(event)
                        }
                    }
                }
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        collectJob?.cancel()
        scope.cancel()
        ZhiboApp.instance.engineController.apply {
            engine.stop()
            running.value = false
        }
        super.onDestroy()
    }

    private fun startAsForeground() {
        val notification = statusNotification(0)
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(STATUS_NOTIF_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(STATUS_NOTIF_ID, notification)
        }
    }

    private fun notifyStatus(liveCount: Int) {
        val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        manager.notify(STATUS_NOTIF_ID, statusNotification(liveCount))
    }

    private fun statusNotification(liveCount: Int): Notification {
        ensureChannels()
        val stopIntent = PendingIntent.getService(
            this, 1,
            Intent(this, MonitorService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_MONITOR)
            .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
            .setContentTitle("直播监控运行中")
            .setContentText("在线 $liveCount 位主播 · 点此打开应用")
            .setOngoing(true)
            .addAction(0, "停止监控", stopIntent)
            .build()
    }

    private fun notifyLive(event: LiveEvent) {
        ensureChannels()
        val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        val tapIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(this, CHANNEL_LIVE)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("${event.name} 开播了")
            .setContentText(event.title.ifEmpty { event.anchorName.ifEmpty { "点击查看" } })
            .setAutoCancel(true)
            .setContentIntent(tapIntent)
            .build()
        manager.notify(LIVE_NOTIF_BASE + event.url.hashCode(), notification)
    }

    private fun ensureChannels() {
        if (Build.VERSION.SDK_INT >= 26) {
            val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_MONITOR, "监控状态", NotificationManager.IMPORTANCE_LOW),
            )
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_LIVE, "开播通知", NotificationManager.IMPORTANCE_HIGH),
            )
        }
    }

    companion object {
        const val ACTION_STOP = "com.determinantmatrix.zhibo.STOP"
        const val CHANNEL_MONITOR = "monitor_status"
        const val CHANNEL_LIVE = "live_alerts"
        const val STATUS_NOTIF_ID = 1001
        const val LIVE_NOTIF_BASE = 2000
    }
}
