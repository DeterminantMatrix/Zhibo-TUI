/* ZHIBO Web 前端逻辑
 * P1：表格 / 快照推送 / 标签筛选 / 搜索 / 排序 / 行内画质与插件下拉
 * P2：详情编辑 / 设置 / 代理 / 导入 事务对话框（表单 → 差异确认 → 保存）
 * P3：播放(外部 mpv)/停止/复制流/右键菜单/快捷键/▶播放标记
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
  playingIdx: -1,
  // 运行日志环形缓冲（P5）：最多保留 500 条，面板隐藏时也继续累积。
  logLines: [],
  logVisible: false,
  // 事务对话框状态机：kind + 当前舞台数据；formData 供确认页"返回"恢复表单。
  dialog: { kind: "", data: {}, formData: null, busy: false },

  DIALOG_TITLES: {
    edit: "详情与修改",
    settings: "监控设置",
    proxy: "平台代理",
    import: "导入直播间",
    delete: "删除直播间",
    update: "更新中心",
    download: "视频下载",
  },

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
          this.appendLogLine(ev.payload.text);
          this.setInfo(ev.payload.text);
          break;
        case "liveEvent":
          this.setInfo(
            (ev.payload.is_live ? "↑ " : "↓ ") + ev.payload.name +
            (ev.payload.is_live ? " 开播" : " 下播")
          );
          break;
        case "operationFinished":
          this.applyOperationResult(ev.payload);
          break;
        case "dialog":
          this.applyDialogData(ev.payload);
          break;
        case "playerState":
          this.playingIdx = ev.payload.playing ? ev.payload.idx : -1;
          this.renderRows();
          break;
        case "streamUrl":
          this.copyText(ev.payload.url);
          break;
        case "progress":
          if (this.dialog.kind === ev.payload.kind) {
            this.dialog.data = {
              ...this.dialog.data,
              stage: "progress",
              progress: ev.payload.value,
              progressText: ev.payload.text,
            };
            this.renderDialog();
          }
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
      const playing = row.idx === this.playingIdx;
      tr.className = (row.idx === this.selectedIdx ? "selected" : "") + (playing ? " playing" : "");

      const glyph = this.statusGlyph(row);
      const errCell = row.error && row.error !== "-" ? row.error : "";
      const cells = [
        `<td><span class="dot ${glyph.cls}">${glyph.text}</span>${playing ? '<span class="play-mark">▶</span>' : ""}</td>`,
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
    const notifyBtn = document.getElementById("btnNotify");
    if (notifyBtn) {
      notifyBtn.textContent = this.snapshot.notifications_enabled ? "通知：开" : "通知：关";
    }
  },

  setInfo(text) {
    document.getElementById("statusInfo").textContent = text;
  },

  // ---------- P5：运行日志面板 ----------

  LOG_LIMIT: 500,

  appendLogLine(text) {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    this.logLines.push({
      time: pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":" + pad(now.getSeconds()),
      text: String(text == null ? "" : text),
    });
    if (this.logLines.length > this.LOG_LIMIT) {
      this.logLines.splice(0, this.logLines.length - this.LOG_LIMIT);
    }
    if (this.logVisible) this.appendLogNode(this.logLines[this.logLines.length - 1]);
  },

  appendLogNode(line) {
    const box = document.getElementById("logLines");
    if (!box) return;
    const nearBottom =
      box.scrollTop + box.clientHeight >= box.scrollHeight - 24;
    const row = document.createElement("div");
    row.className = "log-line";
    const time = document.createElement("span");
    time.className = "log-time";
    time.textContent = line.time;
    row.appendChild(time);
    row.appendChild(document.createTextNode(line.text));
    box.appendChild(row);
    while (box.childElementCount > this.LOG_LIMIT) box.removeChild(box.firstChild);
    if (nearBottom) box.scrollTop = box.scrollHeight;
  },

  renderAllLogLines() {
    const box = document.getElementById("logLines");
    box.innerHTML = "";
    for (const line of this.logLines) this.appendLogNode(line);
  },

  toggleLog() {
    this.logVisible = !this.logVisible;
    document.getElementById("logPanel").hidden = !this.logVisible;
    if (this.logVisible) this.renderAllLogLines();
    try { localStorage.setItem("zhibo.logVisible", this.logVisible ? "1" : "0"); } catch (err) { /* 忽略 */ }
  },

  restoreLogVisibility() {
    let visible = false;
    try { visible = localStorage.getItem("zhibo.logVisible") === "1"; } catch (err) { /* 忽略 */ }
    this.logVisible = visible;
    document.getElementById("logPanel").hidden = !visible;
    if (visible) this.renderAllLogLines();
  },

  esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  },

  // ---------- P2：事务对话框 ----------

  h(tag, attrs = {}, text) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
      else if (value !== undefined && value !== null) node.setAttribute(key, value);
    }
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  },

  formRow(labelText, control) {
    const row = this.h("div", { class: "form-row" });
    row.appendChild(this.h("label", { class: "form-label" }, labelText));
    row.appendChild(control);
    return row;
  },

  buttonRow(defs) {
    const row = this.h("div", { class: "button-row" });
    for (const def of defs) {
      const btn = this.h("button", { onClick: def.onClick }, def.label);
      if (def.primary) btn.classList.add("dialog-primary");
      if (def.disabled) btn.disabled = true;
      row.appendChild(btn);
    }
    return row;
  },

  dialogError() {
    const err = this.dialog.data.error;
    return this.h("div", { class: "dialog-error" + (err ? " show" : "") }, err || "");
  },

  clearDialogError() {
    if (this.dialog.data && this.dialog.data.error) delete this.dialog.data.error;
  },

  applyDialogData(payload) {
    const kind = payload.kind;
    const incoming = payload.payload || {};
    const stage = String(incoming.stage || "form");
    // 关闭状态下只接受"打开型"事件；checked/progress 等续传直接丢弃，
    // 避免后台检查在用户关闭对话框后又把它弹回来。
    if (!this.dialog.kind && !["form", "confirm", "formats"].includes(stage)) return;
    if (kind === this.dialog.kind) {
      // 更新中心：单条检查结果按 target 打补丁回表单（与 Qt 状态机一致）。
      if (kind === "update" && stage === "checked") {
        const target = String(incoming.target || "");
        const patch = incoming.item || {};
        const items = (this.dialog.data.items || []).map((item) =>
          item.value === target ? { ...item, ...patch } : item
        );
        this.dialog.data = { ...this.dialog.data, stage: "form", target, items };
        this.renderDialog();
        return;
      }
      // 进度/完成态合并进现有数据。
      if (stage === "progress" || stage === "done") {
        this.dialog.data = { ...this.dialog.data, ...incoming };
        this.dialog.busy = false;
        this.renderDialog();
        return;
      }
    }
    // 表单 → 确认：记住表单舞台的完整数据，"返回"时原样恢复。
    if (
      kind === this.dialog.kind &&
      String(this.dialog.data.stage || "form") === "form" &&
      stage === "confirm"
    ) {
      this.dialog.formData = this.dialog.data;
    }
    this.dialog.kind = kind;
    this.dialog.data = incoming;
    this.dialog.busy = false;
    this.renderDialog();
  },

  applyOperationResult(payload) {
    if (payload.kind === this.dialog.kind) {
      this.dialog.busy = false;
      if (!payload.ok) {
        // 更新/下载失败切到 failed 舞台（进度区显示）；其余就地报错不丢表单。
        if (payload.kind === "update" || payload.kind === "download") {
          this.dialog.data = {
            ...this.dialog.data,
            stage: "failed",
            progressText: payload.message || "操作失败",
          };
          this.renderDialog();
        } else {
          this.dialog.data = { ...this.dialog.data, error: payload.message || "操作失败" };
          const errNode = document.querySelector("#dlgBody .dialog-error");
          if (errNode) {
            errNode.textContent = this.dialog.data.error;
            errNode.classList.add("show");
          } else {
            this.renderDialog();
          }
        }
      } else if (payload.payload && payload.payload.done) {
        const data = this.dialog.data;
        const merged = { ...data, stage: "done", progress: 100, progressText: payload.message };
        if (payload.kind === "update" && payload.payload.downloaded === true) {
          const target = String(payload.payload.target || data.target || "");
          merged.target = target;
          merged.items = (data.items || []).map((item) => {
            if (item.value !== target) return item;
            const next = { ...item };
            if (payload.payload.version) {
              next.version = String(payload.payload.version);
              next.installed = true;
            }
            next.lastUpdated = "刚刚";
            if (next.kind === "tool" || next.kind === "package") {
              Object.assign(next, {
                actionLabel: "检查更新",
                actionEnabled: true,
                actionKind: "check",
                updateStatus: "unchecked",
                updateHint: "更新完成；可按需再次检查",
                remoteVersion: "",
                downloadSize: "",
              });
            }
            return next;
          });
        }
        this.dialog.data = merged;
        this.renderDialog();
      } else if (payload.payload && payload.payload.close) {
        this.closeDialog();
      }
    }
    this.setInfo(payload.message || (payload.ok ? "操作完成" : "操作失败"));
  },

  closeDialog() {
    if (this.dialog.busy) return;
    this.dialog = { kind: "", data: {}, formData: null, busy: false };
    document.getElementById("dialogOverlay").hidden = true;
  },

  backToForm() {
    if (this.dialog.busy) return;
    if (!this.dialog.formData) {
      this.closeDialog();
      return;
    }
    this.dialog.data = this.dialog.formData;
    this.dialog.formData = null;
    this.renderDialog();
  },

  renderDialog() {
    const overlay = document.getElementById("dialogOverlay");
    if (!this.dialog.kind) {
      overlay.hidden = true;
      return;
    }
    overlay.hidden = false;
    const dialogWindow = overlay.querySelector(".dialog-window");
    dialogWindow.classList.toggle("wide", this.dialog.kind === "update" || this.dialog.kind === "download");
    document.getElementById("dlgTitle").textContent =
      this.DIALOG_TITLES[this.dialog.kind] || "对话框";
    const stage = String(this.dialog.data.stage || "form");
    const builders = {
      edit: () => (stage === "confirm" ? this.buildConfirmBody() : this.buildEditForm()),
      settings: () => (stage === "confirm" ? this.buildConfirmBody() : this.buildSettingsForm()),
      proxy: () => this.buildProxyForm(),
      import: () => (stage === "confirm" ? this.buildConfirmBody() : this.buildImportForm()),
      update: () => this.buildUpdateCenter(),
      download: () => this.buildDownload(),
    };
    const build = builders[this.dialog.kind];
    const body = document.getElementById("dlgBody");
    body.innerHTML = "";
    body.appendChild(build ? build() : document.createTextNode("未知对话框"));
  },

  collectFormValues() {
    const values = {};
    document.querySelectorAll("#dlgBody [data-field]").forEach((node) => {
      values[node.dataset.field] = node.value;
    });
    return values;
  },

  beginOp() {
    this.dialog.busy = true;
    this.clearDialogError();
    const errNode = document.querySelector("#dlgBody .dialog-error");
    if (errNode) {
      errNode.textContent = "";
      errNode.classList.remove("show");
    }
  },

  buildDetailBox() {
    const data = this.dialog.data;
    const box = this.h("fieldset", { class: "detail-box" });
    box.appendChild(this.h("legend", {}, data.detailTitle || "状态详情"));
    for (const row of data.detailRows || []) {
      const line = this.h("div", { class: "detail-line" });
      line.appendChild(this.h("span", { class: "detail-label" }, row.label));
      line.appendChild(
        this.h(
          "span",
          { class: "detail-value tone-" + (row.tone || "normal"), title: row.value },
          row.value
        )
      );
      box.appendChild(line);
    }
    return box;
  },

  buildEditForm() {
    const form = this.dialog.data.form || {};
    const frag = document.createDocumentFragment();
    const grid = this.h("div", { class: "edit-grid" });
    grid.appendChild(this.buildDetailBox());

    const box = this.h("fieldset", { class: "edit-box" });
    box.appendChild(this.h("legend", {}, "编辑配置"));
    const enabled = this.h("select", { "data-field": "enabled" });
    for (const [value, text] of [["true", "启用监控"], ["false", "停用监控"]]) {
      const opt = this.h("option", { value }, text);
      if (String(form.enabled) === value) opt.selected = true;
      enabled.appendChild(opt);
    }
    box.appendChild(this.formRow("启用", enabled));
    box.appendChild(this.formRow("名称",
      this.h("input", { type: "text", "data-field": "name", value: form.name || "" })));
    box.appendChild(this.formRow("标签",
      this.h("input", { type: "text", "data-field": "tags", value: form.tags || "", placeholder: "多个标签用 | 分隔" })));
    const plugins = this.dialog.data.pluginOptions || [];
    const pluginSel = this.h("select", { "data-field": "plugin" });
    const curPlugin = form.plugin || "";
    if (curPlugin && !plugins.includes(curPlugin)) {
      pluginSel.appendChild(this.h("option", { value: curPlugin }, curPlugin));
    }
    for (const name of plugins) {
      const opt = this.h("option", { value: name }, name);
      if (name.toLowerCase() === String(curPlugin).toLowerCase()) opt.selected = true;
      pluginSel.appendChild(opt);
    }
    box.appendChild(this.formRow("主插件", pluginSel));
    box.appendChild(this.formRow("备用插件",
      this.h("input", { type: "text", "data-field": "fallback_plugins", value: form.fallback_plugins || "", placeholder: "多个插件用 | 分隔" })));
    box.appendChild(this.formRow("平台",
      this.h("input", { type: "text", "data-field": "platform", value: form.platform || "" })));
    box.appendChild(this.formRow("直播间地址",
      this.h("input", { type: "text", "data-field": "url", value: form.url || "" })));
    const qualitySel = this.h("select", { "data-field": "quality" });
    const curQuality = form.quality || "best";
    let matched = false;
    for (const opt of this.dialog.data.qualityOptions || []) {
      const node = this.h("option", { value: opt.value }, opt.label);
      if (String(opt.value).toLowerCase() === String(curQuality).toLowerCase()) {
        node.selected = true;
        matched = true;
      }
      qualitySel.appendChild(node);
    }
    if (!matched) {
      qualitySel.insertBefore(this.h("option", { value: curQuality }, curQuality), qualitySel.firstChild);
    }
    box.appendChild(this.formRow("画质", qualitySel));
    box.appendChild(this.formRow("sport_id",
      this.h("input", { type: "text", "data-field": "sport_id", value: form.sport_id || "" })));
    box.appendChild(this.formRow("扩展字段",
      this.h("textarea", { "data-field": "extra", rows: "5", class: "mono-field", spellcheck: "false" }, form.extra || "{}")));
    grid.appendChild(box);
    frag.appendChild(grid);
    frag.appendChild(this.dialogError());
    frag.appendChild(this.buttonRow([
      { label: "保存修改", primary: true, onClick: () => this.submitEditPreview() },
      { label: "关闭", onClick: () => this.closeDialog() },
    ]));
    return frag;
  },

  submitEditPreview() {
    if (this.dialog.busy) return;
    const values = this.collectFormValues();
    this.beginOp();
    window.pywebview.api.previewEdit(this.dialog.data.idx, values);
  },

  buildSettingsForm() {
    const data = this.dialog.data;
    const frag = document.createDocumentFragment();
    const box = this.h("fieldset", {});
    box.appendChild(this.h("legend", {}, "监控参数"));
    const numberField = (field, label) => {
      const input = this.h("input", {
        type: "number", min: "1", "data-field": field, value: data[field] != null ? data[field] : "",
      });
      return this.formRow(label, input);
    };
    box.appendChild(numberField("poll_interval", "轮询间隔（秒）"));
    box.appendChild(numberField("max_concurrent_checks", "最大并发检测"));
    box.appendChild(numberField("failure_backoff_after", "失败后退避阈值"));
    box.appendChild(numberField("failure_backoff_polls", "退避轮数"));
    const notif = this.h("select", { "data-field": "notifications_enabled" });
    for (const [value, text] of [["true", "开启"], ["false", "关闭"]]) {
      const opt = this.h("option", { value }, text);
      if (String(data.notifications_enabled) === value) opt.selected = true;
      notif.appendChild(opt);
    }
    box.appendChild(this.formRow("桌面通知", notif));
    frag.appendChild(box);
    frag.appendChild(this.dialogError());
    frag.appendChild(this.buttonRow([
      { label: "保存修改", primary: true, onClick: () => this.submitSettings() },
      { label: "关闭", onClick: () => this.closeDialog() },
    ]));
    return frag;
  },

  submitSettings() {
    if (this.dialog.busy) return;
    const values = this.collectFormValues();
    this.beginOp();
    window.pywebview.api.previewSettings(values);
  },

  buildProxyForm() {
    const data = this.dialog.data;
    const frag = document.createDocumentFragment();
    const box = this.h("fieldset", {});
    box.appendChild(this.h("legend", {}, "按平台配置代理（留空 = 默认规则）"));
    const skip = new Set(["stage", "health", "healthSummary", "healthOk", "error"]);
    for (const key of Object.keys(data)) {
      if (skip.has(key)) continue;
      box.appendChild(this.formRow(key,
        this.h("input", { type: "text", "data-field": key, value: data[key] || "", placeholder: "主机:端口 或完整代理 URL" })));
    }
    frag.appendChild(box);
    const health = data.health || [];
    if (health.length || data.healthSummary) {
      const healthBox = this.h("fieldset", { class: "proxy-health" });
      healthBox.appendChild(this.h("legend", {}, "连通性测试"));
      for (const item of health) {
        const tone = item.status === "ok" ? "ok" : item.status === "error" ? "error" : "muted";
        const line = this.h("div", { class: "detail-line" });
        line.appendChild(this.h("span", { class: "detail-label" }, item.platform));
        line.appendChild(
          this.h("span", { class: "detail-value tone-" + tone, title: item.detail },
            item.label + " — " + item.detail)
        );
        healthBox.appendChild(line);
      }
      if (data.healthSummary) {
        const line = this.h("div", { class: "detail-line" });
        line.appendChild(this.h("span", {
          class: "detail-value tone-" + (data.healthOk ? "ok" : "error"),
        }, data.healthSummary));
        healthBox.appendChild(line);
      }
      frag.appendChild(healthBox);
    }
    frag.appendChild(this.dialogError());
    frag.appendChild(this.buttonRow([
      { label: "测试连接", onClick: () => this.submitProxyTest() },
      { label: "保存", primary: true, onClick: () => this.submitProxySave() },
      { label: "关闭", onClick: () => this.closeDialog() },
    ]));
    return frag;
  },

  submitProxyTest() {
    if (this.dialog.busy) return;
    const values = this.collectFormValues();
    this.beginOp();
    window.pywebview.api.testProxy(values);
  },

  submitProxySave() {
    if (this.dialog.busy) return;
    const values = this.collectFormValues();
    this.beginOp();
    window.pywebview.api.saveProxy(values);
  },

  buildImportForm() {
    const data = this.dialog.data;
    const frag = document.createDocumentFragment();
    const box = this.h("fieldset", {});
    box.appendChild(this.h("legend", {}, "导入直播间"));
    box.appendChild(this.formRow("直播间地址",
      this.h("input", { type: "text", "data-field": "url", value: data.url || "", placeholder: "粘贴平台直播间链接" })));
    box.appendChild(this.formRow("标签",
      this.h("input", { type: "text", "data-field": "tag", value: data.tag || "未分类" })));
    frag.appendChild(box);
    frag.appendChild(this.dialogError());
    frag.appendChild(this.buttonRow([
      { label: "获取预览", primary: true, onClick: () => this.submitImportPreview() },
      { label: "关闭", onClick: () => this.closeDialog() },
    ]));
    return frag;
  },

  submitImportPreview() {
    if (this.dialog.busy) return;
    const values = this.collectFormValues();
    this.beginOp();
    window.pywebview.api.previewImport(values.url || "", values.tag || "未分类");
  },

  buildConfirmBody() {
    const data = this.dialog.data;
    const frag = document.createDocumentFragment();
    frag.appendChild(this.h("pre", { class: "preview-text" }, data.previewText || ""));
    frag.appendChild(this.dialogError());
    const buttons = [];
    if (this.dialog.kind === "import") {
      buttons.push({
        label: data.confirmLabel || "确认导入",
        primary: true,
        disabled: !data.canConfirm,
        onClick: () => this.confirmDialog(),
      });
    } else {
      buttons.push({
        label: data.confirmLabel || "确认保存",
        primary: true,
        onClick: () => this.confirmDialog(),
      });
    }
    if (this.dialog.formData) {
      // 有来源表单才有"返回"；删除等纯确认对话框直接给出关闭。
      buttons.push({ label: "返回", onClick: () => this.backToForm() });
    }
    buttons.push({ label: "关闭", onClick: () => this.closeDialog() });
    frag.appendChild(this.buttonRow(buttons));
    return frag;
  },

  confirmDialog() {
    if (this.dialog.busy) return;
    this.beginOp();
    window.pywebview.api.confirmDialog(this.dialog.kind);
  },

  openImportDialog() {
    this.dialog = {
      kind: "import",
      data: { stage: "form", url: "", tag: "未分类" },
      formData: null,
      busy: false,
    };
    this.renderDialog();
  },

  // ---------- P4：更新中心与下载 ----------

  updateStatusGlyph(item) {
    switch (item.updateStatus) {
      case "checking": return "…";
      case "current": return "✓";
      case "install":
      case "update": return "↑";
      case "unknown":
      case "failed": return "!";
      default: return "";
    }
  },

  buildUpdateCenter() {
    const data = this.dialog.data;
    if (["progress", "done", "failed"].includes(String(data.stage || "form"))) {
      return this.buildProgressPane();
    }
    const frag = document.createDocumentFragment();
    const items = data.items || [];
    const selected = items.find((item) => item.value === data.target) || items[0] || null;
    const grid = this.h("div", { class: "update-grid" });

    const list = this.h("div", { class: "update-list" });
    for (const item of items) {
      const isActive = selected && item.value === selected.value;
      const row = this.h("button", {
        class: "update-item" + (isActive ? " active" : ""),
        onClick: () => {
          this.dialog.data = { ...this.dialog.data, target: item.value };
          this.renderDialog();
        },
      });
      const head = this.h("div", { class: "update-item-head" });
      head.appendChild(this.h("span", { class: "update-item-label" }, item.label));
      head.appendChild(this.h("span", {
        class: "update-item-status st-" + (item.updateStatus || "unchecked"),
      }, this.updateStatusGlyph(item)));
      row.appendChild(head);
      row.appendChild(this.h("div", { class: "update-item-hint" },
        (item.version || "-") + (item.updateHint ? " · " + item.updateHint : "")));
      list.appendChild(row);
    }
    grid.appendChild(list);

    const detail = this.h("div", { class: "update-detail" });
    if (selected) {
      const box = this.h("fieldset", { class: "update-detail-box" });
      box.appendChild(this.h("legend", {}, selected.label));
      box.appendChild(this.h("div", { class: "update-desc" }, selected.description || ""));
      const metaRows = [
        ["当前版本", selected.version || "-"],
        ["来源", selected.source || "-"],
        ["上次更新", selected.lastUpdated || "-"],
      ];
      for (const [label, value] of metaRows) {
        const line = this.h("div", { class: "detail-line" });
        line.appendChild(this.h("span", { class: "detail-label" }, label));
        line.appendChild(this.h("span", { class: "detail-value" }, value));
        box.appendChild(line);
      }
      if (selected.updateHint) {
        const line = this.h("div", { class: "detail-line" });
        line.appendChild(this.h("span", { class: "detail-label" }, "状态"));
        line.appendChild(this.h("span", {
          class: "detail-value tone-" +
            (selected.updateStatus === "unknown" ? "error"
              : selected.updateStatus === "current" || selected.updateStatus === "checking" ? "ok" : "normal"),
        }, selected.updateHint));
        box.appendChild(line);
      }
      if (selected.value === "fs1" || selected.value === "bilibili_cookie") {
        box.appendChild(this.h("div", { class: "update-content-hint" },
          selected.value === "fs1"
            ? "粘贴 FS /v1/room curl，或 Tampermonkey 导出的 zhibo.fs1-auth JSON。"
            : "粘贴本人登录 B站后导出的 Netscape cookies.txt；请勿粘贴请求头或他人凭据。"));
        box.appendChild(this.h("textarea", {
          "data-field": "updateContent",
          rows: "5",
          class: "mono-field",
          spellcheck: "false",
          placeholder: selected.value === "fs1" ? "curl 或 FS1 授权 JSON …" : "# Netscape HTTP Cookie File…",
        }, ""));
      }
      detail.appendChild(box);

      const btnRow = this.h("div", { class: "button-row" });
      const actionBtn = this.h("button", {
        class: "dialog-primary",
        onClick: () => this.submitUpdateAction(selected),
      }, (selected.actionLabel || "操作") + " " + selected.label);
      if (!selected.actionEnabled) actionBtn.disabled = true;
      btnRow.appendChild(actionBtn);
      detail.appendChild(btnRow);
      if (selected.kind === "tool") {
        detail.appendChild(this.h("div", { class: "update-footnote" },
          "仅在检查判定需要安装、更新或迁移时下载；安装前校验版本、体积和 SHA-256"));
      } else if (selected.kind === "package") {
        detail.appendChild(this.h("div", { class: "update-footnote" },
          "版本来自 PyPI；执行前会再次比较，不会盲目运行 pip 更新"));
      } else if (selected.value === "bilibili_cookie") {
        detail.appendChild(this.h("div", { class: "update-footnote" },
          "Cookie 内容不会进入日志或更新记录"));
      }
    }
    grid.appendChild(detail);
    frag.appendChild(grid);
    frag.appendChild(this.buttonRow([
      { label: "关闭", onClick: () => this.closeDialog() },
    ]));
    return frag;
  },

  submitUpdateAction(item) {
    if (item.actionKind === "check" || item.actionKind === "recheck") {
      window.pywebview.api.checkUpdate(item.value);
      return;
    }
    const contentNode = document.querySelector('#dlgBody [data-field="updateContent"]');
    const content = contentNode ? contentNode.value : "";
    window.pywebview.api.runUpdate(item.value, content);
    if (item.value === "bilibili_cookie" && contentNode) contentNode.value = "";
  },

  buildProgressPane() {
    const data = this.dialog.data;
    const stage = String(data.stage || "progress");
    const frag = document.createDocumentFragment();
    const box = this.h("div", {
      class: "progress-pane" + (stage === "failed" ? " prog-failed" : stage === "done" ? " prog-done" : ""),
    });
    box.appendChild(this.h("div", { class: "progress-title" },
      stage === "failed" ? "更新失败" : stage === "done" ? "更新完成" : "正在更新"));
    box.appendChild(this.h("div", { class: "progress-text" }, data.progressText || "正在处理…"));
    const pct = typeof data.progress === "number" ? data.progress : -1;
    box.appendChild(this.buildSegProgress(pct));
    const row = this.h("div", { class: "progress-meta" });
    row.appendChild(this.h("span", { class: "progress-pct" }, pct >= 0 ? Math.round(pct) + "%" : ""));
    row.appendChild(this.h("span", { class: "progress-flex" }));
    if (stage === "done" || stage === "failed") {
      row.appendChild(this.h("button", { onClick: () => this.continueFromProgress() },
        this.dialog.kind === "download" ? "返回" : "继续管理"));
    }
    row.appendChild(this.h("button", { onClick: () => this.closeDialog() }, "关闭"));
    box.appendChild(row);
    frag.appendChild(box);
    return frag;
  },

  continueFromProgress() {
    if (this.dialog.kind === "download") {
      this.dialog.data = { ...this.dialog.data, stage: "form" };
    } else {
      this.dialog.data = { ...this.dialog.data, stage: "form" };
    }
    this.renderDialog();
  },

  // 98.css 风格分段蓝块进度条；pct<0 为流动的不确定态。
  buildSegProgress(pct) {
    const bar = this.h("div", { class: "seg-progress" + (pct < 0 ? " indeterminate" : "") });
    const blocks = 24;
    const lit = pct < 0 ? 0 : Math.max(0, Math.min(blocks, Math.round((pct / 100) * blocks)));
    for (let i = 0; i < blocks; i++) {
      const seg = document.createElement("span");
      if (pct < 0) {
        seg.style.animationDelay = (i * 0.08) + "s";
      } else if (i >= lit) {
        seg.classList.add("off");
      }
      bar.appendChild(seg);
    }
    return bar;
  },

  openDownloadDialog() {
    this.dialog = {
      kind: "download",
      data: { stage: "form", url: "" },
      formData: null,
      busy: false,
    };
    this.renderDialog();
  },

  buildDownload() {
    const data = this.dialog.data;
    const stage = String(data.stage || "form");
    if (["progress", "done", "failed"].includes(stage)) {
      return this.buildProgressPane();
    }
    const frag = document.createDocumentFragment();
    if (stage === "formats") {
      const box = this.h("fieldset", {});
      box.appendChild(this.h("legend", {}, "选择下载格式"));
      (data.formats || []).forEach((fmt, i) => {
        const line = this.h("label", { class: "download-option" });
        const radio = this.h("input", {
          type: "radio", name: "dlfmt", value: String(fmt.index),
        });
        if (i === 0) radio.checked = true;
        line.appendChild(radio);
        line.appendChild(this.h("span", {},
          (fmt.hasAudio ? "♪ " : "视频 ") + fmt.label + "  [" + fmt.formatId + "]"));
        box.appendChild(line);
      });
      frag.appendChild(box);
      frag.appendChild(this.dialogError());
      frag.appendChild(this.buttonRow([
        { label: "开始下载", primary: true, onClick: () => this.submitDownload() },
        { label: "关闭", onClick: () => this.closeDialog() },
      ]));
      return frag;
    }
    const box = this.h("fieldset", {});
    box.appendChild(this.h("legend", {}, "视频下载"));
    box.appendChild(this.formRow("视频地址",
      this.h("input", {
        type: "text", "data-field": "url", value: data.url || "",
        placeholder: "YouTube 等视频页面链接",
      })));
    frag.appendChild(box);
    frag.appendChild(this.dialogError());
    frag.appendChild(this.buttonRow([
      { label: "获取格式列表", primary: true, onClick: () => this.submitDownloadFormats() },
      { label: "关闭", onClick: () => this.closeDialog() },
    ]));
    return frag;
  },

  submitDownloadFormats() {
    if (this.dialog.busy) return;
    const values = this.collectFormValues();
    if (!values.url || !String(values.url).trim()) {
      this.setInfo("请先粘贴视频地址");
      return;
    }
    this.beginOp();
    window.pywebview.api.listDownloadFormats(values.url.trim());
  },

  submitDownload() {
    if (this.dialog.busy) return;
    const checked = document.querySelector('#dlgBody input[name="dlfmt"]:checked');
    if (!checked) {
      this.setInfo("请选择一个下载格式");
      return;
    }
    this.beginOp();
    window.pywebview.api.startDownload(Number(checked.value));
  },

  // ---------- 交互 ----------

  // ---------- P3：播放与右键菜单 ----------

  playRow(idx) {
    this.selectedIdx = idx;
    this.renderRows();
    this.setInfo("正在获取直播流…");
    window.pywebview.api.play(idx);
  },

  copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        () => this.setInfo("直播流地址已复制"),
        () => this.copyTextFallback(text)
      );
    } else {
      this.copyTextFallback(text);
    }
  },

  copyTextFallback(text) {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try {
      if (document.execCommand("copy")) this.setInfo("直播流地址已复制");
      else this.setInfo("复制失败：剪贴板不可用");
    } catch (err) {
      this.setInfo("复制失败：剪贴板不可用");
    }
    area.remove();
  },

  openContextMenu(idx, x, y) {
    this.selectedIdx = idx;
    this.renderRows();
    const row = (this.snapshot.rows || []).find((r) => r.idx === idx);
    const menu = document.getElementById("ctxMenu");
    menu.innerHTML = "";
    const items = [
      { label: "详情与修改", action: () => window.pywebview.api.loadDetails(idx) },
      { label: "▶ 播放", action: () => this.playRow(idx) },
      { label: "■ 停止播放", action: () => window.pywebview.api.stopPlayer() },
      { sep: true },
      { label: "复制流地址", action: () => window.pywebview.api.copyStream(idx) },
      { label: "打开直播间网页", action: () => window.pywebview.api.openWeb(idx) },
      { sep: true },
      {
        label: row && row.enabled ? "停用监控" : "恢复监控",
        action: () => window.pywebview.api.toggleEnabled(idx),
      },
      { label: "删除…", action: () => window.pywebview.api.previewDelete(idx), danger: true },
    ];
    for (const item of items) {
      if (item.sep) {
        menu.appendChild(this.h("div", { class: "ctx-sep" }));
        continue;
      }
      menu.appendChild(this.h(
        "button",
        {
          class: "ctx-item" + (item.danger ? " ctx-danger" : ""),
          onClick: () => {
            this.closeContextMenu();
            item.action();
          },
        },
        item.label
      ));
    }
    menu.hidden = false;
    const rect = menu.getBoundingClientRect();
    menu.style.left = Math.max(2, Math.min(x, window.innerWidth - rect.width - 4)) + "px";
    menu.style.top = Math.max(2, Math.min(y, window.innerHeight - rect.height - 4)) + "px";
  },

  closeContextMenu() {
    document.getElementById("ctxMenu").hidden = true;
  },

  wireTable() {
    document.getElementById("tableBody").addEventListener("click", (e) => {
      const sel = e.target.closest("select");
      if (sel) return;
      const tr = e.target.closest("tr");
      if (!tr) return;
      this.selectedIdx = Number(tr.dataset.idx);
      this.renderRows();
    });
    // 双击 = 主操作（播放），与 Qt 版一致；画质/插件列排除避免抢下拉事件。
    document.getElementById("tableBody").addEventListener("dblclick", (e) => {
      if (e.target.closest("select")) return;
      const tr = e.target.closest("tr");
      if (!tr) return;
      this.playRow(Number(tr.dataset.idx));
    });
    document.getElementById("tableBody").addEventListener("contextmenu", (e) => {
      if (e.target.closest("select")) return;
      const tr = e.target.closest("tr");
      if (!tr) return;
      e.preventDefault();
      this.openContextMenu(Number(tr.dataset.idx), e.clientX, e.clientY);
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
    document.getElementById("btnDetails").addEventListener("click", () => {
      if (this.dialog.busy) return;
      if (this.selectedIdx < 0) {
        this.setInfo("请先在表格中选择一个直播间");
        return;
      }
      window.pywebview.api.loadDetails(this.selectedIdx);
    });
    document.getElementById("btnPlay").addEventListener("click", () => {
      if (this.selectedIdx < 0) {
        this.setInfo("请先在表格中选择一个直播间");
        return;
      }
      this.playRow(this.selectedIdx);
    });
    document.getElementById("btnStop").addEventListener("click", () => {
      window.pywebview.api.stopPlayer();
    });
    document.getElementById("btnNotify").addEventListener("click", () => {
      window.pywebview.api.toggleNotifications();
    });
    document.getElementById("btnLog").addEventListener("click", () => this.toggleLog());
    // 点击任意处关闭右键菜单（菜单项自身的事件先于 document 处理）。
    document.addEventListener("click", (e) => {
      if (!e.target.closest("#ctxMenu")) this.closeContextMenu();
    });
    document.getElementById("btnImport").addEventListener("click", () => {
      if (this.dialog.busy) return;
      this.openImportDialog();
    });
    document.getElementById("btnUpdate").addEventListener("click", () => {
      if (this.dialog.busy) return;
      window.pywebview.api.loadUpdateCenter();
    });
    document.getElementById("btnDownload").addEventListener("click", () => {
      if (this.dialog.busy) return;
      this.openDownloadDialog();
    });
    document.getElementById("btnProxy").addEventListener("click", () => {
      if (this.dialog.busy) return;
      window.pywebview.api.loadProxy();
    });
    document.getElementById("btnSettings").addEventListener("click", () => {
      if (this.dialog.busy) return;
      window.pywebview.api.loadSettings();
    });
    document.getElementById("dlgClose").addEventListener("click", () => this.closeDialog());
    const themeBtn = document.getElementById("btnTheme");
    themeBtn.addEventListener("click", () => {
      this.setTheme(document.body.classList.contains("theme-dark") ? "classic" : "dark");
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
      if (e.key === "Escape") {
        this.closeContextMenu();
        if (this.dialog.kind) { e.preventDefault(); this.closeDialog(); }
      }
      if (e.ctrlKey && !e.shiftKey && !e.altKey) {
        const key = (e.key || "").toLowerCase();
        if (key === "p") { e.preventDefault(); if (this.selectedIdx >= 0) this.playRow(this.selectedIdx); return; }
        if (key === "x") { e.preventDefault(); window.pywebview.api.stopPlayer(); return; }
        if (key === "m") { e.preventDefault(); window.pywebview.api.playerControl("toggle_mute"); return; }
        if (e.key === "ArrowUp") { e.preventDefault(); window.pywebview.api.playerControl("volume_up"); return; }
        if (e.key === "ArrowDown") { e.preventDefault(); window.pywebview.api.playerControl("volume_down"); return; }
      }
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

  setTheme(theme) {
    const dark = theme === "dark";
    document.body.classList.toggle("theme-dark", dark);
    document.getElementById("btnTheme").textContent = dark ? "经典模式" : "暗色模式";
    try { localStorage.setItem("zhibo.theme", theme); } catch (err) { /* 忽略 */ }
  },

  restoreTheme() {
    let theme = "classic";
    try { theme = localStorage.getItem("zhibo.theme") || "classic"; } catch (err) { /* 忽略 */ }
    this.setTheme(theme);
  },

  async boot() {
    this.wireWindowControls();
    this.wireControls();
    this.wireTable();
    this.restoreTheme();
    this.restoreLogVisibility();
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
