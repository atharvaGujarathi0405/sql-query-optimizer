"""Build data/queries.yaml from data/antipatterns/*.sql and data/queries/*.sql.

Each file: leading `-- key: value` header lines (dataset, anti-pattern, tags),
then the SQL. id = file stem. Also creates data/splits.yaml (dev/test,
stratified by dataset, fixed seed) ONLY if it does not exist: splits are
frozen before any evaluation (CLAUDE.md rule 5), so this never overwrites them.
`--check` verifies every registered query is in exactly one split.
Usage: python scripts/build_registry.py [--check]
"""
import random
import sys
from collections import defaultdict
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parents[1] / "data"
SEED = 20261027
DEV_FRACTION = 0.4


def parse(path: Path) -> dict:
    meta, sql = {}, []
    for line in path.read_text().splitlines():
        if not sql and line.startswith("--"):
            k, _, v = line[2:].partition(":")
            meta[k.strip()] = v.strip()
        else:
            sql.append(line)
    q = {"id": path.stem, "dataset": meta["dataset"],
         "tags": [t.strip() for t in meta.get("tags", "").split(",") if t.strip()],
         "sql": "\n".join(sql).strip() + "\n"}
    if "anti-pattern" in meta:
        q["anti_pattern"] = meta["anti-pattern"]
    return q


def literal_block(dumper, s):
    return dumper.represent_scalar("tag:yaml.org,2002:str", s, style="|" if "\n" in s else None)


yaml.add_representer(str, literal_block)


def main() -> None:
    queries = [parse(p) for d in ("antipatterns", "queries") for p in sorted((DATA / d).glob("*.sql"))]
    ids = [q["id"] for q in queries]
    assert len(ids) == len(set(ids)), "duplicate query ids"
    (DATA / "queries.yaml").write_text(yaml.dump(queries, sort_keys=False, width=1000))

    splits_path = DATA / "splits.yaml"
    if not splits_path.exists():
        by_ds = defaultdict(list)
        for q in queries:
            by_ds[q["dataset"]].append(q["id"])
        rng, dev, test = random.Random(SEED), [], []
        for ds in sorted(by_ds):
            ids_ds = sorted(by_ds[ds])
            rng.shuffle(ids_ds)
            n_dev = max(1, round(DEV_FRACTION * len(ids_ds)))
            dev += ids_ds[:n_dev]
            test += ids_ds[n_dev:]
        splits_path.write_text("# Frozen before any evaluation. Tune on dev only; report on test. Never regenerate.\n"
                               + yaml.dump({"seed": SEED, "dev_fraction": DEV_FRACTION,
                                            "dev": sorted(dev), "test": sorted(test)}, sort_keys=False))
    splits = yaml.safe_load(splits_path.read_text())
    in_dev, in_test = set(splits["dev"]), set(splits["test"])
    assert not in_dev & in_test, "id in both dev and test"
    missing = set(ids) - in_dev - in_test
    assert not missing, f"queries missing from splits.yaml: {sorted(missing)}"
    assert not (in_dev | in_test) - set(ids), "splits.yaml references unknown ids"

    print(f"{len(queries)} queries registered")
    print(f"{'dataset':12}{'total':>7}{'dev':>6}{'test':>6}{'antipattern':>13}")
    for ds in sorted({q['dataset'] for q in queries}):
        qs = [q for q in queries if q["dataset"] == ds]
        print(f"{ds:12}{len(qs):>7}{sum(q['id'] in in_dev for q in qs):>6}{sum(q['id'] in in_test for q in qs):>6}"
              f"{sum('antipattern' in q['tags'] for q in qs):>13}")


if __name__ == "__main__":
    main()
