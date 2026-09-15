"""Pantalla de acceso (login) a pantalla completa, con imagen de fondo."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from app.interface.widgets import make_label
from app.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if getattr(sys, "frozen", False):
    _ASSET_BASE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    _ASSET_BASE = PROJECT_ROOT
BG_IMAGE = _ASSET_BASE / "fondologin.jfif"


class LoginDialog(QDialog):
    """Login en pantalla completa (sin bordes) con fondo y tarjeta centrada."""

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle(f"{settings.app_name} · Acceso")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setModal(True)

        if BG_IMAGE.exists():
            self._bg = QPixmap(str(BG_IMAGE))
        else:
            self._bg = QPixmap()
        if self._bg.isNull():
            self.setStyleSheet("QWidget { background-color: #111827; }")

        # Tarjeta centrada
        self._outer = QVBoxLayout(self)
        self._outer.addStretch(1)

        card = QFrame()
        card.setObjectName("loginCard")
        card.setFixedWidth(380)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 26, 28, 26)
        card_layout.setSpacing(12)

        title = make_label(settings.app_name, object_name="loginTitle", alignment=Qt.AlignCenter)
        subtitle = make_label(
            settings.store.name or "Punto de venta", object_name="muted", alignment=Qt.AlignCenter
        )
        card_layout.addWidget(title)
        card_layout.addWidget(subtitle)

        self.username = QLineEdit()
        self.username.setPlaceholderText("Usuario")
        self.password = QLineEdit()
        self.password.setPlaceholderText("Contraseña")
        self.password.setEchoMode(QLineEdit.Password)
        card_layout.addSpacing(6)
        card_layout.addWidget(self.username)
        card_layout.addWidget(self.password)

        self.error_label = make_label("", object_name="loginError", alignment=Qt.AlignCenter)
        self.error_label.setHidden(True)
        card_layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        quit_btn = QPushButton("Salir")
        quit_btn.setObjectName("ghost")
        self.login_btn = QPushButton("Ingresar")
        self.login_btn.setObjectName("primary")
        quit_btn.clicked.connect(self.reject)
        self.login_btn.clicked.connect(self._attempt_login)
        buttons.addWidget(quit_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.login_btn)
        card_layout.addLayout(buttons)

        self._outer.addWidget(card, alignment=Qt.AlignHCenter)
        self._outer.addStretch(1)

        QShortcut(QKeySequence(Qt.Key_Return), self, self._attempt_login)
        QShortcut(QKeySequence(Qt.Key_Enter), self, self._attempt_login)

        self.username.setFocus()

    # ------------------------------------------------------------------ #

    def validate_credentials(self, username: str, password: str) -> bool:
        good_user = (self._settings.login_username or "").strip() or "angel"
        good_pass = self._settings.login_password or "12345"
        return username.strip() == good_user and password == good_pass

    def _attempt_login(self) -> None:
        if self.validate_credentials(self.username.text(), self.password.text()):
            self.accept()
            return
        self.error_label.setText("Usuario o contraseña incorrectos.")
        self.error_label.setVisible(True)
        self.password.selectAll()
        self.password.setFocus()

    def paintEvent(self, event) -> None:
        if not self._bg.isNull():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawPixmap(self.rect(), self._bg)
            painter.end()
        super().paintEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self.isFullScreen():
            self.showFullScreen()