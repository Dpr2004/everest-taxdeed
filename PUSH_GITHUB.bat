@echo off
title Everest TaxDeed - Push GitHub
color 0B

echo.
echo ============================================================
echo   EVEREST TAXDEED - PUSH AUTOMATICO PARA GITHUB
echo ============================================================
echo.
echo Pasta: %~dp0
echo.
pause

echo.
echo [1/7] Entrando na pasta do bat...
cd /d "%~dp0"
echo OK
echo.

echo [2/7] Verificando Git...
git --version
if errorlevel 1 (
    echo ERRO: Git nao instalado. Baixe em https://git-scm.com/download/win
    pause
    exit /b 1
)
echo.

echo [3/7] Configurando Git...
git config --global user.name "Daniel Rocha"
git config --global user.email "dpr2004@gmail.com"
git config --global credential.helper manager
echo OK
echo.

echo [4/7] Inicializando repositorio...
if not exist ".git" (
    git init
    git branch -M main
)
echo OK
echo.

echo [5/7] Adicionando arquivos...
git add .
echo OK
echo.

echo [6/7] Commit...
git commit -m "Initial commit Everest TaxDeed Workers v1"
echo.

echo [7/7] Conectando ao GitHub e fazendo push...
git remote remove origin 2>nul
git remote add origin https://github.com/Dpr2004/everest-taxdeed.git
echo.
echo ============================================================
echo   ATENCAO: Vai abrir janela do GitHub pedindo login
echo   Clique em "Sign in with your browser"
echo ============================================================
pause

git push -u origin main --force

if errorlevel 1 (
    echo.
    echo ERRO no push. Tire um print e mande pro Cowork.
    pause
    exit /b 1
)

color 0A
echo.
echo ============================================================
echo   SUCESSO\! Codigo subiu para:
echo   https://github.com/Dpr2004/everest-taxdeed
echo ============================================================
pause
start https://github.com/Dpr2004/everest-taxdeed
