# levantar_interfaz.ps1
# Levanta la interfaz web (interfaz/app.py) conectada al servidor del modelo y abre el navegador.
# Requiere el entorno y el servidor que deja src\levantar_servidor.ps1 (en otra terminal).
#
# Uso (desde la raiz del repo):
#   powershell -ExecutionPolicy Bypass -File interfaz\levantar_interfaz.ps1
#
# Variables de entorno (todas opcionales):
#   LLM_MODELO   por defecto BSC-LT/salamandra-7b-instruct (el mismo del servidor)
#   PUERTO       puerto del servidor del modelo, por defecto 8000
#   UI_PUERTO    puerto de la interfaz, por defecto 8080
#   VENV         carpeta del entorno virtual, por defecto .venv

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Levantar-Interfaz {
    $modelo = if ($env:LLM_MODELO) { $env:LLM_MODELO } else { "BSC-LT/salamandra-7b-instruct" }
    $puerto = if ($env:PUERTO) { $env:PUERTO } else { "8000" }
    $uiPuerto = if ($env:UI_PUERTO) { $env:UI_PUERTO } else { "8080" }
    $venv = if ($env:VENV) { $env:VENV } else { ".venv" }

    $py = "$venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        throw "No existe $venv. Corre primero: powershell -ExecutionPolicy Bypass -File src\levantar_servidor.ps1"
    }
    # 127.0.0.1 y no localhost: en PowerShell 5.1 localhost prueba IPv6 primero y tarda ~2 s por llamada
    try { Invoke-RestMethod "http://127.0.0.1:$puerto/v1/models" -TimeoutSec 5 | Out-Null }
    catch { Write-Host "Aviso: el servidor del modelo no responde en el puerto $puerto; la interfaz carga, pero no podra responder." -ForegroundColor Yellow }

    # app.py lee la conexion al modelo de src/config.py al importarse: las variables van antes de arrancar
    $env:LLM_BASE_URL = "http://127.0.0.1:$puerto/v1"
    $env:LLM_MODELO = $modelo
    $env:METODO_SALIDA = "texto"

    Write-Host "== interfaz web en http://localhost:$uiPuerto (Ctrl+C para detenerla)" -ForegroundColor Cyan
    $proc = Start-Process -FilePath $py -ArgumentList @("interfaz\app.py", "--puerto", $uiPuerto) -NoNewWindow -PassThru
    while ($true) {
        if ($proc.HasExited) { throw "La interfaz termino antes de quedar lista (codigo $($proc.ExitCode))" }
        try { Invoke-RestMethod "http://127.0.0.1:$uiPuerto/api/estado" -TimeoutSec 2 | Out-Null; break }
        catch { Start-Sleep -Seconds 1 }
    }
    Start-Process "http://localhost:$uiPuerto"
    try { $proc.WaitForExit() } finally { if (-not $proc.HasExited) { $proc.Kill() } }
}

Levantar-Interfaz
