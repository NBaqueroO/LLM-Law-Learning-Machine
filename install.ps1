# install.ps1
# Instala el entorno completo del proyecto en una maquina nueva (Windows).
# Requiere: Python 3.12 instalado (no usar 3.13/3.14, PyTorch con CUDA aun no tiene
# wheels estables para esas versiones en Windows).
#
# Uso:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
#   .\install.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== 1. Verificando Python 3.12 ===" -ForegroundColor Cyan
$pyVersions = py -0 2>$null
if ($pyVersions -notmatch "3\.12") {
    Write-Host "No se encontro Python 3.12 instalado." -ForegroundColor Red
    Write-Host "Instalalo primero, por ejemplo con: winget install Python.Python.3.12"
    exit 1
}

Write-Host "=== 2. Creando entorno virtual (.venv) con Python 3.12 ===" -ForegroundColor Cyan
if (Test-Path ".venv") {
    Write-Host "Ya existe una carpeta .venv. Bórrala primero si quieres reinstalar desde cero." -ForegroundColor Yellow
} else {
    py -3.12 -m venv .venv
}

Write-Host "=== 3. Activando entorno virtual ===" -ForegroundColor Cyan
& ".\.venv\Scripts\Activate.ps1"

Write-Host "=== 4. Verificando version de Python dentro del venv ===" -ForegroundColor Cyan
python --version

Write-Host "=== 5. Actualizando pip ===" -ForegroundColor Cyan
python -m pip install --upgrade pip

Write-Host "=== 6. Instalando PyTorch con soporte CUDA (cu124) ===" -ForegroundColor Cyan
pip install torch --index-url https://download.pytorch.org/whl/cu124

Write-Host "=== 7. Verificando que CUDA quedo disponible ===" -ForegroundColor Cyan
$cudaCheck = python -c "import torch; print(torch.cuda.is_available())"
if ($cudaCheck -ne "True") {
    Write-Host "ADVERTENCIA: CUDA no esta disponible. El modelo correra en CPU (muy lento)." -ForegroundColor Yellow
    Write-Host "Revisa que tengas una GPU NVIDIA y drivers actualizados (nvidia-smi)." -ForegroundColor Yellow
} else {
    Write-Host "CUDA disponible correctamente." -ForegroundColor Green
}

Write-Host "=== 8. Instalando el resto de dependencias (requirements.txt) ===" -ForegroundColor Cyan
pip install -r requirements.txt

Write-Host "=== Instalacion completa ===" -ForegroundColor Green
Write-Host "Recuerda descargar/copiar los pesos del modelo en la carpeta 'modelos/' (no se versiona en git)."
Write-Host "Para activar el entorno en el futuro: .\.venv\Scripts\Activate.ps1"