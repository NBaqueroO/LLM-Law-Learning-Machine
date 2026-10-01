#!/usr/bin/env python3
"""Escaneo de huecos: revisa, area por area del banco, si las normas que un abogado esperaria
encontrar (codigos, estatutos, reformas recientes, decretos unicos) estan en corpus.db y en el
indice. No descarga nada.

  python auditar_corpus.py                      # tabla por area + lista de lo que falta
  python auditar_corpus.py --index data/index_sin_sentencias
  python auditar_corpus.py --solo-faltantes

Estados:
  OK        en corpus.db y en el indice
  NO-INDICE en corpus.db pero fuera del indice (p.ej. decreto que no esta en DECRETOS_CLAVE)
  FALTA     no esta en corpus.db
Y al final lista las que parecen INCOMPLETAS: menos de la mitad de los articulos que lexis
dice que tiene la norma (campo noart), o 10 chunks o menos.

La lista NORMAS_CLAVE es editable: si falta algo que ustedes saben que se pregunta, agreguenlo.
Se armo a mano (no sale del banco de preguntas).
"""
import argparse, json, sqlite3, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from citas import clave_doc, CONSTITUCION

CP = ("CONSTITUCION", "", "")
NORMAS_CLAVE = {
    "Derecho constitucional": [
        CP + ("Constitucion Politica",),
        ("DECRETO", "2591", "1991", "Accion de tutela"),
        ("DECRETO", "2067", "1991", "Procedimiento ante la Corte Constitucional"),
        ("LEY", "5", "1992", "Reglamento del Congreso"),
        ("LEY", "134", "1994", "Mecanismos de participacion"),
        ("LEY", "1757", "2015", "Participacion democratica"),
        ("LEY", "270", "1996", "Estatutaria de la administracion de justicia"),
        ("LEY", "472", "1998", "Acciones populares y de grupo"),
        ("LEY", "393", "1997", "Accion de cumplimiento"),
        ("LEY", "1095", "2006", "Habeas corpus"),
        ("LEY", "1712", "2014", "Transparencia y acceso a la informacion"),
        ("LEY", "1755", "2015", "Derecho de peticion"),
        ("LEY", "1448", "2011", "Victimas y restitucion de tierras"),
        ("LEY", "1751", "2015", "Estatutaria de salud"),
        ("LEY", "1618", "2013", "Derechos de personas con discapacidad"),
        ("LEY", "1996", "2019", "Capacidad legal de personas con discapacidad"),
        ("DECRETO", "2241", "1986", "Codigo Electoral"),
        ("LEY", "1801", "2016", "Codigo Nacional de Policia"),
    ],
    "Derecho administrativo": [
        ("LEY", "1437", "2011", "CPACA"),
        ("LEY", "2080", "2021", "Reforma al CPACA"),
        ("LEY", "80", "1993", "Estatuto de contratacion estatal"),
        ("LEY", "1150", "2007", "Reforma a la contratacion estatal"),
        ("LEY", "1474", "2011", "Estatuto anticorrupcion"),
        ("LEY", "2195", "2022", "Transparencia y lucha contra la corrupcion"),
        ("LEY", "1952", "2019", "Codigo General Disciplinario"),
        ("LEY", "2094", "2021", "Reforma al Codigo Disciplinario"),
        ("LEY", "909", "2004", "Empleo publico y carrera"),
        ("LEY", "489", "1998", "Organizacion de la administracion"),
        ("LEY", "136", "1994", "Regimen municipal"),
        ("LEY", "1551", "2012", "Modernizacion de municipios"),
        ("LEY", "610", "2000", "Responsabilidad fiscal"),
        ("LEY", "678", "2001", "Accion de repeticion"),
        ("LEY", "1882", "2018", "Reforma contratacion (pliegos tipo)"),
        ("DECRETO", "1082", "2015", "DUR Planeacion (reglamento de contratacion)"),
        ("DECRETO", "1083", "2015", "DUR Funcion Publica"),
        ("DECRETO", "111", "1996", "Estatuto Organico del Presupuesto"),
        ("DECRETO", "403", "2020", "Control fiscal"),
        ("DECRETO", "019", "2012", "Antitramites"),
        ("LEY", "99", "1993", "Sistema Nacional Ambiental"),
        ("LEY", "1333", "2009", "Sancionatorio ambiental"),
    ],
    "Derecho penal": [
        ("LEY", "599", "2000", "Codigo Penal"),
        ("LEY", "906", "2004", "Codigo de Procedimiento Penal"),
        ("LEY", "600", "2000", "Procedimiento penal anterior"),
        ("LEY", "890", "2004", "Aumento de penas"),
        ("LEY", "1453", "2011", "Seguridad ciudadana"),
        ("LEY", "1709", "2014", "Reforma penitenciaria"),
        ("LEY", "65", "1993", "Codigo Penitenciario"),
        ("LEY", "1257", "2008", "Violencia contra la mujer"),
        ("LEY", "1761", "2015", "Feminicidio"),
        ("LEY", "1826", "2017", "Procedimiento abreviado y acusador privado"),
        ("LEY", "1098", "2006", "Responsabilidad penal adolescente (Infancia)"),
        ("LEY", "2197", "2022", "Seguridad ciudadana 2022"),
    ],
    "Derecho procesal": [
        ("LEY", "1564", "2012", "Codigo General del Proceso"),
        ("LEY", "2213", "2022", "Virtualidad en la justicia"),
        ("LEY", "1563", "2012", "Estatuto de arbitraje"),
        ("LEY", "2220", "2022", "Estatuto de conciliacion"),
        ("DECRETO", "2158", "1948", "Codigo Procesal del Trabajo"),
        ("LEY", "712", "2001", "Reforma al CPT"),
        ("LEY", "1395", "2010", "Descongestion judicial"),
        ("DECRETO", "1069", "2015", "DUR Justicia"),
    ],
    "Derecho comercial y sociedades": [
        ("DECRETO", "410", "1971", "Codigo de Comercio"),
        ("LEY", "222", "1995", "Reforma al Codigo de Comercio (sociedades)"),
        ("LEY", "1258", "2008", "Sociedad por acciones simplificada"),
        ("LEY", "1116", "2006", "Insolvencia empresarial"),
        ("LEY", "2069", "2020", "Emprendimiento"),
        ("LEY", "527", "1999", "Comercio electronico y firma digital"),
        ("LEY", "1676", "2013", "Garantias mobiliarias"),
        ("DECRETO", "663", "1993", "Estatuto Organico del Sistema Financiero"),
        ("LEY", "964", "2005", "Mercado de valores"),
        ("DECRETO", "2555", "2010", "Compilacion sector financiero"),
        ("LEY", "1314", "2009", "Contabilidad e informacion financiera"),
        ("DECRETO", "2420", "2015", "DUR contable"),
        ("LEY", "1231", "2008", "Factura como titulo valor"),
        ("DECRETO", "1074", "2015", "DUR Comercio, Industria y Turismo"),
        ("LEY", "1429", "2010", "Formalizacion y generacion de empleo"),
    ],
    "Derecho civil": [
        ("LEY", "84", "1873", "Codigo Civil"),
        ("LEY", "57", "1887", "Adopcion de codigos"),
        ("DECRETO", "960", "1970", "Estatuto del Notariado"),
        ("LEY", "1579", "2012", "Estatuto de registro de instrumentos publicos"),
        ("LEY", "820", "2003", "Arrendamiento de vivienda urbana"),
        ("LEY", "675", "2001", "Propiedad horizontal"),
        ("LEY", "791", "2002", "Reduccion de terminos de prescripcion"),
        ("DECRETO", "1260", "1970", "Estatuto del registro del estado civil"),
        ("DECRETO", "1077", "2015", "DUR Vivienda"),
    ],
    "Derecho de familia": [
        ("LEY", "1098", "2006", "Codigo de la Infancia y la Adolescencia"),
        ("LEY", "54", "1990", "Union marital de hecho"),
        ("LEY", "979", "2005", "Reforma union marital"),
        ("LEY", "25", "1992", "Divorcio del matrimonio religioso"),
        ("LEY", "1361", "2009", "Proteccion integral de la familia"),
        ("LEY", "294", "1996", "Violencia intrafamiliar"),
        ("LEY", "575", "2000", "Reforma violencia intrafamiliar"),
        ("LEY", "721", "2001", "Filiacion (prueba de ADN)"),
        ("LEY", "1060", "2006", "Impugnacion de paternidad"),
        ("LEY", "1878", "2018", "Restablecimiento de derechos"),
        ("LEY", "2097", "2021", "Registro de deudores alimentarios (REDAM)"),
        ("LEY", "2126", "2021", "Comisarias de familia"),
    ],
    "Derecho tributario": [
        ("DECRETO", "624", "1989", "Estatuto Tributario"),
        ("DECRETO", "1625", "2016", "DUR tributario"),
        ("LEY", "1607", "2012", "Reforma tributaria 2012"),
        ("LEY", "1819", "2016", "Reforma tributaria 2016"),
        ("LEY", "2010", "2019", "Ley de crecimiento economico"),
        ("LEY", "2155", "2021", "Ley de inversion social"),
        ("LEY", "2277", "2022", "Reforma tributaria 2022"),
        ("LEY", "788", "2002", "Reforma tributaria 2002"),
        ("LEY", "14", "1983", "Fortalecimiento de fiscos territoriales (ICA)"),
        ("DECRETO", "1333", "1986", "Codigo de Regimen Municipal"),
        ("LEY", "1066", "2006", "Cartera publica y cobro coactivo"),
    ],
    "Derecho laboral": [
        ("DECRETO", "2663", "1950", "Codigo Sustantivo del Trabajo"),
        ("LEY", "100", "1993", "Sistema de seguridad social"),
        ("LEY", "797", "2003", "Reforma pensional 2003"),
        ("LEY", "2381", "2024", "Reforma pensional 2024"),
        ("LEY", "2466", "2025", "Reforma laboral 2025"),
        ("LEY", "789", "2002", "Reforma laboral 2002"),
        ("LEY", "50", "1990", "Reforma laboral 1990"),
        ("LEY", "1010", "2006", "Acoso laboral"),
        ("LEY", "1562", "2012", "Riesgos laborales"),
        ("DECRETO", "1295", "1994", "Sistema de riesgos profesionales"),
        ("LEY", "776", "2002", "Prestaciones de riesgos laborales"),
        ("LEY", "2101", "2021", "Reduccion de la jornada laboral"),
        ("LEY", "1822", "2017", "Licencia de maternidad"),
        ("LEY", "2114", "2021", "Licencia de paternidad"),
        ("LEY", "1636", "2013", "Mecanismo de proteccion al cesante"),
        ("LEY", "1221", "2008", "Teletrabajo"),
        ("LEY", "2088", "2021", "Trabajo en casa"),
        ("LEY", "2121", "2021", "Trabajo remoto"),
        ("DECRETO", "1072", "2015", "DUR Trabajo"),
        ("DECRETO", "1833", "2016", "Compilacion de pensiones"),
    ],
    "Derecho de los mercados [competencia, consumidor, datos personales y propiedad intelectual]": [
        ("LEY", "1480", "2011", "Estatuto del Consumidor"),
        ("LEY", "1340", "2009", "Proteccion de la competencia"),
        ("DECRETO", "2153", "1992", "Reestructuracion SIC (practicas restrictivas)"),
        ("LEY", "155", "1959", "Practicas comerciales restrictivas"),
        ("LEY", "256", "1996", "Competencia desleal"),
        ("LEY", "1581", "2012", "Proteccion de datos personales"),
        ("LEY", "1266", "2008", "Habeas data financiero"),
        ("LEY", "2157", "2021", "Reforma habeas data (borron y cuenta nueva)"),
        ("LEY", "2300", "2023", "Contacto a consumidores (dejen de fregar)"),
        ("DECISION", "486", "2000", "Regimen comun de propiedad industrial (CAN)"),
        ("DECISION", "351", "1993", "Regimen comun de derecho de autor (CAN)"),
        ("LEY", "23", "1982", "Derechos de autor"),
        ("LEY", "44", "1993", "Reforma derechos de autor"),
        ("LEY", "1915", "2018", "Reforma derechos de autor 2018"),
        ("LEY", "1341", "2009", "Ley TIC"),
        ("LEY", "1978", "2019", "Modernizacion sector TIC"),
    ],
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/corpus.db")
    ap.add_argument("--index", default="data/index_sin_sentencias")
    ap.add_argument("--solo-faltantes", action="store_true")
    a = ap.parse_args()

    con = sqlite3.connect(a.db)
    en_db = defaultdict(list)  # clave -> [(doc_id, n_chunks)]
    tam = {}  # doc_id -> (caracteres, chunk mas largo)
    for doc_id, tipo, numero, anio, n, chars, mayor in con.execute(
            "SELECT d.doc_id, d.tipo, d.numero, d.anio, COUNT(c.chunk_id), SUM(LENGTH(c.texto)), MAX(LENGTH(c.texto)) "
            "FROM documentos d JOIN chunks c ON c.doc_id = d.doc_id WHERE d.estado = 'ok' GROUP BY d.doc_id"):
        if tipo:
            en_db[clave_doc(tipo, numero, anio)].append((doc_id, n))
            tam[doc_id] = (chars, mayor)
    noart = {}
    listado = Path(a.db).parent / "seed" / "lexis_listado.jsonl"
    if listado.exists():
        for l in listado.read_text(encoding="utf-8").splitlines():
            f = json.loads(l)
            if f.get("noart"):
                noart[f"lexis_{f['id']}"] = int(f["noart"])
    ruta = Path(a.index) / "chunk_ids.json"
    indexados = {c.split("#", 1)[0] for c in json.loads(ruta.read_text(encoding="utf-8"))} if ruta.exists() else set()
    if not indexados:
        print(f"AVISO: no encontre {ruta}; todo saldra como NO-INDICE")

    faltan, fuera, cortas = [], [], []
    totales = defaultdict(lambda: [0, 0, 0])
    for area, normas in NORMAS_CLAVE.items():
        if not a.solo_faltantes:
            print(f"\n== {area} ==")
        for tipo, numero, anio, nombre in normas:
            k = CONSTITUCION if tipo == "CONSTITUCION" else clave_doc(tipo, numero, anio)
            docs = en_db.get(k, [])
            if not docs:
                estado = "FALTA"
                faltan.append((area, tipo, numero, anio, nombre))
            elif any(d in indexados for d, _ in docs):
                estado = "OK"
            else:
                estado = "NO-INDICE"
                fuera.append((area, tipo, numero, anio, nombre, docs[0][0]))
            totales[area][("OK", "NO-INDICE", "FALTA").index(estado)] += 1
            if docs:
                doc_id, n = max(docs, key=lambda x: x[1])
                esperado = noart.get(doc_id)
                if (esperado and n < 0.5 * esperado) or n <= 10:
                    cortas.append((tipo, numero, anio, nombre, doc_id, n, esperado, *tam[doc_id]))
            if not a.solo_faltantes:
                ref = f"{tipo} {numero} de {anio}".strip() if tipo != "CONSTITUCION" else "Constitucion"
                chunks = max((n for _, n in docs), default=0)
                print(f"  {estado:<9} {ref:<24} {chunks:>6} chunks  {nombre}")

    print("\nResumen por area (OK / fuera del indice / falta):")
    for area, (ok, ni, fa) in totales.items():
        print(f"  {ok:>3} / {ni:>2} / {fa:>2}   {area[:60]}")
    if fuera:
        print(f"\nEn corpus.db pero FUERA del indice ({len(fuera)}): agregarlos a seed_match.DECRETOS_CLAVE")
        for area, tipo, numero, anio, nombre, doc_id in fuera:
            print(f"  {tipo} {numero} de {anio}  ({nombre})  -> {doc_id}")
    if cortas:
        print(f"\nPosiblemente INCOMPLETAS ({len(cortas)}): pocos chunks para lo que es la norma")
        for tipo, numero, anio, nombre, doc_id, n, esperado, chars, mayor in cortas:
            print(f"  {tipo} {numero} de {anio}  ({nombre})  -> {doc_id}: {n} chunks"
                  f"{f' de ~{esperado} articulos segun lexis' if esperado else ''}, {chars} caracteres, "
                  f"el mas largo {mayor}")
    if faltan:
        print(f"\nFALTAN en corpus.db ({len(faltan)}):")
        for area, tipo, numero, anio, nombre in faltan:
            como = (f"python bajar_senado.py ley_{numero}_{anio}" if tipo == "LEY" and int(anio) >= 2020
                    else f"python buscar_lexis.py --tipo {tipo} --numero {numero} --anio {anio} --bajar 1")
            print(f"  {tipo} {numero} de {anio}  ({nombre})\n      {como}")


if __name__ == "__main__":
    main()
