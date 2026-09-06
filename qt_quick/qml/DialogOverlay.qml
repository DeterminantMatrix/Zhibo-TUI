import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: overlay
    visible: controller.dialogKind !== ""
    property bool updateProgressVisible: controller.dialogKind === "update"
                                         && (controller.dialogStage === "progress"
                                             || controller.dialogStage === "done"
                                             || controller.dialogStage === "failed")

    readonly property var theme: controller.themePalette
    property color bg0: theme.bg0
    property color bg1: theme.bg1
    property color bg2: theme.bg2
    property color bg3: theme.bg3
    property color line: theme.line
    property color lineBright: theme.lineBright
    property color textMain: theme.textMain
    property color textMuted: theme.textMuted
    property color accent: theme.accent
    property color green: theme.green
    property color red: theme.red

    function titleFor(kind) {
        const titles = {
            edit: "EDIT FOLLOWER / 编辑关注项",
            delete: "DELETE FOLLOWER / 删除直播间",
            settings: "MONITOR SETTINGS / 监控设置",
            import: "IMPORT FOLLOWER / 导入直播间",
            proxy: "PLATFORM PROXY / 平台代理",
            update: "UPDATE CENTER / 更新中心",
            download: "VIDEO DOWNLOAD / 视频下载"
        }
        return titles[kind] || "OPERATION"
    }

    function componentFor(kind, stage) {
        if (stage === "loading") return loadingComponent
        if (kind === "update") return updateComponent
        if (stage === "confirm") return confirmComponent
        if (stage === "progress" || stage === "done") return progressComponent
        if (kind === "edit") return editComponent
        if (kind === "settings") return settingsComponent
        if (kind === "import") return importComponent
        if (kind === "proxy") return proxyComponent
        if (kind === "download") return downloadComponent
        return loadingComponent
    }

    function panelHeightFor(kind, stage) {
        if (kind === "edit" || kind === "update") return 760
        if (kind === "delete") return 410
        if (stage === "confirm" || stage === "progress" || stage === "done") return 620
        if (kind === "import") return 430
        if (kind === "settings") return 520
        if (kind === "proxy") return (controller.dialogData.health || []).length > 0 ? 620 : 540
        if (kind === "download") return 540
        return 620
    }

    Rectangle {
        objectName: "dialogBackdrop"
        anchors.fill: parent
        color: overlay.theme.backdrop
        visible: true
        MouseArea { anchors.fill: parent; enabled: parent.visible }
    }

    Rectangle {
        id: panel
        objectName: "dialogPanel"
        anchors.centerIn: parent
        width: Math.min(parent.width - 48, (controller.dialogKind === "edit" || controller.dialogKind === "update") ? 900 : 760)
        height: Math.min(parent.height - 42, overlay.panelHeightFor(controller.dialogKind, controller.dialogStage || "form"))
        color: overlay.bg1
        border.color: overlay.lineBright
        border.width: 1
        radius: 10

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 18
            spacing: 12

            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: overlay.titleFor(controller.dialogKind)
                    color: overlay.accent
                    font.pixelSize: 17
                    font.weight: Font.Bold
                    Layout.fillWidth: true
                }
                TuiButton {
                    text: "关闭 [Esc]"
                    enabled: !controller.dialogBusy
                    onClicked: controller.closeDialog()
                }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: overlay.line }

            Loader {
                id: contentLoader
                Layout.fillWidth: true
                Layout.fillHeight: true
                sourceComponent: overlay.componentFor(controller.dialogKind, controller.dialogStage || "form")
            }

            Text {
                Layout.fillWidth: true
                visible: controller.dialogError !== ""
                text: controller.dialogError
                color: overlay.red
                font.pixelSize: 12
                wrapMode: Text.Wrap
            }

            ProgressBar {
                Layout.fillWidth: true
                visible: controller.dialogBusy && !overlay.updateProgressVisible
                indeterminate: true
            }
        }
    }

    component TuiButton: Button {
        id: button
        property bool primary: false
        implicitHeight: 38
        leftPadding: 14
        rightPadding: 14
        contentItem: Text {
            text: button.text
            color: !button.enabled ? overlay.theme.disabledText
                   : button.primary ? overlay.theme.primaryText
                   : button.down ? overlay.theme.pressedText : overlay.textMain
            font.pixelSize: 12
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            color: button.primary
                   ? (button.down ? overlay.theme.primaryPressed : button.hovered ? overlay.theme.primaryHover : overlay.accent)
                   : button.down ? overlay.theme.buttonPressed : button.hovered ? overlay.theme.buttonHover : overlay.bg2
            border.color: button.primary ? overlay.accent
                         : button.activeFocus ? overlay.accent : button.hovered ? overlay.lineBright : overlay.line
            radius: 7
        }
    }

    component TuiField: RowLayout {
        id: field
        property string label: ""
        property string placeholder: ""
        property alias text: input.text
        Layout.fillWidth: true
        spacing: 10
        Text {
            text: field.label
            color: overlay.textMuted
            font.pixelSize: 12
            Layout.preferredWidth: 125
        }
        TextField {
            id: input
            Layout.fillWidth: true
            Layout.preferredHeight: 38
            placeholderText: field.placeholder
            color: overlay.textMain
            selectByMouse: true
            background: Rectangle {
                color: overlay.bg0
                border.color: input.activeFocus ? overlay.accent : overlay.line
                radius: 7
            }
        }
    }

    component TuiSwitchField: RowLayout {
        id: switchField
        property string label: ""
        property alias checked: toggle.checked
        Layout.fillWidth: true
        spacing: 10
        Text {
            text: switchField.label
            color: overlay.textMuted
            font.pixelSize: 12
            Layout.preferredWidth: 125
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 42
            color: overlay.bg0
            border.color: toggle.activeFocus ? overlay.accent : overlay.line
            radius: 7
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 8
                Text {
                    text: toggle.checked ? "已开启" : "已关闭"
                    color: toggle.checked ? overlay.accent : overlay.textMuted
                    font.pixelSize: 12
                    Layout.fillWidth: true
                }
                Switch { id: toggle }
            }
        }
    }

    Component {
        id: loadingComponent
        Item {
            BusyIndicator { anchors.centerIn: parent; running: true }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: parent.verticalCenter
                anchors.topMargin: 48
                text: "正在读取数据…"
                color: overlay.textMuted
            }
        }
    }

    Component {
        id: confirmComponent
        ColumnLayout {
            spacing: 12
            TextArea {
                Layout.fillWidth: true
                Layout.fillHeight: true
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.Wrap
                text: controller.dialogData.previewText || ""
                color: overlay.textMain
                font.pixelSize: 13
                background: Rectangle { color: overlay.bg0; border.color: overlay.line; radius: 7 }
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                TuiButton {
                    primary: true
                    text: controller.dialogData.confirmLabel || "确认"
                    visible: controller.dialogData.canConfirm === undefined || controller.dialogData.canConfirm
                    enabled: !controller.dialogBusy
                    onClicked: controller.confirmDialog()
                }
                TuiButton { text: "返回"; enabled: !controller.dialogBusy; onClicked: controller.backToForm() }
            }
        }
    }

    Component {
        id: editComponent
        ColumnLayout {
            spacing: 9
            Text {
                text: "保存前会显示脱敏差异；Cookie、令牌和授权头不能写入关注配置。"
                color: overlay.textMuted
                font.pixelSize: 12
                Layout.fillWidth: true
                wrapMode: Text.Wrap
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ColumnLayout {
                    width: contentLoader.width - 28
                    spacing: 7
                    TuiSwitchField { id: editEnabled; label: "启用"; checked: String(controller.dialogData.enabled || "true").toLowerCase() === "true" }
                    TuiField { id: editName; label: "名称"; text: controller.dialogData.name || "" }
                    TuiField { id: editTags; label: "标签"; placeholder: "用 | 或逗号分隔"; text: controller.dialogData.tags || "" }
                    TuiField { id: editPlugin; label: "主插件"; text: controller.dialogData.plugin || "" }
                    TuiField { id: editFallbacks; label: "备用插件"; placeholder: "用 | 分隔"; text: controller.dialogData.fallback_plugins || "" }
                    TuiField { id: editPlatform; label: "平台"; text: controller.dialogData.platform || "" }
                    TuiField { id: editUrl; label: "直播间地址"; text: controller.dialogData.url || "" }
                    TuiField { id: editQuality; label: "画质"; text: controller.dialogData.quality || "best" }
                    TuiField { id: editSport; label: "sport_id"; text: controller.dialogData.sport_id || "" }
                    Text { text: "扩展字段（JSON 对象）"; color: overlay.textMuted; font.pixelSize: 12 }
                    TextArea {
                        id: editExtra
                        Layout.fillWidth: true
                        Layout.preferredHeight: 130
                        text: controller.dialogData.extra || "{}"
                        color: overlay.textMain
                        selectByMouse: true
                        wrapMode: TextEdit.WrapAnywhere
                        background: Rectangle { color: overlay.bg0; border.color: editExtra.activeFocus ? overlay.accent : overlay.line; radius: 7 }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                TuiButton {
                    primary: true
                    text: "生成修改预览"
                    enabled: !controller.dialogBusy
                    onClicked: controller.submitEdit({
                        enabled: editEnabled.checked ? "true" : "false",
                        name: editName.text,
                        tags: editTags.text,
                        plugin: editPlugin.text,
                        fallback_plugins: editFallbacks.text,
                        platform: editPlatform.text,
                        url: editUrl.text,
                        quality: editQuality.text,
                        sport_id: editSport.text,
                        extra: editExtra.text
                    })
                }
            }
        }
    }

    Component {
        id: settingsComponent
        ColumnLayout {
            spacing: 10
            Text { text: "范围外的值不会被静默修正；确认预览后才会保存。"; color: overlay.textMuted; wrapMode: Text.Wrap; Layout.fillWidth: true }
            TuiField { id: pollInterval; label: "轮询间隔（秒）"; placeholder: "5–3600"; text: controller.dialogData.poll_interval || "60" }
            TuiField { id: maxConcurrent; label: "最大并发检测"; placeholder: "1–16"; text: controller.dialogData.max_concurrent_checks || "8" }
            TuiField { id: backoffAfter; label: "失败后退避阈值"; placeholder: "1–20"; text: controller.dialogData.failure_backoff_after || "3" }
            TuiField { id: backoffPolls; label: "退避轮数"; placeholder: "1–60"; text: controller.dialogData.failure_backoff_polls || "3" }
            TuiSwitchField { id: notifyEnabled; label: "桌面通知"; checked: String(controller.dialogData.notifications_enabled || "true").toLowerCase() === "true" }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                TuiButton {
                    primary: true
                    text: "生成设置预览"
                    enabled: !controller.dialogBusy
                    onClicked: controller.submitSettings({
                        poll_interval: pollInterval.text,
                        max_concurrent_checks: maxConcurrent.text,
                        failure_backoff_after: backoffAfter.text,
                        failure_backoff_polls: backoffPolls.text,
                        notifications_enabled: notifyEnabled.checked ? "true" : "false"
                    })
                }
            }
        }
    }

    Component {
        id: importComponent
        ColumnLayout {
            spacing: 12
            Text { text: "支持虎牙、斗鱼、B站、抖音、Twitch、YouTube 等直播间网址。确认前不会修改 CSV。"; color: overlay.textMuted; Layout.fillWidth: true; wrapMode: Text.Wrap }
            TuiField { id: importTag; label: "标签"; placeholder: "例如 ASMR / LOL"; text: controller.dialogData.tag || "" }
            TuiField { id: importUrl; label: "直播间网址"; placeholder: "https://…"; text: controller.dialogData.url || "" }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                TuiButton { primary: true; text: "生成导入预览"; enabled: !controller.dialogBusy; onClicked: controller.submitImport(importUrl.text, importTag.text) }
            }
        }
    }

    Component {
        id: proxyComponent
        ColumnLayout {
            spacing: 9
            Text { text: "留空使用默认代理；填写 direct 强制直连；可填写端口或完整代理 URL。"; color: overlay.textMuted; Layout.fillWidth: true; wrapMode: Text.Wrap }
            TuiField { id: proxyTwitch; label: "Twitch"; text: controller.dialogData.twitch || "" }
            TuiField { id: proxyYoutube; label: "YouTube"; text: controller.dialogData.youtube || "" }
            TuiField { id: proxyKick; label: "Kick"; text: controller.dialogData.kick || "" }
            TuiField { id: proxyChzzk; label: "CHZZK"; text: controller.dialogData.chzzk || "" }
            TuiField { id: proxyTiktok; label: "TikTok"; text: controller.dialogData.tiktok || "" }
            TuiField { id: proxyTwitcasting; label: "TwitCasting"; text: controller.dialogData.twitcasting || "" }
            Rectangle {
                objectName: "proxyHealthPanel"
                Layout.fillWidth: true
                Layout.preferredHeight: (controller.dialogData.health || []).length > 0 ? 154 : 58
                color: overlay.bg0
                border.color: controller.dialogData.healthOk === false ? overlay.red : overlay.line
                radius: 7
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 9
                    spacing: 6
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: controller.dialogData.healthSummary || "尚未测试当前代理配置"
                            color: controller.dialogData.healthOk === false ? overlay.red : overlay.textMuted
                            font.pixelSize: 12
                            font.weight: Font.DemiBold
                            Layout.fillWidth: true
                        }
                        Text { text: "仅测试连通性，不会保存"; color: overlay.textMuted; font.pixelSize: 10 }
                    }
                    GridLayout {
                        visible: (controller.dialogData.health || []).length > 0
                        Layout.fillWidth: true
                        columns: 2
                        rowSpacing: 4
                        columnSpacing: 8
                        Repeater {
                            model: controller.dialogData.health || []
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.preferredHeight: 34
                                color: overlay.bg2
                                border.color: modelData.status === "error" ? overlay.theme.dangerBorder
                                            : modelData.status === "ok" ? overlay.theme.successBorder : overlay.line
                                radius: 5
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 8
                                    anchors.rightMargin: 8
                                    spacing: 7
                                    Text { text: modelData.platform; color: overlay.textMain; font.pixelSize: 11; Layout.preferredWidth: 72 }
                                    Text {
                                        text: modelData.label + " · " + modelData.detail
                                        color: modelData.status === "error" ? overlay.red
                                             : modelData.status === "ok" ? overlay.green : overlay.textMuted
                                        font.pixelSize: 10
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }
                }
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                TuiButton {
                    text: "测试当前配置"
                    enabled: !controller.dialogBusy
                    onClicked: controller.testProxy({
                        twitch: proxyTwitch.text,
                        youtube: proxyYoutube.text,
                        kick: proxyKick.text,
                        chzzk: proxyChzzk.text,
                        tiktok: proxyTiktok.text,
                        twitcasting: proxyTwitcasting.text
                    })
                }
                TuiButton {
                    primary: true
                    text: "保存代理设置"
                    enabled: !controller.dialogBusy
                    onClicked: controller.submitProxy({
                        twitch: proxyTwitch.text,
                        youtube: proxyYoutube.text,
                        kick: proxyKick.text,
                        chzzk: proxyChzzk.text,
                        tiktok: proxyTiktok.text,
                        twitcasting: proxyTwitcasting.text
                    })
                }
            }
        }
    }

    Component {
        id: updateComponent
        ColumnLayout {
            id: updatePane
            objectName: "updatePane"
            property bool running: controller.dialogStage === "progress"
            property bool progressVisible: controller.dialogStage === "progress"
                                           || controller.dialogStage === "done"
                                           || controller.dialogStage === "failed"
            property int selectedIndex: {
                const items = controller.dialogData.items || []
                const requested = controller.dialogData.target || "mpv"
                for (let i = 0; i < items.length; ++i) {
                    if (items[i].value === requested) return i
                }
                return items.length > 0 ? 0 : -1
            }
            property var selectedItem: selectedIndex >= 0
                                       ? (controller.dialogData.items || [])[selectedIndex]
                                       : null
            function selectItem(index) {
                if (selectedItem && selectedItem.value === "bilibili_cookie" && selectedIndex !== index)
                    updateContent.clear()
                selectedIndex = index
            }
            spacing: 8
            Text {
                text: "组件管理"
                color: overlay.textMain
                font.pixelSize: 14
                font.weight: Font.DemiBold
            }
            Text {
                text: "打开时只读取本地状态，不访问网络。点击组件按钮后才检查该组件的远端版本。"
                color: overlay.textMuted
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                font.pixelSize: 12
            }
            ScrollView {
                id: componentCards
                objectName: "updateComponentCards"
                Layout.fillWidth: true
                Layout.preferredHeight: updatePane.progressVisible ? 265 : 342
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                GridLayout {
                    width: componentCards.availableWidth
                    columns: 2
                    columnSpacing: 7
                    rowSpacing: 7
                    Repeater {
                        model: controller.dialogData.items || []
                        delegate: Rectangle {
                            required property var modelData
                            required property int index
                            objectName: "updateCard-" + modelData.value
                            Layout.fillWidth: true
                            Layout.preferredHeight: 77
                            color: updatePane.selectedIndex === index ? overlay.theme.selectedBg : overlay.bg0
                            border.color: updatePane.selectedIndex === index ? overlay.accent
                                        : modelData.updateStatus === "unknown" ? overlay.theme.dangerBorder
                                        : !modelData.installed && modelData.actionEnabled ? overlay.theme.dangerBorder : overlay.line
                            border.width: updatePane.selectedIndex === index ? 2 : 1
                            radius: 7
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 9
                                spacing: 4
                                RowLayout {
                                    Layout.fillWidth: true
                                    Text {
                                        text: modelData.label || "-"
                                        color: overlay.textMain
                                        font.pixelSize: 13
                                        font.weight: Font.DemiBold
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                    Rectangle {
                                        Layout.preferredWidth: actionText.implicitWidth + 16
                                        Layout.preferredHeight: 23
                                        color: modelData.updateStatus === "unknown" ? overlay.theme.dangerBg
                                               : modelData.actionEnabled
                                                 ? (modelData.installed ? overlay.theme.successBg : overlay.theme.dangerBg) : overlay.bg2
                                        border.color: modelData.updateStatus === "unknown" ? overlay.theme.dangerBorder
                                                     : modelData.actionEnabled
                                                       ? (modelData.installed ? overlay.theme.successBorder : overlay.theme.dangerBorder) : overlay.line
                                        radius: 11
                                        Text {
                                            id: actionText
                                            anchors.centerIn: parent
                                            text: modelData.actionLabel || "查看"
                                            color: modelData.updateStatus === "unknown" ? overlay.theme.dangerText
                                                   : modelData.actionEnabled
                                                     ? (modelData.installed ? overlay.accent : overlay.theme.dangerText) : overlay.textMuted
                                            font.pixelSize: 10
                                            font.weight: Font.DemiBold
                                        }
                                    }
                                }
                                Text {
                                    text: (modelData.installed ? "当前 " : "状态 ") + (modelData.version || "未知")
                                          + "  ·  " + (modelData.source || "")
                                    color: modelData.installed ? overlay.accent : overlay.red
                                    font.pixelSize: 11
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                Text {
                                    text: modelData.kind === "tool"
                                          ? ((modelData.updateHint || "正在检查远端版本")
                                             + (modelData.downloadSize ? "  ·  " + modelData.downloadSize : ""))
                                          : "上次操作  " + (modelData.lastUpdated || "无记录")
                                    color: overlay.textMuted
                                    font.pixelSize: 9
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                            }
                            MouseArea {
                                anchors.fill: parent
                                enabled: !updatePane.progressVisible
                                cursorShape: Qt.PointingHandCursor
                                onClicked: updatePane.selectItem(parent.index)
                            }
                        }
                    }
                }
            }
            Rectangle {
                id: selectedUpdateDetail
                objectName: "selectedUpdateDetail"
                Layout.fillWidth: true
                Layout.preferredHeight: 94
                color: overlay.bg0
                border.color: overlay.line
                radius: 6
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 9
                    spacing: 4
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: updatePane.selectedItem ? updatePane.selectedItem.label : "未选择组件"
                            color: overlay.textMain
                            font.pixelSize: 12
                            font.weight: Font.DemiBold
                        }
                        Text {
                            text: updatePane.selectedItem
                                  ? "当前 " + updatePane.selectedItem.version
                                    + (updatePane.selectedItem.lastVersion ? " · 上次成功 " + updatePane.selectedItem.lastVersion : "")
                                  : ""
                            color: overlay.accent
                            font.pixelSize: 10
                        }
                        Item { Layout.fillWidth: true }
                        Text {
                            text: updatePane.selectedItem
                                  ? (updatePane.selectedItem.restartRequired ? "操作后需重启" : updatePane.selectedItem.source || "") : ""
                            color: overlay.textMuted
                            font.pixelSize: 10
                        }
                    }
                    Text {
                        visible: updatePane.selectedItem && updatePane.selectedItem.kind === "tool"
                        text: updatePane.selectedItem
                              ? ((updatePane.selectedItem.updateHint || "")
                                 + (updatePane.selectedItem.downloadSize
                                    ? "  ·  安装包 " + updatePane.selectedItem.downloadSize : "")) : ""
                        color: updatePane.selectedItem && updatePane.selectedItem.updateStatus === "unknown"
                               ? overlay.red : overlay.accent
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                        font.pixelSize: 10
                    }
                    Text {
                        text: updatePane.selectedItem ? updatePane.selectedItem.description : ""
                        color: overlay.textMuted
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        font.pixelSize: 11
                    }
                }
            }
            Text {
                visible: !updatePane.progressVisible && updatePane.selectedItem
                         && (updatePane.selectedItem.value === "fs1" || updatePane.selectedItem.value === "bilibili_cookie")
                text: updatePane.selectedItem && updatePane.selectedItem.value === "fs1"
                      ? "粘贴 FS /v1/room curl，或 Tampermonkey 导出的 zhibo.fs1-auth JSON。"
                      : "粘贴本人登录 B站后导出的 Netscape cookies.txt；请勿粘贴请求头或他人凭据。"
                color: overlay.textMuted
                Layout.fillWidth: true
                wrapMode: Text.Wrap
            }
            TextArea {
                id: updateContent
                Layout.fillWidth: true
                Layout.preferredHeight: 88
                visible: !updatePane.progressVisible && updatePane.selectedItem
                         && (updatePane.selectedItem.value === "fs1" || updatePane.selectedItem.value === "bilibili_cookie")
                placeholderText: updatePane.selectedItem && updatePane.selectedItem.value === "fs1" ? "curl 或 FS1 授权 JSON …" : "# Netscape HTTP Cookie File…"
                color: overlay.textMain
                selectByMouse: true
                wrapMode: TextEdit.WrapAnywhere
                background: Rectangle { color: overlay.bg0; border.color: updateContent.activeFocus ? overlay.accent : overlay.line; radius: 7 }
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                visible: !updatePane.progressVisible
                Layout.fillWidth: true
                Text {
                    text: updatePane.selectedItem && updatePane.selectedItem.value === "bilibili_cookie"
                          ? "Cookie 内容不会进入日志或更新记录"
                          : updatePane.selectedItem && updatePane.selectedItem.kind === "tool"
                            ? "仅在检查判定需要安装、更新或迁移时下载；安装前校验版本、体积和 SHA-256"
                            : updatePane.selectedItem && updatePane.selectedItem.kind === "package"
                              ? "版本来自 PyPI；执行前会再次比较，不会盲目运行 pip 更新"
                            : "执行过程会显示在右侧运行日志"
                    color: overlay.textMuted
                    font.pixelSize: 11
                }
                Item { Layout.fillWidth: true }
                TuiButton {
                    objectName: "updateActionButton"
                    primary: true
                    text: updatePane.selectedItem
                          ? (updatePane.selectedItem.actionLabel + " " + updatePane.selectedItem.label)
                          : "请选择组件"
                    enabled: !controller.dialogBusy && updatePane.selectedItem && updatePane.selectedItem.actionEnabled
                    onClicked: {
                        if (updatePane.selectedItem.actionKind === "check"
                                || updatePane.selectedItem.actionKind === "recheck") {
                            controller.checkUpdate(updatePane.selectedItem.value)
                        } else {
                            const target = updatePane.selectedItem.value
                            const content = updateContent.text
                            controller.submitUpdate(target, content)
                            if (target === "bilibili_cookie") updateContent.clear()
                        }
                    }
                }
            }
            Rectangle {
                objectName: "updateProgressArea"
                visible: updatePane.progressVisible
                Layout.fillWidth: true
                Layout.preferredHeight: 138
                color: overlay.bg0
                border.color: controller.dialogStage === "failed" ? overlay.red
                              : controller.dialogStage === "done" ? overlay.accent : overlay.lineBright
                radius: 7

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 9
                    Text {
                        Layout.fillWidth: true
                        text: controller.dialogStage === "failed" ? "更新失败"
                              : controller.dialogStage === "done" ? "更新完成" : "正在更新"
                        color: controller.dialogStage === "failed" ? overlay.red : overlay.accent
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                    }
                    Text {
                        Layout.fillWidth: true
                        text: controller.dialogData.progressText || "正在处理…"
                        color: overlay.textMain
                        wrapMode: Text.Wrap
                        font.pixelSize: 12
                    }
                    ProgressBar {
                        objectName: "updateProgressBar"
                        Layout.fillWidth: true
                        from: 0
                        to: 100
                        value: Math.max(0, controller.dialogData.progress || 0)
                        indeterminate: (controller.dialogData.progress || 0) < 0
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: (controller.dialogData.progress || 0) >= 0
                                  ? Math.round(controller.dialogData.progress || 0) + "%" : ""
                            color: overlay.textMuted
                            font.pixelSize: 11
                        }
                        Item { Layout.fillWidth: true }
                        TuiButton {
                            text: "继续管理"
                            visible: controller.dialogStage === "done" || controller.dialogStage === "failed"
                            onClicked: controller.continueUpdateCenter()
                        }
                    }
                }
            }
        }
    }

    Component {
        id: downloadComponent
        ColumnLayout {
            spacing: 10
            Text { text: "输入 YouTube 视频或直播链接，读取格式后选择下载画质。"; color: overlay.textMuted; Layout.fillWidth: true; wrapMode: Text.Wrap }
            TuiField { id: downloadUrl; label: "视频网址"; placeholder: "https://www.youtube.com/watch?v=…"; text: controller.dialogData.url || "" }
            RowLayout {
                visible: controller.dialogData.stage === "formats"
                Layout.fillWidth: true
                Text { text: "下载格式"; color: overlay.textMuted; Layout.preferredWidth: 125 }
                ComboBox {
                    id: formatBox
                    Layout.fillWidth: true
                    model: controller.dialogData.formats || []
                    textRole: "label"
                    valueRole: "index"
                }
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                TuiButton {
                    primary: true
                    visible: controller.dialogData.stage !== "formats"
                    text: "获取格式"
                    enabled: !controller.dialogBusy
                    onClicked: controller.requestDownloadFormats(downloadUrl.text)
                }
                TuiButton {
                    primary: true
                    visible: controller.dialogData.stage === "formats"
                    text: "开始下载"
                    enabled: !controller.dialogBusy && formatBox.currentIndex >= 0
                    onClicked: controller.startDownload(formatBox.currentValue)
                }
            }
        }
    }

    Component {
        id: progressComponent
        ColumnLayout {
            spacing: 14
            Item { Layout.fillHeight: true }
            Text {
                Layout.fillWidth: true
                text: controller.dialogData.progressText || "正在处理…"
                color: controller.dialogData.stage === "done" ? overlay.accent : overlay.textMain
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                font.pixelSize: 14
            }
            ProgressBar {
                Layout.fillWidth: true
                from: 0
                to: 100
                value: Math.max(0, controller.dialogData.progress || 0)
                indeterminate: (controller.dialogData.progress || 0) < 0
            }
            Text {
                Layout.fillWidth: true
                text: (controller.dialogData.progress || 0) >= 0 ? Math.round(controller.dialogData.progress || 0) + "%" : ""
                color: overlay.textMuted
                horizontalAlignment: Text.AlignHCenter
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                TuiButton { text: "关闭"; visible: controller.dialogData.stage === "done"; onClicked: controller.closeDialog() }
            }
        }
    }

    Shortcut {
        sequence: "Escape"
        enabled: overlay.visible && !controller.dialogBusy
        onActivated: controller.closeDialog()
    }
}
