package com.determinantmatrix.zhibo

import android.webkit.CookieManager
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.determinantmatrix.zhibo.core.resolver.Fs1Auth
import com.determinantmatrix.zhibo.core.resolver.Fs1Trust
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * 内置浏览器 — 三种姿态（对齐设计稿 §8）：
 * BILIBILI_LOGIN 登录采集 Cookie；FS1_AUTH 被动抓取 /v1/room 授权头；SNIFF 房间页嗅探流地址。
 * 凭据只进加密存储，绝不写入关注项或日志。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BrowserScreen(request: BrowserRequest, onDone: () -> Unit) {
    val app = ZhiboApp.instance
    val clickScope = rememberCoroutineScope()
    val captured = remember { mutableStateOf(false) }
    val statusText = remember {
        mutableStateOf(
            when (request) {
                is BrowserRequest.BilibiliLogin -> "登录后自动采集 Cookie（检测到 SESSDATA 即保存）"
                is BrowserRequest.Fs1Auth -> "进入飞速直播页面，自动捕获 /v1/room 授权请求"
                is BrowserRequest.Sniff -> "打开直播间页面，自动记录 m3u8 / flv 流地址"
            },
        )
    }
    val sniffed = remember { mutableStateListOf<String>() }
    var webViewRef by remember { mutableStateOf<WebView?>(null) }
    val req = request

    // B 站：定时检查登录态 Cookie（CookieManager 特权 API 可读 HttpOnly）
    if (request is BrowserRequest.BilibiliLogin) {
        LaunchedEffect(Unit) {
            while (!captured.value) {
                delay(2000)
                val cookie = CookieManager.getInstance().getCookie("https://live.bilibili.com/")
                if (cookie?.contains("SESSDATA=") == true) {
                    app.credentials.saveBilibiliCookie(cookie)
                    captured.value = true
                    statusText.value = "✅ 已采集 B 站 Cookie（含 SESSDATA），高清画质已解锁"
                }
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        when (request) {
                            is BrowserRequest.BilibiliLogin -> "B 站登录采集"
                            is BrowserRequest.Fs1Auth -> "FS1 授权采集"
                            is BrowserRequest.Sniff -> "流地址嗅探"
                        },
                    )
                },
                actions = {
                    Button(
                        onClick = {
                            webViewRef?.stopLoading()
                            onDone()
                        },
                        modifier = Modifier.padding(end = 12.dp),
                    ) { Text("完成") }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = statusText.value,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(horizontal = 16.dp),
            )

            AndroidView(
                factory = { ctx ->
                    WebView(ctx).apply {
                        settings.javaScriptEnabled = true
                        settings.domStorageEnabled = true
                        CookieManager.getInstance().setAcceptCookie(true)
                        CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)
                        webViewClient = object : WebViewClient() {
                            override fun shouldInterceptRequest(
                                view: WebView,
                                request: WebResourceRequest,
                            ): android.webkit.WebResourceResponse? {
                                val url = request.url.toString()
                                when (req) {
                                    is BrowserRequest.Fs1Auth -> {
                                        if (!captured.value && Fs1Trust.isTrustedApi(url)) {
                                            val auth = request.requestHeaders
                                            val token = auth?.get("authorization").orEmpty()
                                            if (token.isNotEmpty()) {
                                                val site = view.url
                                                    ?.takeIf { Fs1Trust.isTrustedSite(it) }
                                                    ?.split('/')
                                                    ?.take(3)
                                                    ?.joinToString("/")
                                                val siteUrl = site ?: Fs1Trust.DEFAULT_SITE_URL
                                                app.credentials.saveFs1Auth(
                                                    Fs1Auth(
                                                        siteUrl = siteUrl,
                                                        apiUrl = url,
                                                        token = token,
                                                        apiVersion = auth.get("api-version") ?: "8",
                                                        imei = auth.get("imei").orEmpty(),
                                                        dunImei = auth.get("dun-imei").orEmpty(),
                                                    ),
                                                )
                                                captured.value = true
                                                statusText.value = "✅ 已捕获 FS1 授权（域名校验通过），监控即时生效"
                                            }
                                        }
                                    }
                                    is BrowserRequest.Sniff -> {
                                        val path = url.substringBefore('?').lowercase()
                                        val host = request.url.host?.lowercase().orEmpty()
                                        val media = path.endsWith(".m3u8") || path.endsWith(".flv") ||
                                            host.contains("bilivideo") || host.contains("mcdn")
                                        val image = listOf(".jpg", ".png", ".gif", ".webp", ".css", ".js")
                                            .any { path.endsWith(it) }
                                        if (media && !image) {
                                            if (sniffed.none { it == url }) {
                                                sniffed.add(url)
                                                statusText.value = "已捕获 ${sniffed.size} 条流地址，点击直接播放"
                                            }
                                        }
                                    }
                                    else -> Unit
                                }
                                return null
                            }
                        }
                        loadUrl(
                            when (request) {
                                is BrowserRequest.BilibiliLogin -> "https://www.bilibili.com"
                                is BrowserRequest.Fs1Auth -> Fs1Trust.DEFAULT_SITE_URL
                                is BrowserRequest.Sniff -> request.url
                            },
                        )
                    }.also { webViewRef = it }
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f),
            )

            if (request is BrowserRequest.Sniff && sniffed.isNotEmpty()) {
                LazyColumn(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp)
                        .weight(0.3f),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    items(sniffed) { url ->
                        Card(modifier = Modifier.fillMaxWidth()) {
                            Row(
                                modifier = Modifier.padding(8.dp),
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                OutlinedButton(onClick = {
                                    val name = (request as BrowserRequest.Sniff).name
                                    clickScope.launch {
                                        app.playerManager.playDirect(name, "sniff", url)
                                        onDone()
                                    }
                                }) { Text("播放") }
                                Text(
                                    text = url.takeLast(60),
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}
