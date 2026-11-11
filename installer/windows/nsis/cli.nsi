; NSIS installer for the command line (rayo-cli). Windows; see common.nsh for the options.
;
;   makensis /DAPP_NAME="Example CLI" /DAPP_VERSION=0.1.0 /DSOURCE_DIR=..\..\build\installer\rayo-cli \
;            /DOUT_FILE=..\..\dist\rayo-cli-0.1.0-setup.exe installer\nsis\cli.nsi
; scripts/build_installer.py does this from the project's own metadata.

; Stable id of this product: keep it for the life of the project, so a newer setup replaces an
; older install. Generate a new one for your project (python -c "import uuid; print(uuid.uuid4())").
!define GUID "{792CFD32-BAD6-4CB9-B788-87E42AB2D926}"
!define MAIN_EXE "rayo-cli.exe"
!define HAS_PATH

!include "common.nsh"
