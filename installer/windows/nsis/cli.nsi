; NSIS installer for the command line (example-cli). Windows; see common.nsh for the options.
;
;   makensis /DAPP_NAME="Example CLI" /DAPP_VERSION=0.1.0 /DSOURCE_DIR=..\..\build\installer\example-cli \
;            /DOUT_FILE=..\..\dist\example-cli-0.1.0-setup.exe installer\nsis\cli.nsi
; scripts/build_installer.py does this from the project's own metadata.

; Stable id of this product: keep it for the life of the project, so a newer setup replaces an
; older install. Generate a new one for your project (python -c "import uuid; print(uuid.uuid4())").
!define GUID "{6F1D5B2E-3A7C-4E19-9B64-0C2E8A5D7F31}"
!define MAIN_EXE "example-cli.exe"
!define HAS_PATH

!include "common.nsh"
