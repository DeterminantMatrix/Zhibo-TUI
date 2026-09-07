import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

ApplicationWindow {
    id: root
    objectName: "rootWindow"
    width: 1520
    height: 900
    minimumWidth: 1040
    minimumHeight: 680
    visible: true
    flags: Qt.Window | Qt.FramelessWindowHint
    title: "直播监控工具 · Qt Quick TUI"
    readonly property var theme: controller.themePalette
    color: bg0
    // 统一字体：不显式指定时，TextArea 等 Controls 会用 FluentWinUI3
    // 样式的默认字体，中文回退成宋体，与界面其他部分不一致。
    font.family: "Microsoft YaHei UI"

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
    property color yellow: theme.yellow
    property color red: theme.red
    property bool allowClose: false
    property bool shortcutsEnabled: controller.dialogKind === "" && !search.activeFocus
    // 与 controller/model 的列定义一一对应的排序键。
    readonly property var sortKeys: ["default", "tags", "name", "platform", "title", "quality", "last_check", "health", "plugin", "error"]
    // 最大化时 QML 读不到"正常几何"，手动跟踪窗口化状态下的尺寸位置。
    property var lastNormal: ({ x: 0, y: 0, w: 0, h: 0 })
    readonly property bool narrowLayout: width < 1180
    readonly property bool logPaneVisible: controller.logVisible

    palette.window: bg0
    palette.windowText: textMain
    palette.base: bg1
    palette.text: textMain
    palette.button: bg2
    palette.buttonText: textMain
    palette.highlight: theme.highlight
    palette.highlightedText: theme.highlightedText

    function restoreWindow() {
        showNormal()
        raise()
        requestActivate()
    }

    function hideWindow() {
        saveGeometry()
        hide()
        controller.windowHidden()
    }

    function saveGeometry() {
        const maximized = visibility === Window.Maximized
        const g = maximized ? lastNormal : { x: root.x, y: root.y, w: root.width, h: root.height }
        controller.saveWindowGeometry(g.x, g.y, g.w, g.h, maximized)
    }

    onXChanged: if (visibility === Window.Windowed) lastNormal.x = x
    onYChanged: if (visibility === Window.Windowed) lastNormal.y = y
    onWidthChanged: if (visibility === Window.Windowed) lastNormal.w = width
    onHeightChanged: if (visibility === Window.Windowed) lastNormal.h = height

    Component.onCompleted: {
        const g = controller.windowGeometry()
        if (g && g.valid) {
            root.x = g.x
            root.y = g.y
            root.width = g.width
            root.height = g.height
            if (g.maximized)
                root.visibility = Window.Maximized
        }
    }

    Shortcut {
        sequence: "Ctrl+F"
        context: Qt.ApplicationShortcut
        onActivated: {
            search.forceActiveFocus()
            search.selectAll()
        }
    }

    Shortcut {
        sequence: "F5"
        context: Qt.ApplicationShortcut
        onActivated: controller.action("refresh")
    }

    function exitWindow() {
        allowClose = true
        close()
    }

    function toggleMaximized() {
        if (visibility === Window.Maximized)
            showNormal()
        else
            showMaximized()
    }

    function openRowContextMenu(sourceItem, localX, localY) {
        const position = sourceItem.mapToItem(root.contentItem, localX, localY)
        const menuWidth = rowContextMenu.width
        const menuHeight = Math.max(180, rowContextMenu.implicitHeight)
        const safeX = Math.max(8, Math.min(position.x, root.width - menuWidth - 8))
        const safeY = Math.max(8, Math.min(position.y, root.height - menuHeight - 8))
        rowContextMenu.popup(root.contentItem, safeX, safeY)
    }

    onVisibilityChanged: {
        if (visibility === Window.Minimized && !allowClose && controller.trayAvailable)
            Qt.callLater(hideWindow)
    }

    onClosing: function(close) {
        saveGeometry()
        if (!allowClose && controller.trayAvailable) {
            close.accepted = false
            hideWindow()
        }
    }

    Connections {
        target: controller
        function onHideRequested() { root.hideWindow() }
        function onShowRequested() { root.restoreWindow() }
        function onLayoutStateChanged() { table.forceLayout() }
        function onQuitRequested() { root.saveGeometry() }
    }

    component TuiButton: Button {
        id: control
        property bool compact: false
        property bool primary: false
        implicitHeight: compact ? 32 : 38
        leftPadding: compact ? 10 : 14
        rightPadding: compact ? 10 : 14
        focusPolicy: Qt.StrongFocus
        contentItem: Text {
            text: control.text
            color: !control.enabled ? root.theme.disabledText
                 : control.primary ? root.theme.primaryText
                 : control.down ? root.theme.pressedText : root.textMain
            font.pixelSize: control.compact ? 12 : 13
            font.weight: control.activeFocus ? Font.DemiBold : Font.Normal
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            color: control.primary
                   ? (control.down ? root.theme.primaryPressed : control.hovered ? root.theme.primaryHover : root.accent)
                   : control.down ? root.theme.buttonPressed : control.hovered ? root.theme.buttonHover : root.bg2
            border.color: control.primary ? root.accent
                         : control.activeFocus ? root.accent : control.hovered ? root.lineBright : root.line
            border.width: 1
            radius: 7
        }
    }

    component TuiMenuItem: MenuItem {
        id: menuItem
        property bool danger: false
        implicitWidth: 230
        implicitHeight: 34
        leftPadding: 12
        rightPadding: 12
        contentItem: Text {
            text: menuItem.text
            color: !menuItem.enabled ? root.theme.disabledText
                 : menuItem.danger ? root.red : root.textMain
            font.pixelSize: 12
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            color: menuItem.highlighted ? root.theme.menuHighlight : root.bg1
            border.color: menuItem.highlighted ? root.lineBright : "transparent"
        }
    }

    component DetailLine: Rectangle {
        id: detailLine
        property string label: ""
        property string value: "-"
        property string tone: "normal"
        Layout.fillWidth: true
        implicitHeight: Math.max(42, detailValue.implicitHeight + 18)
        color: root.bg0
        border.color: root.line
        radius: 6
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 11
            anchors.rightMargin: 11
            spacing: 12
            Text {
                text: detailLine.label
                color: root.textMuted
                font.pixelSize: 11
                Layout.preferredWidth: 82
            }
            Text {
                id: detailValue
                text: detailLine.value || "-"
                color: detailLine.tone === "error" ? root.red
                     : detailLine.tone === "warning" ? root.yellow
                     : detailLine.tone === "ok" ? root.green
                     : detailLine.tone === "muted" ? root.textMuted
                     : root.textMain
                font.pixelSize: 12
                wrapMode: Text.WrapAnywhere
                Layout.fillWidth: true
            }
        }
    }

    component WindowButton: Rectangle {
        id: windowButton
        property string symbol: ""
        property bool danger: false
        signal clicked()
        implicitWidth: 46
        implicitHeight: 36
        color: buttonMouse.pressed
               ? (danger ? root.theme.dangerBorder : root.theme.buttonPressed)
               : buttonMouse.containsMouse
                 ? (danger ? root.red : root.theme.buttonHover) : "transparent"
        Text {
            anchors.centerIn: parent
            text: windowButton.symbol
            color: buttonMouse.containsMouse && windowButton.danger ? "white" : root.textMain
            font.pixelSize: windowButton.symbol === "—" ? 16 : 15
            font.weight: Font.DemiBold
        }
        MouseArea {
            id: buttonMouse
            anchors.fill: parent
            hoverEnabled: true
            acceptedButtons: Qt.LeftButton
            onClicked: windowButton.clicked()
        }
    }

    Rectangle {
        id: customTitleBar
        objectName: "customTitleBar"
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 38
        z: 20
        color: root.bg1
        border.color: root.line

        Item {
            anchors.fill: parent
            anchors.rightMargin: 138
            Text {
                anchors.left: parent.left
                anchors.leftMargin: 14
                anchors.verticalCenter: parent.verticalCenter
                text: "▣  ZHIBO  ·  直播监控工具"
                color: root.textMain
                font.pixelSize: 12
                font.weight: Font.DemiBold
            }
            DragHandler {
                target: null
                onActiveChanged: {
                    if (active)
                        root.startSystemMove()
                }
            }
            TapHandler {
                acceptedButtons: Qt.LeftButton
                onDoubleTapped: root.toggleMaximized()
            }
        }

        Row {
            anchors.top: parent.top
            anchors.right: parent.right
            height: parent.height
            WindowButton { symbol: "—"; onClicked: root.showMinimized() }
            WindowButton {
                symbol: root.visibility === Window.Maximized ? "❐" : "□"
                onClicked: root.toggleMaximized()
            }
            WindowButton { symbol: "×"; danger: true; onClicked: root.close() }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 8
        anchors.rightMargin: 8
        anchors.topMargin: 46
        anchors.bottomMargin: 8
        spacing: 6

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 58
            color: root.bg1
            border.color: root.line
            radius: 8

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 10
                spacing: 8

                Text {
                    text: "ZHIBO"
                    color: root.accent
                    font.pixelSize: 20
                    font.weight: Font.Bold
                    Layout.preferredWidth: 88
                }
                Text {
                    text: "直播监控"
                    color: root.textMuted
                    font.pixelSize: 12
                    Layout.preferredWidth: 70
                }
                Rectangle { width: 1; Layout.fillHeight: true; Layout.topMargin: 10; Layout.bottomMargin: 10; color: root.line }
                Rectangle {
                    Layout.preferredWidth: statusLabel.implicitWidth + 24
                    Layout.preferredHeight: 30
                    color: controller.statusText.indexOf("检测中") >= 0 ? root.theme.statusBusyBg : root.theme.statusIdleBg
                    border.color: controller.statusText.indexOf("检测中") >= 0 ? root.theme.statusBusyBorder : root.theme.statusIdleBorder
                    radius: 15
                    Text {
                        id: statusLabel
                        anchors.centerIn: parent
                        text: controller.statusText
                        color: controller.statusText.indexOf("检测中") >= 0 ? root.yellow : root.accent
                        font.pixelSize: 11
                    }
                }
                Item {
                    Layout.fillWidth: true
                }
                TextField {
                    id: search
                    visible: !root.narrowLayout
                    Layout.preferredWidth: 260
                    Layout.preferredHeight: 38
                    placeholderText: "搜索主播、平台、标题…"
                    selectByMouse: true
                    color: root.textMain
                    placeholderTextColor: root.theme.placeholder
                    onTextEdited: controller.setSearch(text)
                    background: Rectangle {
                        color: root.bg0
                        border.color: search.activeFocus ? root.accent : root.line
                        radius: 7
                    }
                }
                ComboBox {
                    id: filterBox
                    Layout.preferredWidth: 92
                    model: ["全部", "在线", "离线", "异常"]
                    // 用户交互会永久断开 currentIndex 的声明式绑定，
                    // 改为在控制器变化时命令式回写，保证键盘 O 切换时显示同步。
                    currentIndex: 0
                    onActivated: controller.setStateFilter(currentText)
                    Connections {
                        target: controller
                        function onStateFilterChanged() {
                            filterBox.currentIndex = Math.max(0, filterBox.find(controller.stateFilter))
                        }
                    }
                }
                TuiButton { text: "新增直播间"; onClicked: controller.action("import") }
                TuiButton { primary: true; text: "播放"; onClicked: controller.action("play") }
                TuiButton { text: "刷新  R"; onClicked: controller.action("refresh") }
                TuiButton {
                    id: themeButton
                    objectName: "themeButton"
                    text: "主题 · " + controller.themeLabel + "  ▾"
                    onClicked: themeMenu.popup(themeButton, themeButton.width - themeMenu.width, themeButton.height + 4)
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 44
            color: root.bg0
            border.color: root.line
            radius: 8

            Flickable {
                anchors.fill: parent
                anchors.margins: 4
                contentWidth: tagRow.width
                contentHeight: height
                clip: true

                Row {
                    id: tagRow
                    height: parent.height
                    Repeater {
                        model: controller.tabs
                        delegate: Rectangle {
                            required property string modelData
                            width: Math.max(76, tagLabel.implicitWidth + 28)
                            height: tagRow.height
                            color: controller.currentTag === modelData ? root.theme.tagSelected : "transparent"
                            border.color: controller.currentTag === modelData ? root.theme.tagBorder : "transparent"
                            radius: 6
                            Text {
                                id: tagLabel
                                anchors.centerIn: parent
                                text: modelData
                                color: controller.currentTag === modelData ? root.accent : root.textMuted
                                font.pixelSize: 12
                                font.weight: controller.currentTag === modelData ? Font.DemiBold : Font.Normal
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: controller.setCurrentTag(parent.modelData)
                            }
                        }
                    }
                }
            }
        }

        SplitView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            orientation: Qt.Horizontal

            handle: Rectangle {
                implicitWidth: 3
                color: SplitHandle.pressed ? root.accent : SplitHandle.hovered ? root.lineBright : root.line
            }

            Item {
                SplitView.fillWidth: true
                SplitView.minimumWidth: 620

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 0

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 34
                        color: root.bg0
                        border.color: root.line
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 6
                            spacing: 6
                            Text { text: "列表列"; color: root.textMuted; font.pixelSize: 11 }
                            TuiButton {
                                compact: true
                                primary: controller.columnPreset === "compact"
                                text: "紧凑"
                                onClicked: controller.setColumnPreset("compact")
                            }
                            TuiButton {
                                compact: true
                                primary: controller.columnPreset === "full"
                                text: "完整"
                                onClicked: controller.setColumnPreset("full")
                            }
                            TuiButton { compact: true; text: "重置列宽"; onClicked: controller.resetColumnWidths() }
                            Item { Layout.fillWidth: true }
                        }
                    }

                    HorizontalHeaderView {
                        id: header
                        Layout.fillWidth: true
                        Layout.preferredHeight: 38
                        syncView: table
                        clip: true
                        resizableColumns: true
                        delegate: Rectangle {
                            required property string display
                            required property int column
                            id: headerCell
                            implicitHeight: 38
                            color: root.theme.headerBg
                            border.color: root.line
                            Text {
                                anchors.fill: parent
                                anchors.leftMargin: 8
                                anchors.rightMargin: 5
                                // 活动排序列显示方向指示器。
                                text: {
                                    let label = headerCell.display
                                    const key = root.sortKeys[headerCell.column]
                                    if (controller.sortColumn === key && key !== "default")
                                        label += controller.sortDescending ? "  ▼" : "  ▲"
                                    return label
                                }
                                color: root.theme.headerText
                                font.pixelSize: 12
                                font.weight: Font.DemiBold
                                verticalAlignment: Text.AlignVCenter
                                elide: Text.ElideRight
                            }
                            // 点列头排序；右侧 7px 拖拽区由后面的 MouseArea 接管。
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: controller.setSortColumn(root.sortKeys[headerCell.column])
                            }
                            MouseArea {
                                anchors.top: parent.top
                                anchors.bottom: parent.bottom
                                anchors.right: parent.right
                                width: 7
                                cursorShape: Qt.SizeHorCursor
                                property real pressedX: 0
                                property real initialWidth: 0
                                onPressed: function(mouse) {
                                    pressedX = mouse.x
                                    initialWidth = streamModel.columnWidth(headerCell.column)
                                }
                                onPositionChanged: function(mouse) {
                                    if (pressed) {
                                        controller.setColumnWidth(headerCell.column, initialWidth + mouse.x - pressedX)
                                        colWidthSave.restart()
                                        table.forceLayout()
                                    }
                                }
                                onReleased: controller.persistColumnWidths()
                            }
                        }
                    }

                    TableView {
                        id: table
                        objectName: "streamTable"
                        focus: true
                        activeFocusOnTab: true
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        model: streamModel
                        clip: true
                        boundsBehavior: Flickable.StopAtBounds
                        columnSpacing: 1
                        rowSpacing: 1
                        columnWidthProvider: function(column) { return streamModel.columnWidth(column) }
                        rowHeightProvider: function(row) { return 37 }
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                        ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }

                        Keys.onReturnPressed: controller.action("play")
                        Keys.onEnterPressed: controller.action("play")
                        Keys.onUpPressed: controller.moveSelection(-1)
                        Keys.onDownPressed: controller.moveSelection(1)

                        delegate: Rectangle {
                            id: cell
                            required property int row
                            required property int column
                            required property string display
                            required property color foreground
                            required property int followerIndex
                            required property string configuredQuality
                            required property string configuredPlugin
                            property var qualityChoices: column === 5
                                                                 ? controller.qualityOptions(followerIndex) : []
                            implicitWidth: streamModel.columnWidth(column)
                            implicitHeight: 37
                            color: followerIndex === controller.selectedFollower
                                   ? root.theme.rowSelected
                                   : row % 2 ? root.theme.rowAlternate : root.bg1
                            border.color: followerIndex === controller.playingFollower ? root.accent
                                         : followerIndex === controller.selectedFollower ? root.theme.tagBorder : root.theme.rowBorder
                            border.width: followerIndex === controller.playingFollower ? 2 : 1
                            // 保存成功后模型角色变化会重新同步画质下拉框的显示。
                            onConfiguredQualityChanged: if (column === 5) qualitySelector.currentIndex = qualitySelector.configuredIndex()
                            onQualityChoicesChanged: if (column === 5) qualitySelector.currentIndex = qualitySelector.configuredIndex()

                            Text {
                                visible: cell.column !== 5
                                anchors.fill: parent
                                anchors.leftMargin: 9
                                anchors.rightMargin: 5
                                // 正在播放的主播行：状态列加 ▶ 前缀。
                                text: (cell.followerIndex === controller.playingFollower && cell.column === 0 ? "▶" : "") + cell.display
                                color: cell.followerIndex === controller.playingFollower ? root.accent : cell.foreground
                                font.pixelSize: 12
                                verticalAlignment: Text.AlignVCenter
                                elide: Text.ElideRight
                            }
                            ComboBox {
                                id: qualitySelector
                                objectName: cell.column === 5 ? "qualitySelector-" + cell.followerIndex : ""
                                visible: cell.column === 5
                                anchors.fill: parent
                                anchors.margins: 3
                                model: cell.qualityChoices
                                textRole: "label"
                                valueRole: "value"
                                font.pixelSize: 11
                                leftPadding: 7
                                rightPadding: 22
                                function configuredIndex() {
                                    const wanted = (cell.configuredQuality || "best").toLowerCase()
                                    for (let i = 0; i < cell.qualityChoices.length; ++i) {
                                        if ((cell.qualityChoices[i].value || "").toLowerCase() === wanted)
                                            return i
                                    }
                                    return 0
                                }
                                Component.onCompleted: currentIndex = configuredIndex()
                                onActivated: function(index) {
                                    controller.selectFollower(cell.followerIndex)
                                    controller.setFollowerQuality(cell.followerIndex, currentValue)
                                    // 保存是异步的且可能失败；显示始终跟随已保存配置，
                                    // 成功保存后 cell 的角色变化会更新显示。
                                    currentIndex = configuredIndex()
                                    table.forceActiveFocus()
                                }
                                contentItem: Text {
                                    leftPadding: 2
                                    rightPadding: 2
                                    text: qualitySelector.displayText
                                    color: cell.foreground
                                    font.pixelSize: 11
                                    verticalAlignment: Text.AlignVCenter
                                    elide: Text.ElideRight
                                }
                                indicator: Text {
                                    x: qualitySelector.width - width - 7
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: "⌄"
                                    color: qualitySelector.hovered ? root.accent : root.textMuted
                                    font.pixelSize: 14
                                }
                                background: Rectangle {
                                    color: qualitySelector.down ? root.theme.buttonPressed
                                         : qualitySelector.hovered ? root.theme.buttonHover : "transparent"
                                    border.color: qualitySelector.activeFocus ? root.accent
                                                : qualitySelector.hovered ? root.lineBright : "transparent"
                                    radius: 5
                                }
                                popup.background: Rectangle {
                                    color: root.bg1
                                    border.color: root.lineBright
                                    radius: 6
                                }
                                ToolTip.visible: hovered
                                ToolTip.text: "当前配置：" + (cell.configuredQuality || "best")
                                              + " · " + (cell.configuredPlugin || "插件")
                            }
                            MouseArea {
                                anchors.fill: parent
                                acceptedButtons: cell.column === 5
                                                 ? Qt.RightButton
                                                 : Qt.LeftButton | Qt.RightButton
                                onPressed: function(mouse) {
                                    table.forceActiveFocus()
                                    controller.selectFollower(cell.followerIndex)
                                    if (mouse.button === Qt.RightButton) {
                                        root.openRowContextMenu(cell, mouse.x, mouse.y)
                                        mouse.accepted = true
                                    }
                                }
                                onDoubleClicked: function(mouse) {
                                    // 双击 = 主操作（播放），与音乐/视频类软件一致；
                                    // 画质列排除，避免和下拉框抢事件。
                                    if (mouse.button === Qt.LeftButton && cell.column !== 5) {
                                        controller.selectFollower(cell.followerIndex)
                                        controller.action("play")
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Rectangle {
                id: logContainer
                objectName: "logContainer"
                // 折叠时保留一条细栏，点击即可展开（工具栏不再有收起/展开按钮）。
                SplitView.preferredWidth: root.logPaneVisible ? controller.logWidth : 44
                SplitView.minimumWidth: root.logPaneVisible ? 250 : 44
                SplitView.maximumWidth: root.logPaneVisible ? 600 : 44
                color: root.bg1
                border.color: root.line
                radius: 8

                Rectangle {
                    anchors.fill: parent
                    radius: 8
                    visible: !root.logPaneVisible
                    color: stripHover.hovered ? root.bg2 : root.bg1
                    TapHandler { onTapped: controller.setLogVisible(true) }
                    HoverHandler { id: stripHover; cursorShape: Qt.PointingHandCursor }
                    Text {
                        text: "运行日志 ▶"
                        color: root.textMuted
                        font.pixelSize: 12
                        anchors.centerIn: parent
                        rotation: -90
                    }
                }

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 8
                    visible: root.logPaneVisible
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: "运行日志"; color: root.textMain; font.pixelSize: 13; font.weight: Font.DemiBold }
                        Rectangle { width: 6; height: 6; radius: 3; color: root.green }
                        Item { Layout.fillWidth: true }
                        TuiButton {
                            compact: true
                            text: "收起"
                            onClicked: controller.setLogVisible(false)
                        }
                    }
                    Flickable {
                        id: logScroll
                        objectName: "logScroll"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: width
                        contentHeight: Math.max(logPanel.height, height)
                        flickableDirection: Flickable.VerticalFlick
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {
                            policy: ScrollBar.AsNeeded
                        }

                        // 视口底色：内容不足一屏时补齐面板，避免露出容器底色。
                        Rectangle {
                            width: logScroll.width
                            height: logScroll.height
                            color: root.bg0
                            border.color: root.line
                            radius: 6
                        }

                        TextArea {
                            id: logPanel
                            objectName: "logPanel"
                            width: logScroll.width
                            // 高度跟随内容：若强行撑满视口，TextArea 会因
                            // 光标可见性触发内部滚动位移，把文字渲染到面板中部。
                            height: contentHeight + topPadding + bottomPadding
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.WrapAnywhere
                            // 增量追加：常规日志只 append 新行，避免每条日志
                            // 重拼 800 行并整体重设文本；截断时才整体重建。
                            font.family: "Microsoft YaHei UI"
                            Component.onCompleted: text = controller.logText
                            Connections {
                                target: controller
                                function onLogAppended(line) { logPanel.append(line) }
                                function onLogTrimmed() { logPanel.text = controller.logText }
                            }
                            color: root.theme.headerText
                            font.pixelSize: 12
                            background: Item {}
                            onTextChanged: Qt.callLater(scrollToLatest)
                            // 内容少时顶部对齐（从上往下阅读）；超出视口后吸底。
                            function scrollToLatest() {
                                logScroll.contentY = Math.max(
                                    0,
                                    logScroll.contentHeight - logScroll.height
                                )
                            }
                        }
                    }
                }
                onWidthChanged: {
                    if (visible && width >= 250)
                        logWidthSave.restart()
                }
            }
        }

        Timer {
            id: logWidthSave
            interval: 350
            onTriggered: controller.setLogWidth(Math.round(logContainer.width))
        }

        Timer {
            id: colWidthSave
            interval: 400
            onTriggered: controller.persistColumnWidths()
        }

        Rectangle {
            id: functionBar
            Layout.fillWidth: true
            Layout.preferredHeight: 54
            color: root.bg1
            border.color: root.line
            radius: 8

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 9
                anchors.rightMargin: 9
                spacing: 7

                Repeater {
                    model: [
                        { label: "播放  Enter", command: "play", primary: true },
                        { label: "详情  I", command: "details", primary: false },
                        { label: "编辑  E", command: "edit", primary: false },
                        { label: "复制流  C", command: "copy_stream", primary: false },
                        { label: "通知  N", command: "notification", primary: false }
                    ]
                    delegate: TuiButton {
                        required property var modelData
                        compact: true
                        primary: modelData.primary
                        text: modelData.label
                        onClicked: controller.action(modelData.command)
                    }
                }
                Item { Layout.fillWidth: true }
                Text {
                    text: controller.summaryText
                    color: root.textMuted
                    font.pixelSize: 11
                }
                TuiButton {
                    id: moreButton
                    objectName: "moreButton"
                    compact: true
                    text: "更多  ···"
                    onClicked: moreActionsMenu.popup(moreButton, moreButton.width - moreActionsMenu.width, -moreActionsMenu.implicitHeight)
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 28
            color: root.theme.footerBg
            border.color: root.line
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 8
                anchors.rightMargin: 8
                Text { text: controller.statusText; color: root.textMuted; font.pixelSize: 11; Layout.fillWidth: true }
                Text { text: "↑↓ 选择   Enter 播放   X 停止"; color: root.theme.footerText; font.pixelSize: 11 }
            }
        }
    }

    Menu {
        id: themeMenu
        objectName: "themeMenu"
        width: 190
        modal: false
        dim: false
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: root.bg1; border.color: root.lineBright; radius: 7 }
        TuiMenuItem { text: (controller.themeName === "blue" ? "✓  " : "    ") + "深海蓝"; onTriggered: controller.setTheme("blue") }
        TuiMenuItem { text: (controller.themeName === "black" ? "✓  " : "    ") + "纯黑"; onTriggered: controller.setTheme("black") }
        TuiMenuItem { text: (controller.themeName === "warm" ? "✓  " : "    ") + "暖黄"; onTriggered: controller.setTheme("warm") }
        TuiMenuItem { text: (controller.themeName === "day" ? "✓  " : "    ") + "日间"; onTriggered: controller.setTheme("day") }
    }

    Menu {
        id: moreActionsMenu
        objectName: "moreActionsMenu"
        width: 250
        modal: false
        dim: false
        background: Rectangle { color: root.bg1; border.color: root.lineBright; radius: 7 }
        TuiMenuItem { text: "监控设置  [S]"; onTriggered: controller.action("settings") }
        TuiMenuItem { text: "打开网页  [F]"; onTriggered: controller.action("web") }
        TuiMenuItem { text: "导入直播间  [J]"; onTriggered: controller.action("import") }
        TuiMenuItem { text: "更新中心  [U]"; onTriggered: controller.action("update") }
        TuiMenuItem { text: "下载视频  [D]"; onTriggered: controller.action("download") }
        TuiMenuItem { text: "平台代理  [P]"; onTriggered: controller.action("proxy") }
        TuiMenuItem { text: "切换筛选  [O]"; onTriggered: controller.action("filter") }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: root.line } }
        TuiMenuItem { text: "隐藏到托盘  [H]"; onTriggered: controller.action("tray") }
        TuiMenuItem { text: "退出程序  [T]"; onTriggered: controller.action("quit") }
    }

    Menu {
        id: rowContextMenu
        objectName: "rowContextMenu"
        width: 310
        modal: false
        dim: false
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle {
            color: root.bg1
            border.color: root.lineBright
            radius: 7
        }

        TuiMenuItem { text: "修改直播间信息  [E]"; onTriggered: controller.action("edit") }
        TuiMenuItem { text: "查看状态详情  [I]"; onTriggered: controller.action("details") }
        TuiMenuItem {
            text: controller.selectedRowEnabled ? "停止监控（保留配置）" : "恢复监控"
            onTriggered: controller.action("toggle_enabled")
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: root.line } }
        TuiMenuItem { text: "播放直播  [Enter]"; onTriggered: controller.action("play") }
        TuiMenuItem { text: "停止播放  [X]"; onTriggered: controller.action("stop") }
        TuiMenuItem { text: "复制直播流地址  [C]"; onTriggered: controller.action("copy_stream") }
        TuiMenuItem { text: "复制直播间摘要  [Ctrl+Shift+C]"; onTriggered: controller.action("copy_selected") }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: root.line } }
        TuiMenuItem { text: "打开直播网页  [F]"; onTriggered: controller.action("web") }
        TuiMenuItem { text: "下载当前视频  [D]"; onTriggered: controller.action("download_selected") }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: root.line } }
        TuiMenuItem { text: "删除直播间"; danger: true; onTriggered: controller.action("delete") }
    }

    DialogOverlay {
        anchors.fill: parent
        z: 200
    }

    Rectangle {
        anchors.fill: parent
        color: root.theme.backdrop
        visible: controller.detailsVisible
        z: 100
        MouseArea { anchors.fill: parent; onClicked: controller.hideDetails() }

        Rectangle {
            width: Math.min(510, parent.width * 0.46)
            anchors.top: parent.top
            anchors.topMargin: 10
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 10
            anchors.right: parent.right
            anchors.rightMargin: 10
            color: root.bg1
            border.color: root.lineBright
            radius: 10
            MouseArea { anchors.fill: parent }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: controller.selectedDetails.detailTitle || "关注详情"; color: root.textMain; font.pixelSize: 17; font.weight: Font.Bold; Layout.fillWidth: true; elide: Text.ElideRight }
                    TuiButton { compact: true; text: "关闭  Esc"; onClicked: controller.hideDetails() }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: root.line }
                ScrollView {
                    id: detailScroll
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    ColumnLayout {
                        width: detailScroll.availableWidth
                        spacing: 6
                        Repeater {
                            model: controller.selectedDetails.detailRows || []
                            delegate: DetailLine {
                                required property var modelData
                                label: modelData.label || ""
                                value: modelData.value || "-"
                                tone: modelData.tone || "normal"
                            }
                        }
                        Text {
                            visible: !controller.selectedDetails.detailRows || controller.selectedDetails.detailRows.length === 0
                            text: "正在读取状态机、平台健康度与最近事件…"
                            color: root.textMuted
                            font.pixelSize: 12
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    TuiButton { text: "打开网页 [F]"; onClicked: controller.action("web") }
                    TuiButton { text: "复制流 [C]"; onClicked: controller.action("copy_stream") }
                    Item { Layout.fillWidth: true }
                }
            }
        }
    }

    component ResizeHandle: MouseArea {
        property int resizeEdges: Qt.LeftEdge
        enabled: root.visibility === Window.Windowed
        z: 1000
        hoverEnabled: true
        onPressed: root.startSystemResize(resizeEdges)
    }

    ResizeHandle {
        anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
        width: 6; resizeEdges: Qt.LeftEdge; cursorShape: Qt.SizeHorCursor
    }
    ResizeHandle {
        anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
        width: 6; resizeEdges: Qt.RightEdge; cursorShape: Qt.SizeHorCursor
    }
    ResizeHandle {
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        height: 6; resizeEdges: Qt.TopEdge; cursorShape: Qt.SizeVerCursor
    }
    ResizeHandle {
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        height: 6; resizeEdges: Qt.BottomEdge; cursorShape: Qt.SizeVerCursor
    }
    ResizeHandle {
        anchors.left: parent.left; anchors.top: parent.top
        width: 9; height: 9; resizeEdges: Qt.LeftEdge | Qt.TopEdge; cursorShape: Qt.SizeFDiagCursor
    }
    ResizeHandle {
        anchors.right: parent.right; anchors.top: parent.top
        width: 9; height: 9; resizeEdges: Qt.RightEdge | Qt.TopEdge; cursorShape: Qt.SizeBDiagCursor
    }
    ResizeHandle {
        anchors.left: parent.left; anchors.bottom: parent.bottom
        width: 9; height: 9; resizeEdges: Qt.LeftEdge | Qt.BottomEdge; cursorShape: Qt.SizeBDiagCursor
    }
    ResizeHandle {
        anchors.right: parent.right; anchors.bottom: parent.bottom
        width: 9; height: 9; resizeEdges: Qt.RightEdge | Qt.BottomEdge; cursorShape: Qt.SizeFDiagCursor
    }

    Shortcut { sequence: "N"; enabled: root.shortcutsEnabled; onActivated: controller.action("notification") }
    Shortcut { sequence: "R"; enabled: root.shortcutsEnabled; onActivated: controller.action("refresh") }
    Shortcut { sequence: "I"; enabled: root.shortcutsEnabled; onActivated: controller.action("details") }
    Shortcut { sequence: "E"; enabled: root.shortcutsEnabled; onActivated: controller.action("edit") }
    Shortcut { sequence: "S"; enabled: root.shortcutsEnabled; onActivated: controller.action("settings") }
    Shortcut { sequence: "F"; enabled: root.shortcutsEnabled; onActivated: controller.action("web") }
    Shortcut { sequence: "C"; enabled: root.shortcutsEnabled; onActivated: controller.action("copy_stream") }
    Shortcut { sequence: "U"; enabled: root.shortcutsEnabled; onActivated: controller.action("update") }
    Shortcut { sequence: "D"; enabled: root.shortcutsEnabled; onActivated: controller.action("download") }
    Shortcut { sequence: "P"; enabled: root.shortcutsEnabled; onActivated: controller.action("proxy") }
    Shortcut { sequence: "Ctrl+Shift+C"; enabled: root.shortcutsEnabled; onActivated: controller.action("copy_selected") }
    Shortcut { sequence: "O"; enabled: root.shortcutsEnabled; onActivated: controller.action("filter") }
    Shortcut { sequence: "H"; enabled: root.shortcutsEnabled; onActivated: controller.action("tray") }
    Shortcut { sequence: "T"; enabled: root.shortcutsEnabled; onActivated: controller.action("quit") }
    Shortcut {
        sequence: "Return"
        enabled: root.shortcutsEnabled && !table.activeFocus
        onActivated: controller.action("play")
        onActivatedAmbiguously: controller.action("play")
    }
    Shortcut {
        sequence: "Enter"
        enabled: root.shortcutsEnabled && !table.activeFocus
        onActivated: controller.action("play")
        onActivatedAmbiguously: controller.action("play")
    }
    Shortcut { sequence: "X"; enabled: root.shortcutsEnabled; onActivated: controller.action("stop") }
    Shortcut { sequence: "J"; enabled: root.shortcutsEnabled; onActivated: controller.action("import") }
    Shortcut { sequence: "Up"; enabled: root.shortcutsEnabled && !table.activeFocus; onActivated: controller.moveSelection(-1) }
    Shortcut { sequence: "Down"; enabled: root.shortcutsEnabled && !table.activeFocus; onActivated: controller.moveSelection(1) }
    Shortcut { sequence: "Escape"; enabled: controller.detailsVisible && controller.dialogKind === ""; onActivated: controller.hideDetails() }
}
