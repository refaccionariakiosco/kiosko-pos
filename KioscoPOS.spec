# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

# Recolecta todo el paquete `app` (varias partes —sync, realtime, coordinador—
# se importan en tiempo de ejecución y PyInstaller no las detecta por análisis
# estático). pocketbase/httpx se fuerzan además explícitamente.
hiddenimports = collect_submodules('app')
hiddenimports += ['pocketbase', 'httpx']


a = Analysis(
    ['launch.py'],
    pathex=[],
    binaries=[],
    datas=[('fondologin.jfif', '.')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KioscoPOS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icono.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='KioscoPOS',
)
