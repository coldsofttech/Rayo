; NSIS installer for the desktop app (rayohx-ui, with rayohx-cli beside it). Windows only; see
; common.nsh for the options.
;
;   makensis /DAPP_NAME="RayoHX" /DAPP_VERSION=0.1.0 /DSOURCE_DIR=..\..\build\installer\rayohx-ui \
;            /DOUT_FILE=..\..\dist\rayohx-ui-0.1.0-setup.exe installer\nsis\ui.nsi
; scripts/build_installer.py does this from the project's own metadata.

; Stable id of this product: keep it for the life of the project, so a newer setup replaces an
; older install. Generate a new one for your project (python -c "import uuid; print(uuid.uuid4())").
!define GUID "{13C161C2-BD1D-4ACB-A31C-F9D1BC7B3DDA}"
!define MAIN_EXE "rayohx-ui.exe"
!define EXTRA_EXE "rayohx-cli.exe"
!define HAS_DESKTOP

!include "common.nsh"
