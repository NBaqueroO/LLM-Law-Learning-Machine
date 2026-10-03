"""Interfaz web
"""
from __future__ import annotations

import argparse
import itertools
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.config import LLM_MODELO

ESTATICOS = Path(__file__).resolve().parent / "src" / "ui" / "static"
log = logging.getLogger("app")


class Consulta(BaseModel):
    pregunta: str = Field(min_length=3, max_length=8000)
    formato: Optional[str] = None        
    opciones: Optional[dict[str, str]] = None
    area: Optional[str] = None


class Sistema:
    """El grafo compilado y su estado de carga."""

    def __init__(self):
        self.grafo = None
        self.estado = "cargando"
        self.detalle = ""
        self.info: dict = {}
        self.ids = itertools.count(1)

    def cargar(self, demo: bool, sin_denso: bool) -> None:
        try:
            t = time.time()
            if demo:
                from src.ui.servicio import GrafoDemo
                self.grafo = GrafoDemo()
                self.info = {"modo": "demo", "modelo": "sin modelo (demostración)"}
            else:
                from src.graph.workflow import construir_grafo
                from src.retrieval.resources import Recursos
                recursos = Recursos.cargar(usar_denso=not sin_denso)
                self.grafo = construir_grafo(recursos)
                self.info = {"modo": "real", "modelo": LLM_MODELO, "hibrido": recursos.hibrido,
                             "reranker": recursos.reranker.modelo if recursos.reranker else None}
            self.info["carga_s"] = round(time.time() - t, 1)
            self.estado = "listo"
            log.info("recursos listos en %.0f s", time.time() - t)
        except Exception as e:  
            log.exception("no se pudieron cargar los recursos")
            self.estado, self.detalle = "error", f"{type(e).__name__}: {e}"


def crear_app(demo: bool = False, sin_denso: bool = False, cargar_en_hilo: bool = True) -> FastAPI:
    sistema = Sistema()
    app = FastAPI(title="LLM Law Learning Machine", docs_url="/api/docs", redoc_url=None)
    app.state.sistema = sistema
    if cargar_en_hilo:
        threading.Thread(target=sistema.cargar, args=(demo, sin_denso), daemon=True).start()
    else:
        sistema.cargar(demo, sin_denso)

    @app.get("/api/estado")
    def estado():
        return {"estado": sistema.estado, "detalle": sistema.detalle, **sistema.info}

    @app.post("/api/consultar")
    def consultar(c: Consulta): 
        if sistema.estado != "listo":
            raise HTTPException(503, sistema.detalle or "El sistema todavía está cargando los índices.")
        from src.ui.servicio import consultar as correr
        try:
            return correr(sistema.grafo, c.pregunta.strip(), c.formato, c.opciones,
                          id_=next(sistema.ids), area=c.area)
        except Exception as e:
            log.exception("falló la consulta")
            raise HTTPException(500, f"Falló la consulta: {type(e).__name__}: {e}")

    @app.get("/")
    def inicio():
        return FileResponse(ESTATICOS / "index.html")

    app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")
    return app


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--puerto", type=int, default=8000)
    ap.add_argument("--sin-denso", action="store_true", help="solo BM25 (sin GPU ni dense.faiss)")
    ap.add_argument("--demo", action="store_true", help="sin índices ni LLM: respuestas de ejemplo")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    import uvicorn
    print(f"Interfaz en http://{args.host}:{args.puerto}" + ("  (modo demostración)" if args.demo else ""))
    uvicorn.run(crear_app(demo=args.demo, sin_denso=args.sin_denso), host=args.host, port=args.puerto)


if __name__ == "__main__":
    main()
