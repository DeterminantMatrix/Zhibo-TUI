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
  // 事务对话框状态机：kind + 当前舞台数据；formData 供确认页"返回"恢复表单。
  dialog: { kind: "", data: {}, formData: null, busy: false },

  DIALOG_TITLES: {
    edit: "详情与修改",
    settings: "监控设置",
    proxy: "平台代理",
    import: "导入直播间",
    delete: "删除直播间",
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
    // 表单 → 确认：记住表单舞台的完整数据，"返回"时原样恢复。
    if (
      kind === this.dialog.kind &&
      String(this.dialog.data.stage || "form") === "form" &&
      String(incoming.stage) === "confirm"
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
        // 失败留在当前舞台；就地显示错误，避免整表单重渲染丢掉已输入内容。
        this.dialog.data = { ...this.dialog.data, error: payload.message || "操作失败" };
        const errNode = document.querySelector("#dlgBody .dialog-error");
        if (errNode) {
          errNode.textContent = this.dialog.data.error;
          errNode.classList.add("show");
        } else {
          this.renderDialog();
        }
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
    document.getElementById("dlgTitle").textContent =
      this.DIALOG_TITLES[this.dialog.kind] || "对话框";
    const stage = String(this.dialog.data.stage || "form");
    const builders = {
      edit: () => (stage === "confirm" ? this.buildConfirmBody() : this.buildEditForm()),
      settings: () => (stage === "confirm" ? this.buildConfirmBody() : this.buildSettingsForm()),
      proxy: () => this.buildProxyForm(),
      import: () => (stage === "confirm" ? this.buildConfirmBody() : this.buildImportForm()),
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
    // 点击任意处关闭右键菜单（菜单项自身的事件先于 document 处理）。
    document.addEventListener("click", (e) => {
      if (!e.target.closest("#ctxMenu")) this.closeContextMenu();
    });
    document.getElementById("btnImport").addEventListener("click", () => {
      if (this.dialog.busy) return;
      this.openImportDialog();
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
