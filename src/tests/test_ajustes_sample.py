"""Ajustes que salieron de revisar sample_50: área en la 1.ª búsqueda y citar las normas recuperadas."""
from src.graph import nodes
from src.guards.citation_builder import construir_citas

P = [{"texto": "[Sentencia C-355 de 2006] La Corte...", "encabezado": "Sentencia C-355 de 2006"},
     {"texto": "[Constitución Política de 1991] Artículo 29. El debido proceso...",
      "encabezado": "Constitución Política de 1991", "articulo": "29"},
     {"texto": "[Código General del Proceso - Ley 1564 de 2012] Artículo 6...",
      "encabezado": "Código General del Proceso - Ley 1564 de 2012", "articulo": "6"}]


def test_cita_las_normas_recuperadas_que_el_modelo_no_nombro():
    salida = {"respuesta": "Sí.", "referencia_legal": "Sentencia C-355 de 2006"}
    solo_usados = construir_citas("semi_open", salida, P, [0])
    con_todas = construir_citas("semi_open", salida, P, [0], citar_recuperadas=True)
    assert "Constitución" not in solo_usados["referencia_legal"]
    assert con_todas["referencia_legal"] == ("Sentencia C-355 de 2006; Constitución Política de 1991, artículo 29; "
                                             "Código General del Proceso - Ley 1564 de 2012, artículo 6")


def test_no_agrega_sentencias_que_el_modelo_no_uso():
    salida = {"justificacion": "Por el debido proceso."}
    out = construir_citas("multiple_choice", salida, P, [1], citar_recuperadas=True)
    assert "C-355" not in out["justificacion"] and "Ley 1564 de 2012" in out["justificacion"]


def test_el_area_entra_a_la_consulta_pero_no_a_los_cuerpos_esperados(monkeypatch):
    monkeypatch.setattr(nodes, "AREA_EN_CONSULTA", True)
    area = "Derecho de los mercados [competencia, consumidor, datos personales y propiedad intelectual]"
    r = nodes.classify({"id": 1, "formato": "semi_open", "area": area,
                        "pregunta": "¿Es abusiva esta cláusula de un contrato de seguro?"})
    assert r["consulta"].endswith("Estatuto del Consumidor Ley 1581 de 2012")
    assert r["cuerpos_esperados"] == []


def test_prompt_evaluacion_se_suma_al_sistema(monkeypatch):
    import importlib
    import src.config
    import src.generation.prompts as prompts
    monkeypatch.setenv("PROMPT_EVALUACION", "1")
    importlib.reload(src.config)
    importlib.reload(prompts)
    assert "# CÓMO SE CALIFICA TU RESPUESTA" in prompts.SISTEMA
    monkeypatch.setenv("PROMPT_EVALUACION", "0")
    importlib.reload(src.config)
    importlib.reload(prompts)
    assert "CÓMO SE CALIFICA" not in prompts.SISTEMA
