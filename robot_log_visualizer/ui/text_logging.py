from qtpy import QtWidgets
from qtpy.QtGui import QColor, QBrush
from qtpy.QtWidgets import QTableWidgetItem


class TextLoggingItem:
    def __init__(self, table_widget, background_color="#ffeba8"):
        self.table_widget = table_widget
        self.index_coloured_cell = None
        self.background_color = QBrush(QColor(background_color))
        self.white_background = QBrush(QColor("#ffffff"))
        self.table_widget.clear()

    def add_entry(self, text, timestamp, font_color=None):
        item = QTableWidgetItem(text)
        if font_color is not None:
            # text = '<font color="' + str(font_color) + '">' + text + "</font>"
            item.setForeground(QBrush(QColor(font_color)))

        item_timestamp = QTableWidgetItem(f"{timestamp:.2f}")
        if font_color is not None:
            # text = '<font color="' + str(font_color) + '">' + text + "</font>"
            item_timestamp.setForeground(QBrush(QColor(font_color)))

        self.table_widget.insertRow(self.table_widget.rowCount())
        self.table_widget.setItem(self.table_widget.rowCount() - 1, 0, item_timestamp)
        self.table_widget.setItem(self.table_widget.rowCount() - 1, 1, item)

    def clean(self):
        self.table_widget.setColumnCount(2)
        self.table_widget.setRowCount(0)
        self.table_widget.resizeColumnToContents(1)
        self.table_widget.clear()
        header = self.table_widget.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.index_coloured_cell = None

    def highlight_cell(self, index):
        if index == self.index_coloured_cell:
            return

        if self.index_coloured_cell is not None:
            self.table_widget.item(self.index_coloured_cell, 0).setBackground(
                self.white_background
            )
            self.table_widget.item(self.index_coloured_cell, 1).setBackground(
                self.white_background
            )

        if index is not None:
            self.table_widget.item(index, 0).setBackground(self.background_color)
            self.table_widget.item(index, 1).setBackground(self.background_color)

        self.index_coloured_cell = index
