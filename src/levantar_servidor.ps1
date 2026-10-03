# levantar_servidor.ps1
# Instala dependencias, valida que todo quedo bien y levanta el servidor del modelo
# (OpenAI-compatible) en esta terminal, en una sola funcion. La interfaz web va aparte:
# interfaz\levantar_interfaz.ps1.
#
# Uso (desde la raiz del repo):
#   powershell -ExecutionPolicy Bypass -File src\levantar_servidor.ps1
#
# Variables de entorno (todas opcionales):
#   LLM_MODELO   por defecto BSC-LT/salamandra-7b-instruct (el de src/config.py)
#   PUERTO       servidor del modelo, por defecto 8000
#   ALIA_4BIT    "1" para cargar el modelo en 4-bit
#   SIN_GPU      "1" para no exigir CUDA y correr en CPU
#   VENV         carpeta del entorno virtual, por defecto .venv
#
# El servidor es src/servidor_alia.py, que sirve el modelo con transformers sobre la GPU.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Levantar-Servidor {
    $modelo = if ($env:LLM_MODELO) { $env:LLM_MODELO } else { "BSC-LT/salamandra-7b-instruct" }
    $puerto = if ($env:PUERTO) { $env:PUERTO } else { "8000" }
    $sinGpu = $env:SIN_GPU -eq "1"

    function Revisar($paso) { if ($LASTEXITCODE -ne 0) { throw "Fallo: $paso (codigo $LASTEXITCODE)" } }

    $venv = if ($env:VENV) { $env:VENV } else { ".venv" }
    Write-Host "== 1. entorno virtual ($venv)" -ForegroundColor Cyan
    if (-not (Test-Path "$venv\Scripts\python.exe")) {
        $versiones = py -0 | Out-String
        if ($versiones -match "3\.12") { $v = "3.12" }
        elseif ($versiones -match "3\.13") { $v = "3.13" }
        else { throw "Se necesita Python 3.12 o 3.13 (winget install Python.Python.3.12)" }
        py -$v -m venv $venv; Revisar "crear $venv"
    }
    $py = (Resolve-Path "$venv\Scripts\python.exe").Path

    Write-Host "== 2. dependencias" -ForegroundColor Cyan
    & $py -m pip install -q --upgrade pip; Revisar "actualizar pip"
    # torch con CUDA antes de requirements.txt, para que sentence-transformers no traiga el de CPU
    # sin stderr: con ErrorActionPreference=Stop, PowerShell 5.1 corta el script si un ejecutable escribe en stderr redirigido
    $tieneCuda = & $py -c "import importlib.util as u; print(bool(u.find_spec('torch')) and __import__('torch').cuda.is_available())"
    if ($tieneCuda -ne "True" -and -not $sinGpu) {
        & $py -m pip install -q torch --index-url https://download.pytorch.org/whl/cu126; Revisar "torch CUDA"
    }
    & $py -m pip install -q -r requirements.txt; Revisar "requirements.txt"

    # winget lo instala en Program Files o, sin permisos de administrador, en la carpeta del usuario
    $tessDirs = @("$env:ProgramFiles\Tesseract-OCR", "$env:LOCALAPPDATA\Programs\Tesseract-OCR")
    function Buscar-Tesseract { $tessDirs | Where-Object { Test-Path "$_\tesseract.exe" } | Select-Object -First 1 }
    if (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
        if (-not (Buscar-Tesseract)) {
            winget install -e --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements
            Revisar "instalar Tesseract"
        }
        $tessDir = Buscar-Tesseract
        if ($tessDir) { $env:PATH = "$tessDir;$env:PATH" }
    }

    Write-Host "== 3. validacion" -ForegroundColor Cyan
    & $py -m pip check; Revisar "pip check (dependencias incompatibles)"
    $env:SIN_GPU = if ($sinGpu) { "1" } else { "0" }
    $validar = @'
import importlib, os, shutil, sys

modulos = {
    "langgraph": "langgraph", "langchain-openai": "langchain_openai", "pydantic": "pydantic",
    "jsonschema": "jsonschema", "bm25s": "bm25s", "faiss-cpu": "faiss",
    "sentence-transformers": "sentence_transformers", "numpy": "numpy",
    "transformers": "transformers", "accelerate": "accelerate", "bitsandbytes": "bitsandbytes",
    "sentencepiece": "sentencepiece", "protobuf": "google.protobuf", "requests": "requests",
    "beautifulsoup4": "bs4", "pymupdf": "fitz", "pytesseract": "pytesseract", "Pillow": "PIL",
    "truststore": "truststore", "rank-bm25": "rank_bm25", "pytest": "pytest", "torch": "torch",
    "fastapi": "fastapi", "uvicorn": "uvicorn",
}
fallos = []
for paquete, modulo in modulos.items():
    try:
        importlib.import_module(modulo)
        print(f"  ok  {paquete}")
    except Exception as e:
        fallos.append(paquete)
        print(f"  ERR {paquete}: {e}")

if not shutil.which("tesseract"):
    fallos.append("tesseract (binario del sistema)")
    print("  ERR tesseract no esta en el PATH")

import torch
if torch.cuda.is_available():
    print(f"  ok  CUDA: {torch.cuda.get_device_name(0)} "
          f"({torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB)")
elif os.environ["SIN_GPU"] == "1":
    print("  --  CUDA no disponible (SIN_GPU=1, se continua)")
else:
    fallos.append("CUDA")
    print("  ERR CUDA no disponible: revisa drivers (nvidia-smi) o usa SIN_GPU=1")

if fallos:
    sys.exit("Faltan o fallan: " + ", ".join(fallos))
print("Todo instalado.")
'@
    $validar | & $py -; Revisar "validacion"

    # 127.0.0.1 y no localhost: en PowerShell 5.1 localhost prueba IPv6 primero y tarda ~2 s por llamada
    function Responde($url) {
        try { Invoke-RestMethod $url -TimeoutSec 2 | Out-Null; return $true } catch { return $false }
    }

    Write-Host "== 4. servidor del modelo (servidor_alia.py, $modelo, puerto $puerto)" -ForegroundColor Cyan
    $proc = $null
    if (Responde "http://127.0.0.1:$puerto/v1/models") {
        Write-Host "   ya hay un servidor en el puerto ${puerto}: se usa ese" -ForegroundColor Yellow
    } else {
        $argumentos = @("src\servidor_alia.py", "--modelo", $modelo, "--puerto", $puerto)
        if ($env:ALIA_4BIT -eq "1") { $argumentos += "--4bit" }
        if ($sinGpu) { $argumentos += "--cpu" }
        $proc = Start-Process -FilePath $py -ArgumentList $argumentos -NoNewWindow -PassThru
        # espera a que responda /v1/models; si el proceso muere antes, falla
        while (-not (Responde "http://127.0.0.1:$puerto/v1/models")) {
            if ($proc.HasExited) { throw "El servidor termino antes de quedar listo (codigo $($proc.ExitCode))" }
            Start-Sleep -Seconds 2
        }
    }

    Write-Host "== listo: modelo en http://127.0.0.1:$puerto/v1" -ForegroundColor Green
    Write-Host "   Interfaz web, en otra terminal: powershell -ExecutionPolicy Bypass -File interfaz\levantar_interfaz.ps1"

    if ($proc) {
        try { $proc.WaitForExit() } finally { if (-not $proc.HasExited) { $proc.Kill() } }
    }
}

Levantar-Servidor
