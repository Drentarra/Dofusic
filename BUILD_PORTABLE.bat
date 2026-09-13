@echo off
setlocal EnableExtensions
chcp 65001 >nul

for %%I in ("%~dp0.") do set "ROOT=%%~fI"
set "DATA=%ROOT%\Data"
set "BUILD=%ROOT%\.portable-build"
set "UV_VERSION=0.12.10"
set "UVROOT=%BUILD%\uv"
set "UVEXE=%UVROOT%\uv.exe"
set "PYMANAGED=%BUILD%\python-managed"
set "VENV=%BUILD%\build-venv"
set "PYEXE=%VENV%\Scripts\python.exe"

rem ============================================================
rem Isolation stricte :
rem - uv est local au projet
rem - CPython est gere par uv dans .portable-build\python-managed
rem - les paquets ne sont JAMAIS installes dans ce CPython gere
rem - un venv jetable .portable-build\build-venv recoit les dependances
rem - aucun Python du PC, PATH Python, registre Python ou user-site n'est utilise
rem ============================================================
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PIP_NO_INPUT=1"
set "UV_NO_CONFIG=1"
set "UV_NO_MODIFY_PATH=1"
set "UV_UNMANAGED_INSTALL=%UVROOT%"
set "UV_PYTHON_INSTALL_DIR=%PYMANAGED%"
set "UV_CACHE_DIR=%BUILD%\uv-cache"
set "UV_PYTHON_INSTALL_REGISTRY=0"

cd /d "%ROOT%"

echo ==========================================================
echo          DOFUSIC V25.1 ECO - BUILD PORTABLE WINDOWS X64
echo ==========================================================
echo.
echo Architecture du builder :
echo   uv local -^> Python 3.11.16 gere et immuable -^> venv de build
echo.
echo Le Python deja installe sur ce PC n'est jamais utilise,
echo modifie ou inscrit dans le registre. Les dependances sont
echo installees uniquement dans .portable-build\build-venv.
echo.
echo Le ZIP final est autonome : aucun Python, pip ou installateur
echo ne sera necessaire sur le PC de l'utilisateur final.
echo.

if not exist "%DATA%\main.py" (
    echo [ECHEC] Data\main.py introuvable.
    goto :fail
)

if /I not "%PROCESSOR_ARCHITECTURE%"=="AMD64" if /I not "%PROCESSOR_ARCHITEW6432%"=="AMD64" (
    echo [ECHEC] Ce builder est prevu pour Windows x64.
    goto :fail
)

if not exist "%BUILD%" mkdir "%BUILD%"

rem ------------------------------------------------------------
rem 1. Installer uv localement, sans PATH global.
rem ------------------------------------------------------------
if not exist "%UVEXE%" (
    echo [1/6] Preparation de uv %UV_VERSION% dans .portable-build...
    if exist "%UVROOT%" rmdir /s /q "%UVROOT%"
    mkdir "%UVROOT%"

    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; irm 'https://releases.astral.sh/github/uv/releases/download/%UV_VERSION%/uv-installer.ps1' | iex"
    if errorlevel 1 goto :fail

    if not exist "%UVEXE%" (
        echo [ECHEC] uv.exe n'a pas ete cree dans "%UVROOT%".
        goto :fail
    )
) else (
    echo [1/6] uv local deja present.
)

"%UVEXE%" --version
if errorlevel 1 goto :fail

rem ------------------------------------------------------------
rem 2. Installer le CPython gere, puis creer un VENV JETABLE.
rem    Le CPython gere reste volontairement non modifie.
rem ------------------------------------------------------------
echo [2/6] Preparation du Python 3.11.16 gere + venv de BUILD...
"%UVEXE%" python install 3.11.16 --install-dir "%PYMANAGED%" --no-bin --no-registry
if errorlevel 1 goto :fail

if exist "%VENV%" rmdir /s /q "%VENV%"
"%UVEXE%" venv "%VENV%" --python 3.11.16 --managed-python
if errorlevel 1 goto :fail

if not exist "%PYEXE%" (
    echo [ECHEC] Le venv de build n'a pas cree "%PYEXE%".
    goto :fail
)

rem ------------------------------------------------------------
rem 3. Verifier le Python DU VENV et Tcl/Tk avant toute installation.
rem ------------------------------------------------------------
echo [3/6] Verification du venv Python et de Tkinter/Tcl-Tk...
"%PYEXE%" -c "import sys, tkinter, _tkinter; assert sys.prefix != sys.base_prefix, 'Le Python de build doit etre un venv'; print('[OK] Python', sys.version.split()[0], '- Tk', tkinter.TkVersion, '- venv:', sys.prefix, '- base:', sys.base_prefix)"
if errorlevel 1 (
    echo [ECHEC] Le Python du venv existe mais Tkinter/Tcl-Tk est inutilisable.
    goto :fail
)

rem ------------------------------------------------------------
rem 4. Installer les dependances UNIQUEMENT dans le venv.
rem ------------------------------------------------------------
echo [4/6] Installation des dependances dans le venv de BUILD...
"%UVEXE%" pip install --python "%PYEXE%" -r "%DATA%\requirements.txt" -r "%DATA%\requirements-build.txt"
if errorlevel 1 goto :fail

rem RapidOCR declare opencv-python comme dependance. Dofusic utilise volontairement
rem opencv-python-headless, donc RapidOCR est installe sans ses dependances afin
rem d'eviter deux distributions OpenCV concurrentes et ~des dizaines de Mo inutiles.
"%UVEXE%" pip install --python "%PYEXE%" --no-deps rapidocr==3.9.2
if errorlevel 1 goto :fail

rem Garde-fou : un seul fournisseur cv2 doit etre present dans le venv.
"%PYEXE%" -c "import importlib.metadata as m; d={x.metadata.get('Name','').lower() for x in m.distributions()}; assert 'opencv-python' not in d, 'opencv-python installe en doublon'; assert 'opencv-python-headless' in d, 'opencv-python-headless absent'"
if errorlevel 1 goto :fail

rem Verification explicite que PyInstaller et les deps principales
rem sont bien importables depuis le venv, pas depuis le Python gere.
"%PYEXE%" -c "import PyInstaller, pytest, cv2, numpy, onnxruntime, pygame, rapidocr; print('[OK] Environnement de build complet')"
if errorlevel 1 goto :fail

rem ------------------------------------------------------------
rem 5. Tests, modeles OCR, PyInstaller et auto-test du .exe.
rem ------------------------------------------------------------
echo [5/6] Tests + modeles OCR + construction du package...
"%PYEXE%" "%DATA%\tools\build_portable.py" --root "%ROOT%"
if errorlevel 1 goto :fail

rem ------------------------------------------------------------
rem 6. Ne declarer la reussite que si tous les artefacts sont presents.
rem ------------------------------------------------------------
echo [6/6] Verification de la release terminee...
if not exist "%ROOT%\Release\Dofusic\Dofusic.exe" (
    echo [ECHEC] Dofusic.exe absent de la release.
    goto :fail
)
if not exist "%ROOT%\Release\Dofusic\Data\UserData" (
    echo [ECHEC] Data\UserData absent de la release.
    goto :fail
)
if not exist "%ROOT%\Release\Dofusic_V25_1_ECO_WINDOWS_X64.zip" (
    echo [ECHEC] ZIP portable final absent.
    goto :fail
)

echo.
echo ==========================================================
echo [OK] BUILD TERMINE
echo ==========================================================
echo Dossier : "%ROOT%\Release\Dofusic"
echo ZIP     : "%ROOT%\Release\Dofusic_V25_1_ECO_WINDOWS_X64.zip"
echo.
echo Le ZIP genere est celui a partager.
goto :success

:fail
echo.
echo ==========================================================
echo [ECHEC] La construction portable a echoue.
echo ==========================================================
echo Aucun package incomplet ne doit etre distribue.
echo.
echo Pour repartir de zero, supprime uniquement le dossier :
echo   "%BUILD%"
echo puis relance BUILD_PORTABLE.bat.
pause
exit /b 1

:success
pause
exit /b 0
