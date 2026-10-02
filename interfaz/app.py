"""Interfaz gráfica: una pregunta a la vez (respuesta, citas, pasajes y el recorrido por el grafo)
o un lote JSONL completo. Usa exactamente el mismo grafo que src/main.py.

    python interfaz/app.py              # http://localhost:7860
    python interfaz/app.py --share      # en Colab: imprime un enlace público temporal

Necesita lo mismo que main.py: indices/ (corpus.db e índices) y el decoder corriendo (Ollama).
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gradio as gr

from src.runner import entrada, linea_de_respaldo

# Identidad visual: cambiar aquí los colores por los de la guía de marca de Software Colombia.
COLORES = {"primario": "#1f3a93", "acento": "#f2a900", "fondo": "#f6f7fb", "texto": "#1c1f26"}

CSS = f"""
:root {{ --primario: {COLORES['primario']}; --acento: {COLORES['acento']}; }}
.gradio-container {{ background: {COLORES['fondo']}; color: {COLORES['texto']}; }}
#titulo h1 {{ color: var(--primario); margin-bottom: 0; }}
#titulo p {{ margin-top: 4px; }}
button.primary {{ background: var(--primario) !important; border-color: var(--primario) !important; }}
.ruta code {{ background: transparent; color: var(--primario); }}
"""

FORMATOS = {"Detectar solo": None, "Selección múltiple": "multiple_choice",
            "Semiabierta": "semi_open", "Abierta": "open_ended"}

GRADIO_6 = int(gr.__version__.split(".")[0]) >= 6
_grafo = None


def grafo():
    global _grafo
    if _grafo is None:
        from src.graph.workflow import construir_grafo
        _grafo = construir_grafo()
    return _grafo


def correr_una(item: dict) -> tuple[dict, list[str], dict]:
    """Corre el grafo nodo por nodo: (submission, ruta, traza)."""
    estado, ruta = entrada(item), []
    for paso in grafo().stream(estado, stream_mode="updates"):
        for nodo, cambios in paso.items():
            ruta.append(nodo)
            estado = {**estado, **(cambios or {})}
    return estado.get("submission") or linea_de_respaldo(item), ruta, estado.get("traza") or {}


def respuesta_md(sub: dict) -> str:
    if sub.get("abstencion"):
        return "### El sistema se abstiene\nNo encontró fundamento suficiente en el corpus para responder."
    f = sub["formato"]
    if f == "multiple_choice":
        descarte = "\n".join(f"- **{l}**: {m}" for l, m in sorted(sub.get("descarte_opciones", {}).items()))
        return (f"### Respuesta: {sub.get('respuesta_correcta') or '?'}\n\n{sub.get('justificacion', '')}"
                f"\n\n**Por qué no las demás**\n\n{descarte}")
    if f == "semi_open":
        clave = ", ".join(sub.get("palabras_clave", []))
        return (f"### Respuesta\n{sub.get('respuesta', '')}\n\n**Referencia legal:** {sub.get('referencia_legal', '')}"
                f"\n\n**Palabras clave:** {clave}")
    return "\n\n".join(f"### {t}\n{sub.get(c, '')}" for c, t in
                       (("marco_normativo", "Marco normativo"), ("analisis", "Análisis"),
                        ("jurisprudencia", "Jurisprudencia"), ("conclusion", "Conclusión")))


def preguntar(formato, area, pregunta, a, b, c, d):
    if not (pregunta or "").strip():
        raise gr.Error("Escribe la pregunta.")
    opciones = {l: t.strip() for l, t in zip("ABCD", (a, b, c, d)) if (t or "").strip()}
    formato = FORMATOS[formato] or ("multiple_choice" if len(opciones) >= 2 else None)
    item = {"id": 0, "formato": formato, "area": area or None, "pregunta": pregunta.strip(), "opciones": opciones}
    t = time.time()
    sub, ruta, traza = correr_una(item)
    pasajes = [[i + 1, p.get("doc_id", ""), round(p.get("score", 0) or 0, 3), (p.get("texto") or "")[:400]]
               for i, p in enumerate(sub.get("pasajes_recuperados", []))]
    recorrido = (f"**Recorrido ({time.time() - t:.1f} s):** "
                 + " → ".join(f"`{n}`" for n in ruta))
    return respuesta_md(sub), recorrido, pasajes, json.dumps(sub, ensure_ascii=False, indent=1), traza


def lote(archivo, progreso=gr.Progress()):
    if archivo is None:
        raise gr.Error("Sube un JSONL de preguntas (una por línea, como data/sample_50.jsonl).")
    ruta = archivo if isinstance(archivo, str) else archivo.name
    items = [json.loads(l) for l in Path(ruta).read_text(encoding="utf-8").splitlines() if l.strip()]
    salida = Path(tempfile.mkdtemp()) / "submissions.jsonl"
    with open(salida, "w", encoding="utf-8") as f:
        for it in progreso.tqdm(items, desc="respondiendo"):
            try:
                sub = correr_una(it)[0]
            except Exception:   # una pregunta que falla no tumba el lote
                sub = linea_de_respaldo(it)
            f.write(json.dumps(sub, ensure_ascii=False) + "\n")
    return str(salida), f"{len(items)} respuestas listas."


def construir() -> gr.Blocks:
    # Gradio 6 recibe el CSS en launch(); las versiones anteriores, en Blocks()
    extra = {} if GRADIO_6 else {"css": CSS}
    with gr.Blocks(title="LLM Law Learning Machine", **extra) as app:
        gr.Markdown("# LLM Law Learning Machine\nPreguntas de derecho colombiano respondidas con un corpus "
                    "jurídico propio y un modelo abierto. Cada cita sale de los pasajes recuperados.",
                    elem_id="titulo")
        with gr.Tab("Una pregunta"):
            with gr.Row():
                formato = gr.Dropdown(list(FORMATOS), value="Detectar solo", label="Formato")
                area = gr.Textbox(label="Área (opcional)", placeholder="Derecho laboral")
            pregunta = gr.Textbox(label="Pregunta", lines=3)
            with gr.Accordion("Opciones (solo selección múltiple)", open=False):
                ops = [gr.Textbox(label=l, lines=1) for l in "ABCD"]
            boton = gr.Button("Responder", variant="primary")
            respuesta = gr.Markdown()
            recorrido = gr.Markdown(elem_classes="ruta")
            pasajes = gr.Dataframe(headers=["#", "doc_id", "score", "texto"], label="Pasajes recuperados (top 10)",
                                   wrap=True)
            with gr.Accordion("Salida JSON (esquema oficial) y traza", open=False):
                salida_json = gr.Code(language="json", label="submission")
                traza = gr.JSON(label="traza")
            boton.click(preguntar, [formato, area, pregunta, *ops],
                        [respuesta, recorrido, pasajes, salida_json, traza])
        with gr.Tab("Lote JSONL"):
            gr.Markdown("Sube las preguntas (formato de `data/sample_50.jsonl` o `test_992.jsonl`) y descarga "
                        "`submissions.jsonl`. Para las 992 es mejor `python src/main.py --split test` (paralelo y "
                        "reanudable).")
            archivo = gr.File(file_types=[".jsonl"], label="Preguntas")
            correr = gr.Button("Responder todo", variant="primary")
            descarga = gr.File(label="submissions.jsonl")
            estado = gr.Markdown()
            correr.click(lote, archivo, [descarga, estado])
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--share", action="store_true", help="enlace público temporal (Colab)")
    ap.add_argument("--puerto", type=int, default=7860)
    a = ap.parse_args()
    construir().queue().launch(share=a.share, server_port=a.puerto, **({"css": CSS} if GRADIO_6 else {}))
