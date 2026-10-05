from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Slot
from PySide6.QtGui import QColor


HEADERS = ["状态", "标签", "主播", "平台", "标题", "画质", "检测", "健康", "插件", "错误"]
COLUMNS = ["status", "tags_text", "name", "platform", "title", "quality", "last_check", "health", "plugin", "error"]
COLUMN_WIDTHS = [58, 105, 165, 95, 330, 135, 92, 82, 125, 280]
COLUMN_PRESETS = {
    "full": COLUMN_WIDTHS,
    "compact": [58, 88, 150, 82, 330, 0, 0, 0, 0, 220],
}


class StreamTableModel(QAbstractTableModel):
    FollowerIndexRole = Qt.ItemDataRole.UserRole + 1
    ForegroundRole = Qt.ItemDataRole.UserRole + 2
    ConfiguredQualityRole = Qt.ItemDataRole.UserRole + 3
    ConfiguredPluginRole = Qt.ItemDataRole.UserRole + 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        self._column_widths = list(COLUMN_WIDTHS)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return str(row.get(COLUMNS[index.column()], "-"))
        if role in {Qt.ItemDataRole.ForegroundRole, self.ForegroundRole}:
            return self._foreground(row)
        if role == self.FollowerIndexRole:
            return int(row["idx"])
        if role == self.ConfiguredQualityRole:
            return str(row.get("configured_quality") or "best")
        if role == self.ConfiguredPluginRole:
            return str(row.get("configured_plugin") or row.get("plugin") or "")
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section] if 0 <= section < len(HEADERS) else ""
        return super().headerData(section, orientation, role)

    def roleNames(self):
        roles = dict(super().roleNames())
        roles[self.FollowerIndexRole] = b"followerIndex"
        # PySide's default QAbstractItemModel role map does not expose
        # ForegroundRole to QML.  A required ``foreground`` delegate property
        # therefore prevented every table delegate from being instantiated.
        roles[self.ForegroundRole] = b"foreground"
        roles[self.ConfiguredQualityRole] = b"configuredQuality"
        roles[self.ConfiguredPluginRole] = b"configuredPlugin"
        return roles

    # 快照之间真正影响显示的字段；用于跳过内容未变化的整表重建。
    _SIGNATURE_FIELDS = (
        "idx", "enabled", "live", "checking", "state", "name", "platform",
        "title", "quality", "last_check", "health", "plugin", "error",
    )

    def set_rows(self, rows: list[dict]) -> None:
        prepared = []
        for source in rows:
            row = dict(source)
            row["status"] = (
                "⊘" if not row.get("enabled")
                else "●" if row.get("live")
                else "◌" if row.get("checking")
                else "○"
            )
            row["tags_text"] = ", ".join(row.get("tags", []))
            prepared.append(row)
        if self._signature(prepared) == self._signature(self._rows):
            return
        self.beginResetModel()
        self._rows = prepared
        self.endResetModel()

    def _signature(self, rows: list[dict]) -> list:
        return [
            tuple(str(row.get(field)) for field in self._SIGNATURE_FIELDS)
            for row in rows
        ]

    def row_for_follower(self, follower_index: int) -> dict | None:
        return next((dict(row) for row in self._rows if row.get("idx") == follower_index), None)

    def first_follower_index(self) -> int:
        return int(self._rows[0]["idx"]) if self._rows else -1

    def adjacent_follower_index(self, current: int, offset: int) -> int:
        indices = [int(row["idx"]) for row in self._rows]
        if not indices:
            return -1
        try:
            position = indices.index(current)
        except ValueError:
            position = 0
        return indices[max(0, min(len(indices) - 1, position + offset))]

    @Slot(int, result=int)
    def columnWidth(self, column: int) -> int:
        return self._column_widths[column] if 0 <= column < len(self._column_widths) else 100

    def set_column_widths(self, widths: list[int]) -> None:
        if len(widths) != len(COLUMNS):
            return
        self._column_widths = [max(0, int(width)) for width in widths]

    @staticmethod
    def _foreground(row: dict) -> QColor:
        if not row.get("enabled"):
            return QColor("#667382")
        if row.get("error") not in {"", "-"}:
            return QColor("#ff667a")
        if row.get("live"):
            return QColor("#4ce38a")
        if row.get("checking"):
            return QColor("#ffd166")
        return QColor("#9aaabd")
