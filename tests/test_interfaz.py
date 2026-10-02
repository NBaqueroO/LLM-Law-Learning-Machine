"""La interfaz (interfaz/app.py) con recuperación y LLM falsos: arma la página y responde."""
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("gradio")
from test_runner import ITEMS, grafo  # noqa: F401  (fixture con LLM falso)

RAIZ = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("app", RAIZ / "interfaz" / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def test_una_pregunta_y_lote(grafo, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app, "_grafo", grafo)
    md, ruta, pasajes, salida, traza = app.preguntar("Detectar solo", "", "¿Cuál es el término?", "Diez", "Cinco", "", "")
    assert "Respuesta: A" in md and "generate_mc" in ruta and "build_submission" in ruta
    assert json.loads(salida)["formato"] == "multiple_choice" and pasajes
    entrada = tmp_path / "p.jsonl"
    entrada.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in ITEMS[:2]), encoding="utf-8")
    class SinBarra:
        def tqdm(self, xs, **_):
            return xs
    archivo, aviso = app.lote(str(entrada), progreso=SinBarra())
    assert len(Path(archivo).read_text(encoding="utf-8").splitlines()) == 2 and "2 respuestas" in aviso
    app.construir()
