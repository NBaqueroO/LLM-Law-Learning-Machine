"""Paso 2: prueba manual contra el modelo REAL (Ollama o vLLM). No la corre pytest."""
import json
import time

from src.config import LLM_BASE_URL, LLM_MODELO, METODO_SALIDA
from src.graph import nodes

PASAJES = [
    {"chunk_id": "cgp-391", "texto": "[Código General del Proceso - Ley 1564 de 2012] Artículo 391. "
     "Demanda y contestación. El término para contestar la demanda será de diez (10) días."},
    {"chunk_id": "cco-899", "texto": "[Código de Comercio - Decreto 410 de 1971] Artículo 899. "
     "Será nulo absolutamente el negocio jurídico en los siguientes casos: 1) Cuando contraría una "
     "norma imperativa, salvo que la ley disponga otra cosa."},
    {"chunk_id": "cst-64", "texto": "[Código Sustantivo del Trabajo] Artículo 64. Terminación "
     "unilateral del contrato sin justa causa. En caso de terminación unilateral del contrato sin "
     "justa causa comprobada, el empleador deberá pagar al trabajador una indemnización."},
]

CASOS = [
    ("generate_mc", {"formato": "multiple_choice",
                     "pregunta": "¿Qué ocurre con el negocio jurídico que contraría una norma imperativa?",
                     "opciones": {"A": "Es nulo absolutamente", "B": "Es inexistente",
                                  "C": "Es anulable", "D": "Es plenamente válido"}}),
    ("generate_semi", {"formato": "semi_open",
                       "pregunta": "¿Cuál es el término para contestar la demanda en el proceso "
                                   "verbal sumario?"}),
    ("generate_open", {"formato": "open_ended",
                       "pregunta": "Rodolfo trabajó 3 años como cocinero con contrato a término "
                                   "indefinido. El 5 de mayo de 2025 el empleador lo despidió sin "
                                   "invocar ninguna causa. ¿Qué derechos tiene Rodolfo? ¿Qué debe "
                                   "pagar el empleador?"}),
]

if __name__ == "__main__":
    print(f"Servidor: {LLM_BASE_URL} | modelo: {LLM_MODELO} | salida: {METODO_SALIDA}\n")
    for nombre, base in CASOS:
        state = {"id": 1, "area": None, "opciones": {}, "pasajes": PASAJES, "traza": {}, **base}
        inicio = time.perf_counter()
        out = getattr(nodes, nombre)(state)
        segundos = time.perf_counter() - inicio
        print(f"== {nombre} ({segundos:.1f} s)")
        print(json.dumps({k: out.get(k) for k in ("salida", "usados", "traza")},
                         ensure_ascii=False, indent=2))
        print()
