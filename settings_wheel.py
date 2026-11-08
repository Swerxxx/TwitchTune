"""Keep wheel scrolling from editing settings controls."""
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox, QComboBox


class SettingsWheelGuard(QObject):
    def protect(self, control):
        control.installEventFilter(self)

    def protect_children(self, container):
        for widget_type in (QComboBox, QAbstractSpinBox, QAbstractSlider):
            for control in container.findChildren(widget_type):
                self.protect(control)

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel:
            return super().eventFilter(watched, event)

        parent = watched.parentWidget()
        while parent is not None and not isinstance(parent, QAbstractScrollArea):
            parent = parent.parentWidget()
        if parent is not None:
            viewport = parent.viewport()
            position = viewport.mapFromGlobal(event.globalPosition().toPoint())
            scroll_event = QWheelEvent(
                QPointF(position), event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                event.source(), event.pointingDevice(),
            )
            QCoreApplication.sendEvent(viewport, scroll_event)
        event.accept()
        return True
