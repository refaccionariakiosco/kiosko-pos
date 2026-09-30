# AGENTS.md

Guía operativa para el agente de código en este repo. Leerla antes de tocar nada.

## Proceso OBLIGATORIO para cada cambio en el sistema

Para **cada** cambio que se pida (bug, funcionalidad, ajuste), seguir este orden,
sin excepciones y en este orden:

1. **Pruebas**

   ```
   python -m pytest -q
   ```

   Deben quedar en verde. Si algo rompe, arreglar antes de seguir.

2. **Exe**

   ```
   python -m PyInstaller KioscoPOS.spec --noconfirm --clean
   ```

   Resultado: `dist\KioscoPOS\` (`KioscoPOS.exe` + `_internal`).

3. **Sustituir en AppData**

   ```
   Stop-Process -Name KioscoPOS -Force -ErrorAction SilentlyContinue
   Remove-Item -LiteralPath "$env:LOCALAPPDATA\KioscoPOS\_internal" -Recurse -Force
   Remove-Item -LiteralPath "$env:LOCALAPPDATA\KioscoPOS\KioscoPOS.exe" -Force
   Copy-Item -Path "dist\KioscoPOS\*" -Destination "$env:LOCALAPPDATA\KioscoPOS" -Recurse -Force
   ```

   - **NUNCA** tocar `%LOCALAPPDATA%\KioscoPOS\data\` (ahí vive `kiosco.db`).
   - Smoke test: abrir `%LOCALAPPDATA%\KioscoPOS\KioscoPOS.exe` y comprobar
     que aparece la ventana de acceso; luego cerrarla.

4. **Instalador**

   ```
   & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DMyAppVersion=X.Y.Z installer\KioscoPOS.iss
   ```

   Resultado: `dist\SetupKioscoPOS-X.Y.Z.exe`.

5. **Commit + push**

   ```
   git add <sólo fuentes y tests>
   git commit -m "<asunto en español, cuerpo con viñetas>"
   git push origin main
   ```

6. **Release**

   ```
   git tag -a vX.Y.Z -m "vX.Y.Z: resumen"
   git push origin vX.Y.Z
   ```

   La CI (`.github/workflows/release.yml`) compila y publica la release con
   `SetupKioscoPOS-X.Y.Z.exe` y `KioscoPOS-X.Y.Z.zip`. Verificar con:

   ```
   GET https://api.github.com/repos/refaccionariakiosco/kiosko-pos/releases/latest
   ```

## Versionado

- **Patch automático en cada cambio**: `1.3.0 → 1.3.1 → 1.3.2 …`.
  Archivos a actualizar:
  - `app/__init__.py` → `__version__`
  - `pyproject.toml` → `version`
  - `installer/KioscoPOS.iss` → `#define MyAppVersion`
  - La ventana principal muestra `app.__version__` (`app/interface/main_window.py`).
- **Minor** (`1.4.0`) sólo para una función nueva grande (nueva área o flujo completo).
- El tag es siempre `v` + la versión exacta de los archivos anteriores;
  la CI toma el número del tag para el instalador.

## Qué NO commitear

- `dist/`, `build/` (generados, ya en `.gitignore`)
- `data/`, `*.db`, `*.csv`, `.env` (datos y secretos)
- `session-*.md` (borradores de sesiones)
- `*.bak`, `*.fixed.bak`, `.stock_tmp/`
- `cuadre_daemon/` (herramienta aparte, por ahora sin versionar)

## Comandos útiles

| Para | Comando |
| --- | --- |
| Correr desde código | `python -m app.main` (o `iniciar.bat`) |
| Pruebas de un área | `python -m pytest tests/test_smoke.py -q` |
| Estado / últimos cambios | `git status --short` · `git log --oneline -5` |
| Releases previas | `git tag --list` |
