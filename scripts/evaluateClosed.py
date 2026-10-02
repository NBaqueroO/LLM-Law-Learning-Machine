import json
from evaluate import score_closed
from common import read_jsonl
from pathlib import Path
from common import DATA

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--submission", required=True, type=Path)
    ap.add_argument("--split", choices=("sample", "test"), default="sample")
    args = ap.parse_args()

    rows = read_jsonl(DATA / "sample_50.jsonl")
    key = {r["id"]: {"respuesta_correcta": r.get("respuesta_correcta"),
                     "pregunta": r["pregunta"],
                     "formato": r["formato"]} for r in rows}
    closed_ids = [q for q, k in key.items()
                  if k["formato"] == "multiple_choice"]

    subs_list = read_jsonl(args.submission)
    subs = {s["id"]: s for s in subs_list if isinstance(s.get("id"), int)}

    report = {
        "cerradas": score_closed(subs, key, closed_ids),
    }

    # Imprimir el reporte en la terminal
    print(json.dumps(report, ensure_ascii=False, indent=2))

    # Guardar el reporte en un archivo
    with open("outputs/evaluate_closed_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("Reporte guardado en outputs/evaluate_closed_report.json")

    return 0

if __name__ == "__main__":
    import argparse
    main()