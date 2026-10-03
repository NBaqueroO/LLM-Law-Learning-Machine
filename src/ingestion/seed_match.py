"""Cruza las entradas de seed_targets.json con los documentos de lexis (tipo + numero + anio).

Las leyes/decretos/acuerdos del seed ya traen numero y anio en 'canonico'. Los codigos y
estatutos vienen sin numero (ej. ["codigo_general_proceso", null, null]), asi que aqui se
dice a que norma corresponde cada uno. Si agregan una entrada nueva sin numero al seed,
agreguenla tambien a CODIGOS.
"""
import json
import re
from pathlib import Path

# canonico[0] -> (tipo en lexis, numero, anio)
CODIGOS = {
    "codigo_general_proceso": ("LEY", "1564", "2012"),
    "codigo_sustantivo_trabajo": ("DECRETO", "2663", "1950"),
    "estatuto_tributario": ("DECRETO", "624", "1989"),
    "estatuto_consumidor": ("LEY", "1480", "2011"),
    "codigo_infancia": ("LEY", "1098", "2006"),
    "codigo_disciplinario": ("LEY", "1952", "2019"),
    "codigo_nacional_policia": ("LEY", "1801", "2016"),
    # SIN CONFIRMAR el valor de tipo en lexis para normas andinas; si existe (p.ej. "DECISION"),
    # hay que listarlo en --tipos para que entre
    "decision_andina_486": ("DECISION", "486", "2000"),
}
TIPO_LEXIS = {"ley": "LEY", "decreto": "DECRETO", "acuerdo": "ACUERDO"}

# Decretos clave que NO vienen en seed_targets.json pero que el banco usa (salen en los
# legal_basis de la muestra o son codigos). Se tratan como parte del seed con
# items_del_banco = 0, para que tengan areas del banco y no los saque --sin-decretos-extra.
DECRETOS_CLAVE = {
    ("410", "1971"): ("Codigo de Comercio", ["Derecho comercial y sociedades"]),
    ("2591", "1991"): ("Decreto 2591 de 1991 (accion de tutela)", ["Derecho constitucional", "Derecho procesal"]),
    ("306", "1992"): ("Decreto 306 de 1992 (reglamenta la tutela)", ["Derecho constitucional", "Derecho procesal"]),
    ("2158", "1948"): ("Codigo Procesal del Trabajo y de la Seguridad Social", ["Derecho laboral", "Derecho procesal"]),
    ("3743", "1950"): ("Decreto 3743 de 1950 (edicion oficial del CST)", ["Derecho laboral"]),
    ("1260", "1970"): ("Decreto 1260 de 1970 (estado civil)", ["Derecho de familia", "Derecho civil"]),
    ("019", "2012"): ("Decreto Ley 019 de 2012 (antitramites)", ["Derecho administrativo"]),
    ("2150", "1995"): ("Decreto Ley 2150 de 1995 (antitramites)", ["Derecho administrativo"]),
    ("2106", "2019"): ("Decreto Ley 2106 de 2019 (simplificacion de tramites)", ["Derecho administrativo"]),
    # codigos y estatutos que son decretos (los que son LEY ya entran completos al indice)
    ("2241", "1986"): ("Codigo Electoral", ["Derecho constitucional", "Derecho administrativo"]),
    ("1222", "1986"): ("Codigo de Regimen Departamental", ["Derecho administrativo"]),
    ("1333", "1986"): ("Codigo de Regimen Municipal", ["Derecho administrativo"]),
    ("1295", "1994"): ("Decreto Ley 1295 de 1994 (sistema de riesgos laborales)", ["Derecho laboral"]),
    ("111", "1996"): ("Estatuto Organico del Presupuesto", ["Derecho administrativo", "Derecho constitucional"]),
    ("1421", "1993"): ("Estatuto Organico de Bogota", ["Derecho administrativo"]),
    ("403", "2020"): ("Decreto Ley 403 de 2020 (control fiscal)", ["Derecho administrativo"]),
    # Decretos Unicos Reglamentarios (DUR): en lexis vienen con tipo DECRETO, no "DECRETO ÚNICO".
    # Se deja fuera el de Ambiente (1076/2015) porque el banco no cubre derecho ambiental.
    ("1066", "2015"): ("DUR Sector Interior", ["Derecho administrativo", "Derecho de los mercados [competencia, consumidor, datos personales y propiedad intelectual]"]),
    ("1067", "2015"): ("DUR Sector Relaciones Exteriores", ["Derecho administrativo"]),
    ("1068", "2015"): ("DUR Sector Hacienda y Credito Publico", ["Derecho administrativo", "Derecho tributario"]),
    ("1069", "2015"): ("DUR Sector Justicia y del Derecho", ["Derecho administrativo", "Derecho procesal"]),
    ("1070", "2015"): ("DUR Sector Defensa", ["Derecho administrativo"]),
    ("1071", "2015"): ("DUR Sector Agropecuario", ["Derecho administrativo"]),
    ("1072", "2015"): ("DUR Sector Trabajo", ["Derecho laboral"]),
    ("1073", "2015"): ("DUR Sector Minas y Energia", ["Derecho administrativo"]),
    ("1074", "2015"): ("DUR Sector Comercio, Industria y Turismo", ["Derecho comercial y sociedades", "Derecho de los mercados [competencia, consumidor, datos personales y propiedad intelectual]"]),
    ("1075", "2015"): ("DUR Sector Educacion", ["Derecho administrativo"]),
    ("1077", "2015"): ("DUR Sector Vivienda, Ciudad y Territorio", ["Derecho administrativo", "Derecho civil"]),
    ("1078", "2015"): ("DUR Sector TIC", ["Derecho administrativo"]),
    ("1079", "2015"): ("DUR Sector Transporte", ["Derecho administrativo"]),
    ("1080", "2015"): ("DUR Sector Cultura", ["Derecho administrativo"]),
    ("1081", "2015"): ("DUR Sector Presidencia", ["Derecho administrativo"]),
    ("1083", "2015"): ("DUR Sector Funcion Publica", ["Derecho administrativo"]),
    ("1084", "2015"): ("DUR Sector Inclusion Social", ["Derecho administrativo"]),
    ("1085", "2015"): ("DUR Sector Deporte", ["Derecho administrativo"]),
    ("780", "2016"): ("DUR Sector Salud y Proteccion Social", ["Derecho administrativo", "Derecho constitucional"]),
    ("1625", "2016"): ("DUR en materia tributaria", ["Derecho tributario"]),
    ("1833", "2016"): ("Compilacion Sistema General de Pensiones", ["Derecho laboral"]),
    ("2420", "2015"): ("DUR Normas de Contabilidad e Informacion Financiera", ["Derecho comercial y sociedades"]),
    ("2555", "2010"): ("Decreto 2555 de 2010 (compilacion sector financiero)", ["Derecho comercial y sociedades"]),
}


def _num(x) -> str:
    """'0080' -> '80', 80 -> '80', '02' -> '2'."""
    s = re.sub(r"\s+", "", str(x or "")).upper()
    return s.lstrip("0") or s


def _tipo(tipo) -> str:
    """'DECRETO LEY', 'DECRETO LEGISLATIVO', 'DECRETO ÚNICO'... -> 'DECRETO'. Los decretos
    comparten una sola numeracion por anio, asi que no hay choque al unificarlos."""
    t = str(tipo or "").strip().upper()
    return "DECRETO" if t.startswith("DECRETO") else t


def clave(tipo, numero, anio):
    return (_tipo(tipo), _num(numero), str(anio or "").strip())


def clave_de_entrada(entrada: dict):
    """Clave lexis para una entrada del seed, o None si no aplica (jurisprudencia)."""
    tipo, numero, anio = entrada["canonico"]
    if tipo == "constitucion":
        return ("CONSTITUCION POLITICA", "", "1991")
    if tipo in CODIGOS:
        return clave(*CODIGOS[tipo])
    if tipo in TIPO_LEXIS and numero and anio:
        return clave(TIPO_LEXIS[tipo], numero, anio)
    return None


def clave_de_lexis(meta: dict):
    if str(meta.get("tipo", "")).upper() == "CONSTITUCION POLITICA":
        return ("CONSTITUCION POLITICA", "", str(meta.get("anio") or "").strip())
    return clave(meta.get("tipo"), meta.get("numero"), meta.get("anio"))


def cargar(path) -> dict:
    """{clave_lexis: entrada_del_seed}. Si no hay seed_targets, devuelve {}."""
    if not path or not Path(path).exists():
        return {}
    seed = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for e in seed["documentos"]:
        k = clave_de_entrada(e)
        if k:
            out[k] = e
    for (numero, anio), (norma, areas) in DECRETOS_CLAVE.items():
        out.setdefault(clave("DECRETO", numero, anio), {"norma": norma, "areas": areas, "items_del_banco": 0})
    return out
