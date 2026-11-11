; NSIS installer for the desktop app (example-ui, with example-cli beside it). Windows only; see
; common.nsh for the options.
;
;   makensis /DAPP_NAME="Example App" /DAPP_VERSION=0.1.0 /DSOURCE_DIR=..\..\build\installer\example-ui \
;            /DOUT_FILE=..\..\dist\example-ui-0.1.0-setup.exe installer\nsis\ui.nsi
; scripts/build_installer.py does this from the project's own metadata.

; Stable id of this product: keep it for the life of the project, so a newer setup replaces an
; older install. Generate a new one for your project (python -c "import uuid; print(uuid.uuid4())").
!define GUID "{B84C2A90-5D13-47E6-A0F2-93C1E6D84B57}"
!define MAIN_EXE "example-ui.exe"
!define EXTRA_EXE "example-cli.exe"
!define HAS_DESKTOP

!include "common.nsh"
