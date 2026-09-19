@echo off
rem MAgent 一键启动：首次运行自动建 venv 装依赖，然后起服务并打开浏览器
cd /d %~dp0

if not exist .venv (
    echo 首次运行：正在创建虚拟环境...
    python -m venv .venv || py -3 -m venv .venv
)
call .venv\Scripts\activate.bat

python -c "import fastapi, openai, fitz" 2>nul
if errorlevel 1 (
    echo 正在安装依赖（仅需一次）...
    python -m pip install -q -r requirements.txt
)

python -m magent serve
pause
