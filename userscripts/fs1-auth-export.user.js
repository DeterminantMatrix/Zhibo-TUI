// ==UserScript==
// @name         Zhibo FS1 授权导出
// @namespace    zhibo-local
// @version      1.1.0
// @description  在已登录的 FS1 页面提取当前请求快照并复制为 ZHIBO 可导入 JSON
// @include      /^https:\/\/(?:[a-z0-9-]+\.)*(?:fszb|fs)\d+\.com(?:\/|$)/
// @grant        GM_setClipboard
// @run-at       document-start
// ==/UserScript==

(function () {
    "use strict";

    if (!/(^|\.)(?:fszb|fs)\d+\.com$/i.test(window.location.hostname)) return;

    const SOURCE = "zhibo-fs1-auth-export";
    const REQUEST = "request-export";
    const RESPONSE = "export-response";
    const REQUEST_EVENT = "zhibo-fs1-auth-request";
    const RESPONSE_EVENT = "zhibo-fs1-auth-response";
    const API_PATH = "/v1/room";
    const ROOM_PATH = API_PATH;
    const PLAY_PATH = "/v230/play/url";
    const FORMAT = "zhibo.fs1-auth";
    const VERSION = 2;
    const MAX_CAPTURED_REQUESTS = 8;

    // Keep this allowlist broad enough to reproduce the FS frontend request,
    // but do not export unrelated headers from the page.
    const allowedHeaders = new Set([
        "accept", "accept-language", "api-version", "authorization",
        "content-type", "cookie", "device", "device2", "dnt", "dun-imei",
        "imei", "origin", "platform", "referer", "sec-ch-ua",
        "sec-ch-ua-mobile", "sec-ch-ua-platform", "sec-fetch-dest",
        "sec-fetch-mode", "sec-fetch-site", "user-agent", "version",
        "x-requested-with",
    ]);

    // The page context can observe headers attached by the site's fetch/XHR
    // calls. The userscript context cannot reliably patch those calls itself.
    function pageBridge() {
        const SOURCE = "zhibo-fs1-auth-export";
        const REQUEST = "request-export";
        const RESPONSE = "export-response";
        const REQUEST_EVENT = "zhibo-fs1-auth-request";
        const RESPONSE_EVENT = "zhibo-fs1-auth-response";
        const API_PATH = "/v1/room";
        const ROOM_PATH = API_PATH;
        const PLAY_PATH = "/v230/play/url";
        const FORMAT = "zhibo.fs1-auth";
        const VERSION = 2;
        const MAX_CAPTURED_REQUESTS = 8;
        const allowedHeaders = new Set([
            "accept", "accept-language", "api-version", "authorization",
            "content-type", "cookie", "device", "device2", "dnt", "dun-imei",
            "imei", "origin", "platform", "referer", "sec-ch-ua",
            "sec-ch-ua-mobile", "sec-ch-ua-platform", "sec-fetch-dest",
            "sec-fetch-mode", "sec-fetch-site", "user-agent", "version",
            "x-requested-with",
        ]);
        let latestRoomRequest = null;
        let latestPlayRequest = null;
        const capturedRequests = [];

        function asUrl(value) {
            try {
                return new URL(String(value || ""), window.location.href);
            } catch (_) {
                return null;
            }
        }

        function collectHeaders(value) {
            const result = {};
            if (!value) return result;
            try {
                if (value instanceof Headers) {
                    value.forEach((item, key) => {
                        const normalized = String(key).toLowerCase();
                        if (allowedHeaders.has(normalized)) result[normalized] = String(item);
                    });
                } else if (Array.isArray(value)) {
                    value.forEach((pair) => {
                        if (Array.isArray(pair) && pair.length >= 2) {
                            const normalized = String(pair[0]).toLowerCase();
                            if (allowedHeaders.has(normalized)) result[normalized] = String(pair[1]);
                        }
                    });
                } else {
                    Object.keys(value).forEach((key) => {
                        const normalized = key.toLowerCase();
                        if (allowedHeaders.has(normalized)) result[normalized] = String(value[key]);
                    });
                }
            } catch (_) {
                // Some Request/Headers implementations reject inspection.
            }
            return result;
        }

        function safeQuery(url) {
            const query = {};
            url.searchParams.forEach((value, key) => {
                // Avoid copying a future token/signature parameter.
                if (/(token|auth|cookie|signature|secret|password)/i.test(key)) return;
                if (!Object.prototype.hasOwnProperty.call(query, key)) query[key] = value;
            });
            return query;
        }

        function bodyEntries(body) {
            if (!body) return [];
            try {
                if (typeof body === "string" || body instanceof URLSearchParams) {
                    return Array.from(new URLSearchParams(body).entries());
                }
                if (typeof FormData !== "undefined" && body instanceof FormData) {
                    return Array.from(body.entries()).map((pair) => [pair[0], String(pair[1])]);
                }
            } catch (_) {
                // A non-inspectable body should not interfere with the site.
            }
            return [];
        }

        function summarizePlayBody(body) {
            const result = { body_keys: [] };
            const allowedKeys = new Set(["room_id", "code_id", "match_id", "sport_id", "time", "signature"]);
            bodyEntries(body).forEach(([key, value]) => {
                const normalized = String(key);
                if (!allowedKeys.has(normalized)) return;
                if (!result.body_keys.includes(normalized)) result.body_keys.push(normalized);
                if (normalized === "time") result.has_time = true;
                else if (normalized === "signature") result.has_signature = true;
                else result[normalized] = String(value);
            });
            result.body_keys.sort();
            return result;
        }

        function recordRequest(method, rawUrl, rawHeaders, body) {
            const url = asUrl(rawUrl);
            if (!url || url.protocol !== "https:") return;
            const path = url.pathname.replace(/\/+$/, "") || "/";
            if (path !== ROOM_PATH && path !== PLAY_PATH) return;

            const headers = collectHeaders(rawHeaders);
            const snapshot = {
                method: String(method || "GET").toUpperCase(),
                api_url: url.origin + path,
                request_url: url.href,
                query: safeQuery(url),
                headers,
            };
            if (path === ROOM_PATH) {
                snapshot.room_id = url.searchParams.get("room_id") || "";
                snapshot.sport_id = url.searchParams.get("sport_id") || "";
                snapshot.match_id = url.searchParams.get("match_id") || "";
                latestRoomRequest = snapshot;
            } else {
                snapshot.play_api_url = url.origin + path;
                snapshot.body = summarizePlayBody(body);
                latestPlayRequest = snapshot;
            }
            capturedRequests.push(snapshot);
            while (capturedRequests.length > MAX_CAPTURED_REQUESTS) capturedRequests.shift();
        }

        const originalFetch = window.fetch;
        if (typeof originalFetch === "function") {
            window.fetch = function (input, init) {
                try {
                    const request = input instanceof Request ? input : null;
                    const rawUrl = request ? request.url : typeof input === "string" ? input : input && input.url;
                    const headers = new Headers(request ? request.headers : undefined);
                    if (init && init.headers) {
                        new Headers(init.headers).forEach((value, key) => headers.set(key, value));
                    }
                    const method = (init && init.method) || (request && request.method) || "GET";
                    const body = init && Object.prototype.hasOwnProperty.call(init, "body") ? init.body : null;
                    recordRequest(method, rawUrl, headers, body);
                } catch (_) {
                    // Leave the site's request untouched if inspection fails.
                }
                return originalFetch.apply(this, arguments);
            };
        }

        const xhrState = new WeakMap();
        const originalOpen = XMLHttpRequest.prototype.open;
        const originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
        const originalSend = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.open = function (method, url) {
            xhrState.set(this, { method: method || "GET", url: String(url || ""), headers: {} });
            return originalOpen.apply(this, arguments);
        };
        XMLHttpRequest.prototype.setRequestHeader = function (key, value) {
            const state = xhrState.get(this);
            if (state) state.headers[String(key).toLowerCase()] = String(value);
            return originalSetRequestHeader.apply(this, arguments);
        };
        XMLHttpRequest.prototype.send = function (body) {
            const state = xhrState.get(this);
            if (state) recordRequest(state.method, state.url, state.headers, body);
            return originalSend.apply(this, arguments);
        };

        function exportCurrentRequest() {
            const room = latestRoomRequest || { headers: {}, query: {} };
            const play = latestPlayRequest || null;
            // Room headers win; a play request is a fallback because both
            // requests normally share the same auth/device headers.
            const headers = Object.assign({}, play && play.headers ? play.headers : {}, room.headers || {});
            const cookie = headers.cookie || document.cookie || "";
            const payload = {
                format: FORMAT,
                version: VERSION,
                generated_at: new Date().toISOString(),
                site_url: window.location.origin,
                api_url: room.api_url || "",
                request_url: room.request_url || "",
                room_id: room.room_id || (room.query && room.query.room_id) || "",
                sport_id: room.sport_id || (room.query && room.query.sport_id) || "1",
                match_id: room.match_id || (room.query && room.query.match_id) || "",
                api_version: headers["api-version"] || "",
                authorization: headers.authorization || "",
                imei: headers.imei || "",
                dun_imei: headers["dun-imei"] || "",
                user_agent: headers["user-agent"] || navigator.userAgent,
                client_version: headers.version || "",
                play_api_url: (play && (play.play_api_url || play.api_url)) || "",
                cookie,
                headers,
                request_snapshot: {
                    room,
                    play,
                    captured_at: new Date().toISOString(),
                },
                captured_requests: capturedRequests.slice(),
            };
            const hasAuthorization = Boolean(payload.authorization || payload.cookie);
            const result = {
                source: SOURCE,
                type: RESPONSE,
                ok: hasAuthorization,
                payload: hasAuthorization ? payload : null,
                error: hasAuthorization
                    ? ""
                    : "尚未捕获到授权请求；请刷新当前 FS 页面，进入直播间后再点导出",
            };
            document.dispatchEvent(new CustomEvent(RESPONSE_EVENT, { detail: result }));
            window.postMessage(result, "*");
        }

        document.addEventListener(REQUEST_EVENT, exportCurrentRequest);
        window.addEventListener("message", (event) => {
            if (event.source !== window || !event.data || event.data.source !== SOURCE) return;
            if (event.data.type === REQUEST) exportCurrentRequest();
        });
    }

    function injectPageBridge() {
        const script = document.createElement("script");
        script.textContent = "(" + pageBridge.toString() + ")();";
        (document.documentElement || document.head || document.body).appendChild(script);
        script.remove();
    }

    function copyText(text) {
        if (typeof GM_setClipboard === "function") {
            GM_setClipboard(text, "text");
            return Promise.resolve();
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(text);
        }
        return Promise.reject(new Error("当前浏览器不允许写入剪贴板"));
    }

    function installButton() {
        if (document.getElementById("zhibo-fs1-export-button")) return;
        const button = document.createElement("button");
        button.id = "zhibo-fs1-export-button";
        button.type = "button";
        button.textContent = "导出 FS1 授权";
        button.style.cssText = [
            "position:fixed", "right:18px", "bottom:18px", "z-index:2147483647",
            "padding:9px 13px", "border:1px solid #1683ff", "border-radius:7px",
            "background:#1683ff", "color:#fff", "font:14px sans-serif", "cursor:pointer",
            "box-shadow:0 2px 8px rgba(0,0,0,.25)",
        ].join(";");
        button.addEventListener("click", () => {
            if (extractionPending) return;
            extractionPending = true;
            button.disabled = true;
            button.textContent = "正在提取…";
            document.dispatchEvent(new CustomEvent(REQUEST_EVENT));
            window.postMessage({ source: SOURCE, type: REQUEST }, "*");
            extractionTimer = window.setTimeout(() => {
                if (!extractionPending) return;
                extractionPending = false;
                button.disabled = false;
                button.textContent = "导出 FS1 授权";
                window.alert("FS1 导出超时：页面桥接没有响应。请重新安装最新版脚本并刷新当前页面。 ");
            }, 4000);
        });
        document.body.appendChild(button);
    }

    let extractionPending = false;
    let extractionTimer = 0;

    function handleResponse(result) {
        if (!extractionPending || !result) return;
        extractionPending = false;
        if (extractionTimer) window.clearTimeout(extractionTimer);
        const button = document.getElementById("zhibo-fs1-export-button");
        if (button) {
            button.disabled = false;
            button.textContent = "导出 FS1 授权";
        }
        if (!result.ok || !result.payload) {
            window.alert("FS1 授权导出失败：" + (result.error || "没有捕获到授权"));
            return;
        }
        const text = JSON.stringify(result.payload, null, 2);
        copyText(text).then(() => {
            window.alert(
                "FS1 授权快照已复制。打开 ZHIBO 的“更新 → FS1 配置”，直接粘贴并开始更新。\n\n" +
                "导出包含房间请求、动态 match_id、播放接口结构和可用设备头。HttpOnly Cookie 无法由浏览器脚本读取；若站点把 Cookie 设为 HttpOnly，读取端会使用 authorization 和其他可见请求信息。"
            );
        }).catch((error) => {
            window.alert("已提取 FS1 授权，但写入剪贴板失败：" + error.message);
        });
    }

    document.addEventListener(RESPONSE_EVENT, (event) => handleResponse(event.detail));
    window.addEventListener("message", (event) => {
        if (event.source !== window || !event.data || event.data.source !== SOURCE) return;
        if (event.data.type === RESPONSE) handleResponse(event.data);
    });

    injectPageBridge();
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", installButton, { once: true });
    } else {
        installButton();
    }
})();
