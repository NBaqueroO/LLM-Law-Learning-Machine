"""Construye los dos índices y escribe indices/index_manifest.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.config import CORPUS_DB, ENCODER, INDEX_JURIS, INDEX_MANIFEST, INDEX_NORMAS

INDEXAR = Path(__file__).with_name("indexar.py")
PASOS = {
    "normas": ["--sin-decretos-extra", "--sin-sentencias-extra", "--sin-leyes-ruido", "--nombre-codigo"],
    "juris": ["--solo-sentencias"],
}


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 24), b""):
            h.update(bloque)
    return h.hexdigest()


def manifiesto(db: Path, indices: dict[str, Path], salida: Path) -> dict:
    from src.indexing.dense_encoder import prefijos
    man = {"fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"), "indices": {}, "archivos": {}}
    for nombre, carpeta in indices.items():
        info = json.loads((carpeta / "info.json").read_text(encoding="utf-8"))
        man["indices"][nombre] = {"carpeta": carpeta.name, "fragmentos": info.get("n"),
                                  "dimension": info.get("dimension"), "denso": (carpeta / "dense.faiss").exists()}
        man.setdefault("encoder", info.get("modelo"))
    pre_p, pre_c = prefijos(man.get("encoder") or "")
    man["prefijos"] = {"pasaje": pre_p, "consulta": pre_c}
    base = salida.parent
    for ruta in sorted([db, *[p for c in indices.values() for p in c.rglob("*") if p.is_file()]]):
        if "emb_shards" in ruta.parts:
            continue
        clave = ruta.relative_to(base).as_posix() if ruta.is_relative_to(base) else ruta.name
        print(f"  sha256 {clave}")
        man["archivos"][clave] = sha256(ruta)
    salida.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"manifiesto: {salida}")
    return man


def verificar(salida: Path = INDEX_MANIFEST) -> list[str]:
    """Archivos cuyo sha256 no coincide con el manifiesto (vacío = el índice está intacto)."""
    man = json.loads(salida.read_text(encoding="utf-8"))
    malos = []
    for clave, h in man["archivos"].items():
        ruta = salida.parent / clave
        if not ruta.exists() and clave == CORPUS_DB.name:
            ruta = CORPUS_DB
        if not ruta.exists() or sha256(ruta) != h:
            malos.append(clave)
    return malos


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=CORPUS_DB)
    ap.add_argument("--modelo", default=ENCODER)
    ap.add_argument("--solo-bm25", action="store_true")
    ap.add_argument("--solo-manifiesto", action="store_true")
    ap.add_argument("--verificar", action="store_true", help="revisa los sha256 contra el manifiesto y termina")
    args = ap.parse_args(argv)
    if args.verificar:
        malos = verificar()
        print("índice intacto" if not malos else f"no coinciden: {malos}")
        return 1 if malos else 0

    indices = {"normas": INDEX_NORMAS, "juris": INDEX_JURIS}
    if not args.solo_manifiesto:
        for nombre, carpeta in indices.items():
            cmd = [sys.executable, str(INDEXAR), "--db", str(args.db), "--out", str(carpeta),
                   "--modelo", args.modelo, *PASOS[nombre], *(["--solo-bm25"] if args.solo_bm25 else [])]
            print("$", " ".join(cmd))
            subprocess.run(cmd, check=True)
    manifiesto(args.db, {n: c for n, c in indices.items() if (c / "info.json").exists()}, INDEX_MANIFEST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
