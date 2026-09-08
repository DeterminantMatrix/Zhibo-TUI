"""无人值守对话框自检 — 用 evaluate_js 注入真实点击，验证 前端→API→事件回推 全链路。

背景：P2 曾因 addEventListener 事件名大小写错误导致对话框按钮全部无响应，
单元测试（服务层）与语法检查都抓不到。此探针模拟真实按钮点击并断言：
对话框打开、API 往返（无变化保存报错就地显示）、确认页/返回/关闭迁移。
只做只读操作：设置不改值、导入用空地址，绝不写用户配置。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any


# 按钮查找用 textContent 匹配，不依赖渲染顺序。
PROBE_JS = r"""
(async () => {
  const out = {};
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const btns = () => [...document.querySelectorAll('#dlgBody .button-row button')];
  const findBtn = (t) => btns().find((b) => b.textContent.includes(t));
  try {
    document.getElementById('btnSettings').click();
    await sleep(600);
    out.settingsOpened = window.zhibo.dialog.kind === 'settings';
    const save = findBtn('保存');
    out.saveFound = !!save;
    if (save) save.click();
    await sleep(900);
    out.saveError = (document.querySelector('#dlgBody .dialog-error') || {}).textContent || '';
    let close = findBtn('关闭');
    out.closeFound = !!close;
    if (close) close.click();
    await sleep(300);
    out.settingsClosed = document.getElementById('dialogOverlay').hidden;

    document.getElementById('btnImport').click();
    await sleep(300);
    out.importOpened = window.zhibo.dialog.kind === 'import';
    const prev = findBtn('获取预览');
    out.previewFound = !!prev;
    if (prev) prev.click();
    await sleep(1500);
    out.importStage = window.zhibo.dialog.data.stage || '';
    const back = findBtn('返回');
    out.backFound = !!back;
    if (back) back.click();
    await sleep(300);
    out.importBackToForm = window.zhibo.dialog.data.stage === 'form';
    close = findBtn('关闭');
    if (close) close.click();
    await sleep(300);
    out.importClosed = document.getElementById('dialogOverlay').hidden;
  } catch (err) {
    out.error = String((err && err.message) || err);
  }
  window.__smokeResult = JSON.stringify(out);
})()
"""


def run_dialog_probe(window, timeout: float = 20.0) -> dict[str, Any]:
    """注入探针并等待结果；返回解析后的检查项字典（超时返回带 error 的字典）。"""
    result_box: dict[str, str] = {}

    def runner() -> None:
        deadline = time.time() + timeout
        injected = False
        while time.time() < deadline:
            try:
                if not injected and window.evaluate_js(
                    "!!(window.zhibo && document.getElementById('btnSettings'))"
                ):
                    window.evaluate_js(PROBE_JS)
                    injected = True
                if injected:
                    res = window.evaluate_js("window.__smokeResult || ''")
                    if res:
                        result_box["data"] = str(res)
                        break
            except Exception:
                pass
            time.sleep(0.25)

    thread = threading.Thread(target=runner, name="zhibo-webui-smoke", daemon=True)
    thread.start()
    thread.join(timeout=timeout + 5)
    data = result_box.get("data")
    if not data:
        return {"error": "探针超时，未收到结果"}
    try:
        return dict(json.loads(data))
    except ValueError:
        return {"error": f"探针结果无法解析：{data}"}


def verdict(checks: dict[str, Any]) -> tuple[bool, str]:
    """按预期断言检查项，返回 (是否通过, 摘要文本)。"""
    ok = (
        not checks.get("error")
        and bool(checks.get("settingsOpened"))
        and bool(checks.get("saveFound"))
        and "实质变化" in str(checks.get("saveError"))
        and bool(checks.get("closeFound"))
        and bool(checks.get("settingsClosed"))
        and bool(checks.get("importOpened"))
        and bool(checks.get("previewFound"))
        and checks.get("importStage") == "confirm"
        and bool(checks.get("backFound"))
        and bool(checks.get("importBackToForm"))
        and bool(checks.get("importClosed"))
    )
    return ok, json.dumps(checks, ensure_ascii=False)
