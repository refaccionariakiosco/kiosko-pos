"""Tema visual y hoja de estilos global de la aplicación."""

from __future__ import annotations

APP_STYLESHEET = r"""
QWidget {
    font-family: "Segoe UI", "Segoe UI Variable Text";
    font-size: 13px;
    color: #1f2937;
}

QMainWindow, #centralArea {
    background-color: #f3f4f6;
}

#topbar {
    background-color: #111827;
    border-bottom: 1px solid #374151;
}
#topbar QLabel#appTitle {
    color: #ffffff;
    font-size: 18px;
    font-weight: 700;
}
#topbar QLabel#appSubtitle {
    color: #9ca3af;
    font-size: 11px;
}

QToolButton#topNav {
    color: #d1d5db;
    background-color: transparent;
    border: none;
    border-radius: 8px;
    padding: 4px 12px;
    font-size: 12px;
    min-width: 74px;
}
QToolButton#topNav:hover {
    background-color: #1f2937;
    color: #ffffff;
}
QToolButton#topNav:checked {
    background-color: #ef4444;
    color: #ffffff;
    font-weight: 600;
}

QPushButton#primary {
    background-color: #ef4444;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 18px;
    font-weight: 600;
}
QPushButton#primary:hover { background-color: #dc2626; }
QPushButton#primary:disabled { background-color: #b1b9c4; color: #ffffff; }

QPushButton#ghost {
    background-color: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    padding: 7px 14px;
    color: #374151;
}
QPushButton#ghost:hover { background-color: #f9fafb; border-color: #9ca3af; }
QPushButton#ghost:danger { color: #b91c1c; border-color: #fca5a5; }
QPushButton#ghost:danger:hover { background-color: #fef2f2; }

QPushButton#addButton {
    background-color: #16a34a;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 16px;
    font-weight: 700;
}
QPushButton#addButton:hover { background-color: #15803d; }
QPushButton#addButton:pressed { background-color: #166534; }
QPushButton#addButton:disabled { background-color: #b1b9c4; color: #ffffff; }

QPushButton#accent {
    background-color: #2563eb;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 18px;
    font-weight: 600;
}
QPushButton#accent:hover { background-color: #1d4ed8; }
QPushButton#accent:disabled { background-color: #b1b9c4; color: #ffffff; }

QPushButton#navItem {
    background-color: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    padding: 8px 10px;
    font-weight: 500;
}
QPushButton#navItem:hover { background-color: #f3f4f6; }

QLabel#pageTitle { font-size: 20px; font-weight: 700; color: #111827; }
QLabel#sectionTitle { font-size: 15px; font-weight: 600; color: #111827; }

QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox, QTextEdit {
    background-color: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    padding: 6px 8px;
}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus {
    border-color: #ef4444;
}

QTableWidget {
    background-color: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    gridline-color: #f3f4f6;
}
QTableWidget::item { padding: 4px 8px; }
QHeaderView::section {
    background-color: #f9fafb;
    border: none;
    border-bottom: 1px solid #e5e7eb;
    padding: 6px 8px;
    font-weight: 600;
    color: #374151;
}

QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }

QPushButton#productCard {
    background-color: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 10px;
    text-align: left;
}
QPushButton#productCard:hover { border-color: #ef4444; background-color: #fff7f7; }
QPushButton#productCard:pressed { background-color: #fecaca; }

QFrame#card {
    background-color: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
}
QLabel#kpiValue { font-size: 26px; font-weight: 700; color: #111827; }
QLabel#kpiLabel { font-size: 12px; color: #6b7280; }
QLabel#totalAmount { font-size: 24px; font-weight: 700; color: #ef4444; }
QLabel#muted { color: #6b7280; font-size: 12px; }

QMessageBox { background-color: #ffffff; }

QTabWidget::pane {
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    background-color: #ffffff;
}
QTabBar::tab {
    background-color: #ffffff;
    border: 1px solid #d1d5db;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 6px 14px;
    margin-right: 2px;
    color: #374151;
}
QTabBar::tab:selected {
    background-color: #ef4444;
    color: #ffffff;
    font-weight: 600;
}
QTabBar::tab:!selected:hover { background-color: #f3f4f6; }

QToolButton#payMethod {
    background-color: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 10px;
    padding: 8px 6px;
    min-width: 68px;
}
QToolButton#payMethod:hover { border-color: #ef4444; background-color: #fff7f7; }
QToolButton#payMethod:checked {
    background-color: #ef4444;
    border-color: #ef4444;
    color: #ffffff;
    font-weight: 600;
}

QFrame#loginCard {
    background-color: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 14px;
}
QLabel#loginTitle { font-size: 22px; font-weight: 700; color: #111827; }
QLabel#loginError { color: #b91c1c; font-size: 12px; font-weight: 600; }
"""


def apply_theme(app) -> None:
    app.setStyleSheet(APP_STYLESHEET)