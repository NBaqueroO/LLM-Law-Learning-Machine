import unittest

from legal_rag import load_corpus, retrieve_documents, verify_citations, normalize_record


class TestLegalRag(unittest.TestCase):
    def setUp(self):
        self.records = [
            normalize_record(
                {
                    "id": "codigo_civil#art_1",
                    "doc_id": "codigo_civil",
                    "fuente": "Código Civil",
                    "norma": "Ley 84 de 1873",
                    "tipo_norma": "Ley",
                    "anio": 1873,
                    "categoria": "Derecho civil",
                    "articulo": "1",
                    "titulo_articulo": "Disposiciones comprendidas",
                    "texto": "El Código Civil rige la relación de las personas y sus obligaciones.",
                    "texto_indexable": "Código Civil Ley 84 de 1873 Artículo 1 Disposiciones comprendidas. El Código Civil rige la relación de las personas y sus obligaciones.",
                    "estado": "vigente",
                    "indexar": True,
                }
            ),
            normalize_record(
                {
                    "id": "codigo_civil#art_2",
                    "doc_id": "codigo_civil",
                    "fuente": "Código Civil",
                    "norma": "Ley 84 de 1873",
                    "tipo_norma": "Ley",
                    "anio": 1873,
                    "categoria": "Derecho civil",
                    "articulo": "2",
                    "titulo_articulo": "Capacidad de las personas",
                    "texto": "Las personas son capaces de ser titulares de derechos y obligaciones.",
                    "texto_indexable": "Código Civil Ley 84 de 1873 Artículo 2 Capacidad de las personas. Las personas son capaces de ser titulares de derechos y obligaciones.",
                    "estado": "vigente",
                    "indexar": True,
                }
            ),
        ]

    def test_normalize_record_keeps_trail_metadata(self):
        self.assertEqual(self.records[0]["norma_numero"], "84")
        self.assertEqual(self.records[0]["organo_emisor"], "Congreso de Colombia")
        self.assertTrue(self.records[0]["texto_indexable"].startswith("Código Civil"))

    def test_verify_citations_keeps_valid_and_invalid_mentions(self):
        result = verify_citations("La norma del artículo 1 establece la regla, pero el artículo 99 no aparece en la evidencia.", self.records)
        self.assertIn("1", result["valid"])
        self.assertIn("99", result["invalid"])

    def test_retrieve_documents_returns_ranked_hits(self):
        hits = retrieve_documents("relación de obligaciones y personas", self.records, top_k=2)
        self.assertTrue(len(hits) >= 1)
        self.assertEqual(hits[0]["articulo"], "1")

    def test_retrieve_prioritizes_civil_code_scope_for_people_and_obligations(self):
        hits = retrieve_documents(
            "¿Puede el Código Civil regular obligaciones de las personas?",
            self.records,
            top_k=2,
        )
        self.assertEqual(hits[0]["articulo"], "1")

    def test_retrieve_prefers_direct_landlord_termination_authority(self):
        records = [
            normalize_record({
                "id": "codigo_civil#art_2200",
                "doc_id": "codigo_civil",
                "fuente": "Código Civil",
                "norma": "Ley 84 de 1873",
                "tipo_norma": "Ley",
                "anio": 1873,
                "categoria": "Derecho civil",
                "articulo": "2200",
                "titulo_articulo": "Comodato",
                "texto": "El comodato o préstamo de uso es un contrato por el cual una parte entrega una cosa gratis para que haga uso de ella.",
                "texto_indexable": "Código Civil Ley 84 de 1873 Artículo 2200 Comodato. El comodato o préstamo de uso es un contrato por el cual una parte entrega una cosa gratis para que haga uso de ella.",
                "estado": "vigente",
                "indexar": True,
            }),
            normalize_record({
                "id": "codigo_civil#art_1982",
                "doc_id": "codigo_civil",
                "fuente": "Código Civil",
                "norma": "Ley 84 de 1873",
                "tipo_norma": "Ley",
                "anio": 1873,
                "categoria": "Derecho civil",
                "articulo": "1982",
                "titulo_articulo": "Obligaciones del arrendador",
                "texto": "El arrendador es obligado a entregar la cosa arrendada, mantenerla en estado de servir para el fin a que ha sido arrendada y librar al arrendatario de toda turbación.",
                "texto_indexable": "Código Civil Ley 84 de 1873 Artículo 1982. El arrendador es obligado a entregar la cosa arrendada, mantenerla en estado de servir para el fin a que ha sido arrendada y librar al arrendatario de toda turbación.",
                "estado": "vigente",
                "indexar": True,
            }),
            normalize_record({
                "id": "codigo_civil#art_1997",
                "doc_id": "codigo_civil",
                "fuente": "Código Civil",
                "norma": "Ley 84 de 1873",
                "tipo_norma": "Ley",
                "anio": 1873,
                "categoria": "Derecho civil",
                "articulo": "1997",
                "titulo_articulo": "Deterioro de la cosa arrendada",
                "texto": "Faltando el arrendatario a su obligación, responderá de los perjuicios; y aún tendrá derecho el arrendador para poner fin al arrendamiento, en el caso de un grave y culpable deterioro.",
                "texto_indexable": "Código Civil Ley 84 de 1873 Artículo 1997. Faltando el arrendatario a su obligación, responderá de los perjuicios; y aún tendrá derecho el arrendador para poner fin al arrendamiento, en el caso de un grave y culpable deterioro.",
                "estado": "vigente",
                "indexar": True,
            }),
        ]
        hits = retrieve_documents("¿Puede un arrendador terminar unilateralmente el contrato?", records, top_k=2)
        self.assertEqual(hits[0]["articulo"], "1997")


if __name__ == "__main__":
    unittest.main()
