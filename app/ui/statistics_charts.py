"""Native PyQt6 charts for the order dashboard, without extra dependencies."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QSizePolicy, QToolTip, QWidget


def compact_silver(value: int | float) -> str:
    absolute = abs(float(value))
    sign = "−" if value < 0 else ""
    if absolute >= 1_000_000_000:
        return f"{sign}{absolute / 1_000_000_000:.1f}B"
    if absolute >= 1_000_000:
        return f"{sign}{absolute / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{sign}{absolute / 1_000:.1f}K"
    return f"{sign}{absolute:.0f}"


class ChartWidget(QWidget):
    panel = QColor("#151f28")
    border = QColor("#2b414e")
    grid = QColor("#273945")
    text = QColor("#dce7ed")
    muted = QColor("#7f929f")
    gold = QColor("#f2b84b")
    green = QColor("#70d3a2")
    red = QColor("#ef8585")

    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        self.title = title
        self.subtitle = subtitle
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(220)

    def _panel(self, painter: QPainter) -> QRectF:
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(self.border, 1))
        painter.setBrush(self.panel)
        painter.drawRoundedRect(bounds, 12, 12)
        painter.setPen(self.text)
        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        painter.drawText(QRectF(18, 13, bounds.width() - 36, 24), self.title)
        if self.subtitle:
            painter.setPen(self.muted)
            painter.setFont(QFont("Segoe UI", 8))
            painter.drawText(QRectF(18, 36, bounds.width() - 36, 20), self.subtitle)
        return bounds

    def _empty(self, painter: QPainter, bounds: QRectF) -> None:
        painter.setPen(self.muted)
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(
            bounds.adjusted(18, 58, -18, -18),
            Qt.AlignmentFlag.AlignCenter,
            "Még nincs lezárt Order ehhez a kimutatáshoz.",
        )


class TimelineChart(ChartWidget):
    """Revenue, total cost and profit timeline with hover details."""

    series = (
        ("gross", "Bevétel", QColor("#f2b84b")),
        ("cost", "Költség", QColor("#ef8585")),
        ("profit", "Profit", QColor("#70d3a2")),
    )

    def __init__(self, title: str = "Bevétel és profit idővonala") -> None:
        super().__init__(title, "Vidd az egeret egy időszak fölé a pontos értékekhez")
        self.rows: list[dict[str, object]] = []
        self._points: list[tuple[float, dict[str, object]]] = []
        self.setMinimumHeight(290)

    def set_data(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = self._panel(painter)
        self._points = []
        if not self.rows:
            self._empty(painter, bounds)
            return

        legend_x = max(250.0, bounds.width() - 290.0)
        painter.setFont(QFont("Segoe UI", 8))
        for _key, label, color in self.series:
            painter.setPen(QPen(color, 3))
            painter.drawLine(QPointF(legend_x, 26), QPointF(legend_x + 17, 26))
            painter.setPen(self.muted)
            painter.drawText(QRectF(legend_x + 23, 15, 65, 22), label)
            legend_x += 88

        plot = bounds.adjusted(62, 67, -22, -42)
        values = [float(row[key]) for row in self.rows for key, _, _ in self.series]
        low = min(0.0, min(values))
        high = max(1.0, max(values))
        span = high - low or 1.0
        painter.setFont(QFont("Segoe UI", 7))
        for step in range(5):
            ratio = step / 4
            y = plot.bottom() - plot.height() * ratio
            value = low + span * ratio
            painter.setPen(QPen(self.grid, 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(self.muted)
            painter.drawText(QRectF(8, y - 9, 48, 18), Qt.AlignmentFlag.AlignRight, compact_silver(value))

        count = len(self.rows)
        x_step = plot.width() / max(1, count - 1)
        for index, row in enumerate(self.rows):
            x = plot.left() + (index * x_step if count > 1 else plot.width() / 2)
            self._points.append((x, row))
            if index in {0, count - 1} or count <= 6 or index % max(1, count // 5) == 0:
                painter.setPen(self.muted)
                painter.drawText(
                    QRectF(x - 45, plot.bottom() + 8, 90, 18),
                    Qt.AlignmentFlag.AlignHCenter,
                    str(row["label"]),
                )

        for key, _label, color in self.series:
            path = QPainterPath()
            points: list[QPointF] = []
            for index, row in enumerate(self.rows):
                x = plot.left() + (index * x_step if count > 1 else plot.width() / 2)
                y = plot.bottom() - ((float(row[key]) - low) / span) * plot.height()
                point = QPointF(x, y)
                points.append(point)
                path.moveTo(point) if index == 0 else path.lineTo(point)
            painter.setPen(QPen(color, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            painter.setBrush(color)
            painter.setPen(QPen(self.panel, 2))
            for point in points:
                painter.drawEllipse(point, 4, 4)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._points:
            return
        x, row = min(self._points, key=lambda pair: abs(pair[0] - event.position().x()))
        if abs(x - event.position().x()) > 45:
            QToolTip.hideText()
            return
        QToolTip.showText(
            event.globalPosition().toPoint(),
            f"{row['label']}\n"
            f"Bevétel: {int(row['gross']):,} silver\n"
            f"Költség: {int(row['cost']):,} silver\n"
            f"Nettó profit: {int(row['profit']):,} silver\n"
            f"{int(row.get('orders', 0)):,} lezárt Order",
            self,
        )

    def leaveEvent(self, event) -> None:
        QToolTip.hideText()
        super().leaveEvent(event)


class ComparisonBarChart(ChartWidget):
    def __init__(self) -> None:
        super().__init__("Pénzügyi összetétel", "Bevétel, teljes költség és nettó eredmény")
        self.values: list[tuple[str, int, QColor]] = []

    def set_totals(self, gross: int, cost: int, profit: int) -> None:
        self.values = [
            ("Bevétel", gross, self.gold),
            ("Költség", cost, self.red),
            ("Profit", profit, self.green if profit >= 0 else self.red),
        ]
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = self._panel(painter)
        if not self.values or not any(value for _, value, _ in self.values):
            self._empty(painter, bounds)
            return
        plot = bounds.adjusted(28, 68, -28, -34)
        high = max(1, max(abs(value) for _, value, _ in self.values))
        bar_width = min(72.0, plot.width() / 5)
        gap = plot.width() / len(self.values)
        for index, (label, value, color) in enumerate(self.values):
            center = plot.left() + gap * (index + .5)
            height = (abs(value) / high) * max(15, plot.height() - 38)
            rect = QRectF(center - bar_width / 2, plot.bottom() - height - 22, bar_width, height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 7, 7)
            painter.setPen(self.text)
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
            painter.drawText(QRectF(center - gap / 2, rect.top() - 22, gap, 18), Qt.AlignmentFlag.AlignCenter, compact_silver(value))
            painter.setPen(self.muted)
            painter.drawText(QRectF(center - gap / 2, plot.bottom() - 16, gap, 18), Qt.AlignmentFlag.AlignCenter, label)


class HorizontalBarChart(ChartWidget):
    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__(title, subtitle)
        self.rows: list[tuple[str, int]] = []
        self.setMinimumHeight(240)

    def set_data(self, rows: list[tuple[str, int]]) -> None:
        self.rows = rows[:6]
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = self._panel(painter)
        if not self.rows:
            self._empty(painter, bounds)
            return
        plot = bounds.adjusted(18, 65, -18, -15)
        maximum = max(1, max(abs(value) for _, value in self.rows))
        row_height = plot.height() / max(1, len(self.rows))
        label_width = min(155.0, plot.width() * .38)
        for index, (label, value) in enumerate(self.rows):
            y = plot.top() + index * row_height
            painter.setPen(self.text)
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
            clipped = label if len(label) <= 24 else label[:21] + "…"
            painter.drawText(QRectF(plot.left(), y, label_width - 8, row_height), Qt.AlignmentFlag.AlignVCenter, clipped)
            track = QRectF(plot.left() + label_width, y + row_height * .27, plot.width() - label_width - 58, row_height * .46)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.grid)
            painter.drawRoundedRect(track, 5, 5)
            fill = QRectF(track)
            fill.setWidth(max(4, track.width() * abs(value) / maximum))
            painter.setBrush(self.green if value >= 0 else self.red)
            painter.drawRoundedRect(fill, 5, 5)
            painter.setPen(self.green if value >= 0 else self.red)
            painter.drawText(QRectF(track.right() + 7, y, 52, row_height), Qt.AlignmentFlag.AlignVCenter, compact_silver(value))
