/* ZHIBO Web 前端逻辑（P0：窗口控制与后端连通性） */
"use strict";

const zhibo = {
  maximized: false,

  async pingBackend() {
    const status = document.getElementById("statusBackend");
    try {
      const reply = await window.pywebview.api.ping();
      status.textContent = "后端：连接 OK（" + reply + "）";
    } catch (err) {
      status.textContent = "后端：桥接未就绪，重试中…";
      setTimeout(() => this.pingBackend(), 500);
    }
  },

  wireWindowControls() {
    document.getElementById("btnMin").addEventListener("click", () => {
      window.pywebview.api.minimizeWindow();
    });
    document.getElementById("btnMax").addEventListener("click", async () => {
      this.maximized = !this.maximized;
      if (this.maximized) {
        await window.pywebview.api.maximizeWindow();
      } else {
        await window.pywebview.api.restoreWindow();
      }
    });
    document.getElementById("btnClose").addEventListener("click", () => {
      window.pywebview.api.hideToTray();
    });
    // 菜单项 P0 仅高亮，不展开。
    document.querySelectorAll(".menu-item").forEach((item) => {
      item.addEventListener("click", () => {
        document.querySelectorAll(".menu-item").forEach((i) => i.classList.remove("open"));
        item.classList.add("open");
        document.getElementById("statusInfo").textContent =
          "菜单「" + item.textContent + "」将在后续阶段开放";
      });
    });
  },

  boot() {
    this.wireWindowControls();
    // pywebview 桥注入晚于页面加载：等待 window.pywebview 就绪。
    const waitBridge = () => {
      if (window.pywebview && window.pywebview.api) {
        this.pingBackend();
      } else {
        setTimeout(waitBridge, 100);
      }
    };
    window.addEventListener("pywebviewready", () => this.pingBackend());
    waitBridge();
  },
};

window.zhibo = zhibo;
document.addEventListener("DOMContentLoaded", () => zhibo.boot());
