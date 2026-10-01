import json
import re
from pathlib import Path
from typing import Any, Iterable, List

try:  # Optional dependency for lexical retrieval
    from rank_bm25 import BM25Okapi  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    BM25Okapi = None  # type: ignore

try:  # Optional dependency for dense embeddings
    from sentence_transformers import SentenceTransformer  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    SentenceTransformer = None  # type: ignore


ARTICLE_REF_RE = re.compile(
    r"(?:art(?:[íi]culo)|art\.?|articulo)\s*(?:n[ºo.]?\s*)?(\d+[A-Za-z]?)",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜáéíóúüñÑ0-9]+")
LEGAL_QUERY_TERMS = {
    "arrendamiento": [
        "arrendador", "arrendatario", "arrendamiento", "arriendo", "renta",
        "cosa arrendada", "turbación", "turbacion", "saneamiento", "desistir"
    ],
    "comodato": [
        "comodato", "préstamo de uso", "prestamo de uso", "entrega gratuita",
        "restituir", "uso de la cosa"
    ],
}


def normalize_article_value(value: Any) -> str:
    if value is None:
        return ""
    value = str(value).strip()
    return re.sub(r"\s+", " ", value)


def normalize_record(record: dict[str, Any], source_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Adds the metadata the legal RAG pipeline needs to trace and cite sources."""
    source_meta = source_meta or {}
    record = dict(record)

    if "norma_numero" not in record:
        record["norma_numero"] = source_meta.get("norma_numero") or _extract_norma_numero(record.get("norma"), record.get("norma_base"))
    if "organo_emisor" not in record:
        record["organo_emisor"] = source_meta.get("organo_emisor") or "Congreso de Colombia"
    if "categoria" not in record:
        record["categoria"] = source_meta.get("categoria") or "Derecho civil"

    if not record.get("texto_indexable"):
        head = " | ".join(
            part for part in [
                record.get("fuente"),
                record.get("norma"),
                record.get("titulo"),
                record.get("capitulo"),
                record.get("articulo"),
                record.get("titulo_articulo"),
                record.get("texto"),
            ]
            if part
        )
        record["texto_indexable"] = head

    record["indexar"] = bool(record.get("indexar", True)) and bool(record.get("texto_indexable"))
    return record


def _extract_norma_numero(*values: Any) -> str:
    for raw in values:
        if raw is None:
            continue
        match = re.search(r"(?:Ley|Decreto|Resoluci[oó]n|Acto Legislativo)\s*(?:N[ºo.]?\s*)?(\d+[A-Za-z]?)", str(raw), re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def iter_jsonl_files(data_dir: str | Path = "data") -> list[Path]:
    base = Path(data_dir)
    if not base.exists():
        return []
    return sorted(base.glob("*.jsonl"))


def load_corpus(data_dir: str | Path = "data") -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in iter_jsonl_files(data_dir):
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("indexar") is False:
                    continue
                records.append(normalize_record(record))
    return records


def tokenize_text(text: str) -> list[str]:
    return TOKEN_RE.findall((text or "").lower())


def expand_legal_query(question: str) -> set[str]:
    lowered = (question or "").lower()
    terms = set(tokenize_text(lowered))
    for domain, domain_terms in LEGAL_QUERY_TERMS.items():
        if any(term in lowered for term in domain_terms):
            terms.update(domain_terms)
    return terms


def detect_legal_domain(question: str) -> str | None:
    lowered = (question or "").lower()
    if (
        ("código civil" in lowered or "codigo civil" in lowered)
        and "obligacion" in lowered
        and "persona" in lowered
    ):
        return "codigo_civil_scope"
    if any(term in lowered for term in LEGAL_QUERY_TERMS["arrendamiento"]):
        return "arrendamiento"
    if any(term in lowered for term in LEGAL_QUERY_TERMS["comodato"]):
        return "comodato"
    return None


def legal_keyword_boost(record: dict[str, Any], question: str) -> float:
    text = " ".join(
        part for part in [
            record.get("texto_indexable"),
            record.get("titulo_articulo"),
            record.get("texto"),
            record.get("categoria"),
        ]
        if part
    ).lower()
    terms = expand_legal_query(question)
    if not terms:
        return 0.0
    hits = sum(1 for term in terms if term in text)
    return float(hits / max(len(terms), 1))


def _bm25_candidates(question: str, records: Iterable[dict[str, Any]], top_k: int = 5):
    if BM25Okapi is None:
        return []

    docs = [tokenize_text(r.get("texto_indexable") or r.get("texto") or "") for r in records]
    if not docs:
        return []

    bm25 = BM25Okapi(docs)
    q_tokens = tokenize_text(question)
    scores = bm25.get_scores(q_tokens)
    ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)[:top_k]
    results = []
    for index, score in ranked:
        rec = list(records)[index]
        if score <= 0:
            continue
        results.append({"record": rec, "score": float(score)})
    return results


def _lexical_fallback(question: str, records: Iterable[dict[str, Any]], top_k: int = 5):
    q_tokens = set(tokenize_text(question))
    ranked = []
    for rec in records:
        text = (rec.get("texto_indexable") or rec.get("texto") or "").lower()
        tokens = set(tokenize_text(text))
        score = len(q_tokens & tokens)
        if score > 0:
            ranked.append({"record": rec, "score": float(score)})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:top_k]


def _dense_candidates(question: str, records: Iterable[dict[str, Any]], model: Any | None, top_k: int = 5):
    if model is None:
        return []

    texts = ["passage: " + (r.get("texto_indexable") or r.get("texto") or "") for r in records]
    if not texts:
        return []

    try:
        embeddings = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        query_emb = model.encode(f"query: {question}", normalize_embeddings=True, convert_to_numpy=True)
    except TypeError:
        embeddings = model.encode(texts, normalize_embeddings=True)
        query_emb = model.encode(f"query: {question}", normalize_embeddings=True)
    except Exception:
        return []

    try:
        import numpy as np
    except Exception:
        return []

    emb_arr = np.asarray(embeddings)
    q_arr = np.asarray(query_emb)
    if emb_arr.ndim == 1:
        emb_arr = emb_arr.reshape(1, -1)
    if q_arr.ndim == 1:
        q_arr = q_arr.reshape(1, -1)

    sims = emb_arr @ q_arr.T
    sims = sims.reshape(-1)
    ranked = sorted(enumerate(sims.tolist()), key=lambda item: item[1], reverse=True)[:top_k]
    results = []
    for idx, score in ranked:
        rec = list(records)[idx]
        results.append({"record": rec, "score": float(score)})
    return results


def retrieve_documents(
    question: str,
    records: list[dict[str, Any]],
    model: Any | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Hybrid retrieval: lexical BM25 first, then optional dense embedding scores."""
    if not records:
        return []

    domain = detect_legal_domain(question)
    if domain == "codigo_civil_scope":
        scope_definitions = [
            rec for rec in records
            if "disposiciones comprendidas" in (rec.get("titulo_articulo") or "").lower()
            or "comprende las disposiciones legales sustantivas" in (
                rec.get("texto_indexable") or rec.get("texto") or ""
            ).lower()
        ]
        scoped_records = scope_definitions or [
            rec for rec in records
            if "obligacion" in (rec.get("texto_indexable") or rec.get("texto") or "").lower()
            and "persona" in (rec.get("texto_indexable") or rec.get("texto") or "").lower()
        ]
        records = scoped_records or records

    if domain == "arrendamiento":
        domain_terms = LEGAL_QUERY_TERMS["arrendamiento"]
        strong_terms = [
            term for term in domain_terms
            if term not in {"desistir"}
        ]
        records = [
            rec for rec in records
            if any(term in (rec.get("texto_indexable") or rec.get("texto") or "").lower() for term in strong_terms)
        ] or records
        lowered_question = question.lower()
        asks_landlord_termination = (
            "arrendador" in lowered_question
            and any(term in lowered_question for term in ("terminar", "terminación", "terminacion", "poner fin"))
        )
        if asks_landlord_termination:
            termination_records = [
                rec for rec in records
                if "arrendador" in (rec.get("texto_indexable") or rec.get("texto") or "").lower()
                and "poner fin al arrendamiento" in (rec.get("texto_indexable") or rec.get("texto") or "").lower()
            ]
            records = termination_records or records

    if not records:
        return []

    bm25_hits = _bm25_candidates(question, records, top_k=max(10, top_k * 2))
    dense_hits = _dense_candidates(question, records, model, top_k=max(10, top_k * 2))

    merged: dict[str, dict[str, Any]] = {}
    for hit in bm25_hits:
        rec = hit["record"]
        key = rec.get("id") or rec.get("articulo") or str(len(merged))
        merged[key] = {
            "record": rec,
            "score": float(hit["score"]),
            "source": "bm25",
        }
    for hit in dense_hits:
        rec = hit["record"]
        key = rec.get("id") or rec.get("articulo") or str(len(merged))
        prev = merged.get(key)
        score = float(hit["score"])
        if prev is None:
            merged[key] = {"record": rec, "score": score, "source": "dense"}
        else:
            dense_norm = score
            bm25_norm = prev["score"] if prev["source"] == "bm25" else 0.0
            prev["score"] = (0.65 * dense_norm) + (0.35 * bm25_norm)
            prev["source"] = "hybrid"

    for item in merged.values():
        rec = item["record"]
        boost = legal_keyword_boost(rec, question)
        item["score"] = float(item["score"]) + (boost * 5.0)

    if not merged:
        lexical_hits = _lexical_fallback(question, records, top_k=top_k)
        return [
            {
                "id": hit["record"].get("id") or hit["record"].get("articulo"),
                "norma": hit["record"].get("norma"),
                "articulo": hit["record"].get("articulo"),
                "titulo_articulo": hit["record"].get("titulo_articulo"),
                "texto": hit["record"].get("texto") or hit["record"].get("texto_indexable"),
                "score": float(hit["score"]),
                "source": "lexical",
            }
            for hit in lexical_hits
        ]

    ranked = sorted(merged.values(), key=lambda item: item["score"], reverse=True)[:top_k]
    return [
        {
            "id": rec.get("id") or rec.get("articulo"),
            "norma": rec.get("norma"),
            "articulo": rec.get("articulo"),
            "titulo_articulo": rec.get("titulo_articulo"),
            "texto": rec.get("texto") or rec.get("texto_indexable"),
            "score": float(item["score"]),
            "source": item["source"],
        }
        for item in ranked
        for rec in [item["record"]]
    ]


def extract_article_mentions(text: str) -> list[str]:
    matches = ARTICLE_REF_RE.findall(text or "")
    return [normalize_article_value(m) for m in matches]


def verify_citations(answer_text: str, retrieved: list[dict[str, Any]]) -> dict[str, Any]:
    mentions = extract_article_mentions(answer_text)
    if not mentions:
        return {"mentions": [], "valid": [], "invalid": []}

    valid: list[str] = []
    invalid: list[str] = []
    article_lookup = {
        normalize_article_value(doc.get("articulo"))
        for doc in retrieved
        if doc.get("articulo")
    }

    for mention in mentions:
        if mention in article_lookup:
            valid.append(mention)
        else:
            invalid.append(mention)

    return {"mentions": mentions, "valid": sorted(set(valid)), "invalid": sorted(set(invalid))}


def build_alia_prompt(question: str, retrieved: list[dict[str, Any]]) -> str:
    snippets = []
    for i, item in enumerate(retrieved, start=1):
        snippets.append(
            f"[EVIDENCIA {i}]\n"
            f"ID: {item.get('id')}\n"
            f"Norma: {item.get('norma')}\n"
            f"Artículo: {item.get('articulo')}\n"
            f"Texto: {item.get('texto')}\n"
        )

    return (
        "Eres un asistente jurídico colombiano. Responde solo con la evidencia provista. "
        "No inventes artículos, normas ni citas. Si la evidencia no alcanza, abstente.\n\n"
        f"Pregunta: {question}\n\nEvidencia:\n" + "\n".join(snippets)
    )


def needs_abstention(retrieved: list[dict[str, Any]], threshold: float = 0.15) -> bool:
    if not retrieved:
        return True
    return max(float(item.get("score", 0.0)) for item in retrieved) < threshold


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Minimal legal RAG pipeline for Colombian norms.")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--question", default="¿Puede el arrendador terminar unilateralmente el contrato?")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--dense", action="store_true", help="Enable optional dense retrieval (may download a model).")
    args = ap.parse_args()

    records = load_corpus(args.data_dir)
    if not records:
        print("No se encontraron artículos indexables en data/.")
        return

    model = None
    if args.dense and SentenceTransformer is not None:
        try:
            model = SentenceTransformer("intfloat/multilingual-e5-large")
        except Exception:
            model = None

    hits = retrieve_documents(args.question, records, model=model, top_k=args.top_k)
    for idx, item in enumerate(hits, start=1):
        print(f"{idx}. [{item['articulo']}] {item['norma']} :: score={item['score']:.4f}")
        print(item.get("texto", "")[:180])
        print("---")

    print("Prompt: ")
    print(build_alia_prompt(args.question, hits))


if __name__ == "__main__":
    main()
