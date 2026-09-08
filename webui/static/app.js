/* ZHIBO Web 前端逻辑
 * P1：表格 / 快照推送 / 标签筛选 / 搜索 / 排序 / 行内画质与插件下拉
 */
"use strict";

const zhibo = {
  maximized: false,
  snapshot: { rows: [], tags: ["全部"], plugin_options: [] },
  tag: "全部",
  stateFilter: "全部",
  search: "",
  sortKey: "default",
  sortDesc: false,
  selectedIdx: -1,
  polling: false,

  // ---------- 快照与渲染 ----------

  onEvents(batch) {
    for (const ev of batch) {
      switch (ev.kind) {
        case "snapshot":
          this.snapshot = ev.payload;
          this.renderTags();
          this.renderRows();
          this.renderStatus();
          break;
        case "polling":
          this.polling = ev.payload.active;
          this.renderStatus();
          break;
        case "log":
          this.setInfo(ev.payload.text);
          break;
        case "liveEvent":
          this.setInfo(
            (ev.payload.is_live ? "↑ " : "↓ ") + ev.payload.name +
            (ev.payload.is_live ? " 开播" : " 下播")
          );
          break;
        case "operationFinished":
          this.setInfo(ev.payload.message || (ev.payload.ok ? "操作完成" : "操作失败"));
          break;
        case "fatal":
          this.setInfo("致命错误：" + ev.payload.message);
          break;
        default:
          break;
      }
    }
  },

  visibleRows() {
    const q = this.search.trim().toLowerCase();
    let rows = this.snapshot.rows.filter((row) => {
      if (this.tag !== "全部" && !(row.tags || []).includes(this.tag)) return false;
      if (this.stateFilter === "在线" && !row.live) return false;
      if (this.stateFilter === "离线" && (row.live || (row.error && row.error !== "-"))) return false;
      if (this.stateFilter === "异常" && (!row.error || row.error === "-")) return false;
      if (q) {
        const hay = [row.name, row.platform, row.title, row.plugin,
                     (row.tags || []).join(" "), row.error].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    rows.sort(this.rowComparator());
    return rows;
  },

  // 中文按拼音（Chromium ICU zh 排序规则），在线行永远最前。
  rowComparator() {
    const key = this.sortKey;
    const desc = this.sortDesc;
    const zh = "zh-Hans-CN";
    const cmpText = (a, b) => (a || "").localeCompare(b || "", zh);
    if (key === "default") {
      return (a, b) => {
        const r = (a.live === b.live) ? 0 : (a.live ? -1 : 1);
        if (r !== 0) return desc ? -r : r;
        let c = cmpText((a.tags || []).join(","), (b.tags || []).join(","));
        if (c !== 0) return desc ? -c : c;
        c = cmpText(a.platform, b.platform);
        if (c !== 0) return desc ? -c : c;
        return desc ? -cmpText(a.name, b.name) : cmpText(a.name, b.name);
      };
    }
    const val = (row) => key === "tags" ? (row.tags || []).join(",") : (row[key] || "");
    return (a, b) => {
      const r = (a.live === b.live) ? 0 : (a.live ? -1 : 1);
      if (r !== 0) return r;
      const va = val(a), vb = val(b);
      if (!va && vb) return 1;        // 空值沉底
      if (va && !vb) return -1;
      const c = va.localeCompare(vb, zh);
      return desc ? -c : c;
    };
  },

  statusGlyph(row) {
    if (!row.enabled) return { cls: "dot-disabled", text: "⊘" };
    if (row.live) return { cls: "dot-live", text: "" };
    if (row.checking) return { cls: "dot-checking", text: "" };
    return { cls: "dot-offline", text: "" };
  },

  renderTags() {
    const box = document.getElementById("tagTabs");
    const tags = this.snapshot.tags && this.snapshot.tags.length ? this.snapshot.tags : ["全部"];
    if (!tags.includes(this.tag)) this.tag = "全部";
    box.innerHTML = "";
    for (const tag of tags) {
      const btn = document.createElement("button");
      btn.className = "tag-tab" + (tag === this.tag ? " active" : "");
      btn.textContent = tag;
      btn.addEventListener("click", () => {
        this.tag = tag;
        this.persist();
        this.renderTags();
        this.renderRows();
        document.getElementById("winTitle").textContent = "直播监控工具 - [" + tag + "]";
      });
      box.appendChild(btn);
    }
  },

  renderRows() {
    const tbody = document.getElementById("tableBody");
    const rows = this.visibleRows();
    document.getElementById("emptyHint").className =
      "empty-hint" + (rows.length ? "" : " show");
    if (!rows.some((r) => r.idx === this.selectedIdx)) {
      this.selectedIdx = rows.length ? rows[0].idx : -1;
    }
    const plugins = this.snapshot.plugin_options || [];
    const frag = document.createDocumentFragment();
    for (const row of rows) {
      const tr = document.createElement("tr");
      tr.dataset.idx = row.idx;
      if (row.idx === this.selectedIdx) tr.className = "selected";

      const glyph = this.statusGlyph(row);
      const errCell = row.error && row.error !== "-" ? row.error : "";
      const cells = [
        `<td><span class="dot ${glyph.cls}">${glyph.text}</span></td>`,
        `<td title="${this.esc((row.tags || []).join("、"))}">${this.esc((row.tags || []).join("、") || "-")}</td>`,
        `<td title="${this.esc(row.name)}">${this.esc(row.name)}</td>`,
        `<td>${this.esc(row.platform)}</td>`,
        `<td title="${this.esc(row.title)}">${this.esc(row.title || "-")}</td>`,
      ];
      // 画质下拉：值跟随已保存配置，保存成功由快照刷新。
      const qualityOpts = (row.quality_options || []);
      const configuredQ = row.configured_quality || "best";
      let qSel = `<select data-act="quality" data-idx="${row.idx}">`;
      let qMatched = false;
      for (const opt of qualityOpts) {
        const sel = opt.value.toLowerCase() === String(configuredQ).toLowerCase();
        if (sel) qMatched = true;
        qSel += `<option value="${this.esc(opt.value)}"${sel ? " selected" : ""}>${this.esc(opt.label)}</option>`;
      }
      if (!qMatched) {
        qSel = `<option value="${this.esc(configuredQ)}" selected>${this.esc(configuredQ)}</option>` + qSel;
      }
      qSel += "</select>";
      cells.push(`<td>${qSel}</td>`);

      cells.push(`<td>${this.esc(row.last_check || "-")}</td>`);
      cells.push(`<td>${this.esc(row.health || "-")}</td>`);

      const configuredP = row.configured_plugin || row.plugin || "";
      let pSel = `<select data-act="plugin" data-idx="${row.idx}">`;
      if (configuredP && !plugins.includes(configuredP)) {
        pSel += `<option value="${this.esc(configuredP)}" selected>${this.esc(configuredP)}</option>`;
      }
      for (const name of plugins) {
        const sel = name.toLowerCase() === String(configuredP).toLowerCase();
        pSel += `<option value="${this.esc(name)}"${sel ? " selected" : ""}>${this.esc(name)}</option>`;
      }
      pSel += "</select>";
      cells.push(`<td>${pSel}</td>`);

      cells.push(
        `<td class="${errCell ? "err-text" : ""}" title="${this.esc(errCell)}">${this.esc(errCell)}</td>`
      );
      tr.innerHTML = cells.join("");
      frag.appendChild(tr);
    }
    tbody.innerHTML = "";
    tbody.appendChild(frag);
  },

  renderStatus() {
    const rows = this.snapshot.rows || [];
    const online = rows.filter((r) => r.live).length;
    document.getElementById("statusSummary").textContent =
      `在线 ${online} / 总计 ${rows.length}`;
    document.getElementById("statusPoll").textContent = this.polling
      ? "● 检测中…"
      : `○ 间隔 ${this.snapshot.poll_interval || "-"}s`;
  },

  setInfo(text) {
    document.getElementById("statusInfo").textContent = text;
  },

  esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  },

  // ---------- 交互 ----------

  wireTable() {
    document.getElementById("tableBody").addEventListener("click", (e) => {
      const sel = e.target.closest("select");
      if (sel) return;
      const tr = e.target.closest("tr");
      if (!tr) return;
      this.selectedIdx = Number(tr.dataset.idx);
      this.renderRows();
    });
    document.getElementById("tableBody").addEventListener("dblclick", (e) => {
      if (e.target.closest("select")) return;
      const tr = e.target.closest("tr");
      if (!tr) return;
      this.selectedIdx = Number(tr.dataset.idx);
      this.setInfo("播放功能将在 P3 接入（当前为外部 mpv）");
    });
    document.getElementById("tableBody").addEventListener("change", (e) => {
      const sel = e.target;
      if (sel.tagName !== "SELECT") return;
      const idx = Number(sel.dataset.idx);
      if (sel.dataset.act === "quality") {
        window.pywebview.api.setQuality(idx, sel.value);
      } else if (sel.dataset.act === "plugin") {
        window.pywebview.api.setPlugin(idx, sel.value);
      }
      // 保存异步；立刻回到已保存配置，成功后由快照刷新。
      this.renderRows();
    });
    document.querySelectorAll("#streamTable th").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.key;
        if (this.sortKey === key) this.sortDesc = !this.sortDesc;
        else { this.sortKey = key; this.sortDesc = false; }
        this.persist();
        this.renderSortMarks();
        this.renderRows();
      });
    });
  },

  renderSortMarks() {
    document.querySelectorAll("#streamTable th").forEach((th) => {
      const base = th.textContent.replace(/\s*[▲▼]$/, "");
      if (th.dataset.key === this.sortKey && this.sortKey !== "default") {
        th.textContent = base + (this.sortDesc ? " ▼" : " ▲");
      } else {
        th.textContent = base;
      }
    });
  },

  wireControls() {
    document.getElementById("btnRefresh").addEventListener("click", () => {
      window.pywebview.api.refresh();
    });
    document.getElementById("searchBox").addEventListener("input", (e) => {
      this.search = e.target.value;
      this.renderRows();
    });
    document.getElementById("stateFilter").addEventListener("change", (e) => {
      this.stateFilter = e.target.value;
      this.persist();
      this.renderRows();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "F5") { e.preventDefault(); window.pywebview.api.refresh(); }
      const target = e.target;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "SELECT");
      if (!typing && (e.key === "r" || e.key === "R")) window.pywebview.api.refresh();
      if (!typing && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        e.preventDefault();
        this.moveSelection(e.key === "ArrowDown" ? 1 : -1);
      }
    });
  },

  moveSelection(offset) {
    const rows = this.visibleRows();
    if (!rows.length) return;
    let pos = rows.findIndex((r) => r.idx === this.selectedIdx);
    if (pos < 0) pos = 0;
    pos = Math.max(0, Math.min(rows.length - 1, pos + offset));
    this.selectedIdx = rows[pos].idx;
    this.renderRows();
    const tr = document.querySelector(`#tableBody tr[data-idx="${this.selectedIdx}"]`);
    if (tr) tr.scrollIntoView({ block: "nearest" });
  },

  wireWindowControls() {
    document.getElementById("btnMin").addEventListener("click", () => {
      window.pywebview.api.minimizeWindow();
    });
    document.getElementById("btnMax").addEventListener("click", async () => {
      this.maximized = !this.maximized;
      if (this.maximized) await window.pywebview.api.maximizeWindow();
      else await window.pywebview.api.restoreWindow();
    });
    document.getElementById("btnClose").addEventListener("click", () => {
      window.pywebview.api.hideToTray();
    });
    document.querySelectorAll(".menu-item").forEach((item) => {
      item.addEventListener("click", () => {
        document.querySelectorAll(".menu-item").forEach((i) => i.classList.remove("open"));
        item.classList.add("open");
        this.setInfo("菜单「" + item.textContent + "」将在后续阶段开放");
      });
    });
  },

  // ---------- 持久化与启动 ----------

  persist() {
    try {
      localStorage.setItem("zhibo.view", JSON.stringify({
        tag: this.tag,
        stateFilter: this.stateFilter,
        sortKey: this.sortKey,
        sortDesc: this.sortDesc,
      }));
    } catch (err) { /* localStorage 不可用时静默 */ }
  },

  restore() {
    try {
      const saved = JSON.parse(localStorage.getItem("zhibo.view") || "{}");
      if (saved.tag) this.tag = saved.tag;
      if (saved.stateFilter) this.stateFilter = saved.stateFilter;
      if (saved.sortKey) this.sortKey = saved.sortKey;
      if (typeof saved.sortDesc === "boolean") this.sortDesc = saved.sortDesc;
      const filter = document.getElementById("stateFilter");
      if ([...filter.options].some((o) => o.value === this.stateFilter)) {
        filter.value = this.stateFilter;
      }
    } catch (err) { /* 忽略损坏的本地存储 */ }
  },

  async boot() {
    this.wireWindowControls();
    this.wireControls();
    this.wireTable();
    this.restore();
    this.renderSortMarks();
    this.setInfo("等待监控核心…");
    const load = async () => {
      try {
        const snap = await window.pywebview.api.getSnapshot();
        if (snap && snap.rows) {
          this.snapshot = snap;
          this.renderTags();
          this.renderRows();
          this.renderStatus();
          this.setInfo("就绪");
        }
      } catch (err) { /* 桥未就绪时由事件推送补齐 */ }
    };
    window.addEventListener("pywebviewready", load);
    if (window.pywebview && window.pywebview.api) load();
  },
};

window.zhibo = zhibo;
document.addEventListener("DOMContentLoaded", () => zhibo.boot());
