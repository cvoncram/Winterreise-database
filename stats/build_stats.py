#!/usr/bin/env python3
"""
Build normalization statistics for the Winterreise performance corpus.

Runs inside the *database* repository (not the application repository), where
every performance file is available on the local filesystem. Because there is
no network involved, a full recomputation takes seconds, so no incremental
caching is needed: read everything, compute everything, write the result.

Centering uses the median, scaling uses the scaled median absolute deviation
(MAD). Both are robust against the isolated extremes that are characteristic
of this material (single fortissimo climaxes, barely audible pianissimi).

Every scale figure is pooled across all recordings, so that one unit means the
same thing in every recording. What varies is the level at which the residuals
are formed beforehand, and it has to match the question being asked:

    pieces        residuals against the piece level, kept apart per piece.
                  Used when a single piece (or a range of measures) is
                  compared, where only variation within that piece is at
                  stake.
    cycle         residuals against the recording's overall level. Used when
                  whole cycles or halves are compared, where the differences
                  between loud and quiet pieces belong to the question.
    piecesPooled  as `pieces`, but with the pieces thrown together into a
                  single figure. Not used for normalization at present; it is
                  the only yardstick that is identical for every piece and
                  therefore the only basis on which distances measured in
                  different pieces may be compared with each other.

Dynamics and timbre are centred on the recording's own median, since a
generally louder or brighter recording reflects recording technique. Tempo is
not: a recording being generally faster is interpretive information. Its
centre is therefore formed across all recordings, which also means it cancels
out of any comparison between two of them.

Outputs, relative to the repository root:

    stats/corpus.json              scale figures and tempo centres
    stats/recordings/<name>.json   per-recording dynamics and timbre medians
    stats/report.json              data-quality report

Standard library only — no third-party dependencies.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

MEI_NS = {
    "mei": "http://www.music-encoding.org/ns/mei",
    "xml": "http://www.w3.org/XML/1998/namespace",
}

TIMBRE_LABEL = "spectral centroid (Hz)"

# For normally distributed data, MAD ≈ 0.6745·σ. Multiplying by 1/0.6745
# puts the MAD on the same scale as a standard deviation, so that downstream
# z-scores keep their familiar magnitude and the ±2 clamp still means what it
# used to mean.
MAD_SCALE = 1.4826

PERFORMANCES_DIR = "performances"
OUTPUT_DIR = "stats"

import xml.etree.ElementTree as ET


# ---------------------------------------------------------------- parsing


def parse_rows(xml_text: str) -> list[dict]:
    """
    Extract per-beat rows from one performance file.

    Mirrors the application's own parser: the three element kinds are
    collected per measure and matched up positionally, since the upstream
    pipeline writes them in beat order without an explicit beat reference.
    """
    root = ET.fromstring(xml_text)
    rows: list[dict] = []

    for measure in root.findall(".//mei:measure", MEI_NS):
        measure_num = measure.attrib.get("n", "?")
        add = measure.find(".//mei:add", MEI_NS)
        if add is None:
            continue

        tempos = add.findall(".//mei:tempo", MEI_NS)
        dyns = add.findall(".//mei:dynam", MEI_NS)
        timbres = add.findall(f'.//mei:annot[@label="{TIMBRE_LABEL}"]', MEI_NS)

        for i in range(max(len(tempos), len(dyns), len(timbres))):
            rows.append(
                {
                    "measure": f"{measure_num}.{i + 1}",
                    "tempo": _as_float(tempos[i].attrib.get("mm")) if i < len(tempos) else None,
                    "dynamics": _as_float(dyns[i].attrib.get("n")) if i < len(dyns) else None,
                    "timbre": _as_float(timbres[i].attrib.get("n")) if i < len(timbres) else None,
                }
            )

    return rows


def _as_float(value: Optional[str]) -> Optional[float]:
    """Dynamics carries non-numeric values such as 'extreme' in rare cases."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


# ------------------------------------------------------------- statistics


def median(values: list[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def mad(values: list[float], scale: float = MAD_SCALE) -> Optional[float]:
    """Scaled median absolute deviation. None if undefined or degenerate."""
    if not values:
        return None
    centre = statistics.median(values)
    spread = statistics.median([abs(v - centre) for v in values])
    return scale * spread if spread > 0 else None


def logs(values: list[float]) -> list[float]:
    """Log-transform, dropping non-positive values (log is undefined there)."""
    return [math.log(v) for v in values if v > 0]


# ------------------------------------------------------------------ main


def collect_files(root: Path) -> dict[str, dict[int, Path]]:
    """Map recording name -> {piece number -> file path}."""
    base = root / PERFORMANCES_DIR
    if not base.is_dir():
        sys.exit(f"No '{PERFORMANCES_DIR}/' directory found under {root}")

    found: dict[str, dict[int, Path]] = {}
    for recording_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        pieces: dict[int, Path] = {}
        for path in sorted(recording_dir.glob("*.xml")):
            match = re.search(r"_(\d+)_", path.name)
            if match:
                pieces[int(match.group(1))] = path
        if pieces:
            found[recording_dir.name] = pieces
    return found


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    files = collect_files(root)

    recordings: dict[str, dict] = {}
    problems: list[dict] = []

    # Residual pools, each gathering across all recordings. `piece_pool` keeps
    # the pieces apart, `cycle_pool` throws them together at the recording
    # level; the two produce genuinely different spreads and must not be mixed.
    #
    # Note the asymmetry between the parameters. Dynamics and timbre are
    # centred per recording, so their residuals have to be formed while the
    # recording is still in hand. Tempo is centred across recordings, so its
    # pool collects values and the centre is formed from the pool afterwards.
    piece_pool: dict[int, dict[str, list[float]]] = defaultdict(
        lambda: {"dynamics": [], "timbre": [], "tempo": []}
    )
    cycle_pool: dict[str, list[float]] = {"dynamics": [], "timbre": []}
    all_log_tempo: list[float] = []

    for name, pieces in files.items():
        parsed: dict[int, dict[str, list[float]]] = {}

        for piece_num, path in sorted(pieces.items()):
            try:
                rows = parse_rows(path.read_text(encoding="utf-8"))
            except ET.ParseError as exc:
                problems.append({"recording": name, "piece": piece_num,
                                 "file": path.name, "error": f"unparseable XML: {exc}"})
                continue

            if not rows:
                problems.append({"recording": name, "piece": piece_num,
                                 "file": path.name, "error": "no beat rows found"})
                continue

            parsed[piece_num] = {
                "tempo": [r["tempo"] for r in rows if r["tempo"] is not None],
                "dynamics": [r["dynamics"] for r in rows if r["dynamics"] is not None],
                "timbre": [r["timbre"] for r in rows if r["timbre"] is not None],
                "_beats": [1] * len(rows),
            }

        if not parsed:
            problems.append({"recording": name, "error": "no usable pieces"})
            continue

        # Recording-wide medians, used when comparing at cycle / half-cycle scope.
        overall_dynamics = [v for p in parsed.values() for v in p["dynamics"]]
        overall_log_timbre = logs([v for p in parsed.values() for v in p["timbre"]])

        overall_dyn_median = median(overall_dynamics)
        overall_tbr_log_median = median(overall_log_timbre)

        piece_entries: dict[str, dict] = {}

        for piece_num, values in sorted(parsed.items()):
            log_timbre = logs(values["timbre"])
            log_tempo = logs(values["tempo"])

            dyn_median = median(values["dynamics"])
            tbr_log_median = median(log_timbre)

            # No tempo median here: tempo is not centred per recording, so
            # there is nothing recording-specific about it to store.
            piece_entries[str(piece_num)] = {
                "dynamicsMedian": dyn_median,
                "timbreLogMedian": tbr_log_median,
                "beats": len(values["_beats"]),
                "usable": {
                    "tempo": len(values["tempo"]),
                    "dynamics": len(values["dynamics"]),
                    "timbre": len(values["timbre"]),
                },
            }

            entry = piece_pool[piece_num]
            if dyn_median is not None:
                entry["dynamics"] += [v - dyn_median for v in values["dynamics"]]
            if tbr_log_median is not None:
                entry["timbre"] += [v - tbr_log_median for v in log_timbre]
            entry["tempo"] += log_tempo

            if overall_dyn_median is not None:
                cycle_pool["dynamics"] += [v - overall_dyn_median for v in values["dynamics"]]
            if overall_tbr_log_median is not None:
                cycle_pool["timbre"] += [v - overall_tbr_log_median for v in log_timbre]

            all_log_tempo += log_tempo

        recordings[name] = {
            "pieces": piece_entries,
            "overall": {
                "dynamicsMedian": overall_dyn_median,
                "timbreLogMedian": overall_tbr_log_median,
            },
        }

    # The pooled figures are the per-piece pools thrown together — the same
    # residuals, differing only in that the pieces are no longer kept apart.
    # Tempo has to be centred first, since its pool holds values.
    pooled_dynamics = [v for e in piece_pool.values() for v in e["dynamics"]]
    pooled_timbre = [v for e in piece_pool.values() for v in e["timbre"]]
    pooled_tempo: list[float] = []
    for e in piece_pool.values():
        centre = median(e["tempo"])
        if centre is not None:
            pooled_tempo += [v - centre for v in e["tempo"]]

    corpus = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "madScaleFactor": MAD_SCALE,
        "pieces": {
            str(piece_num): {
                "dynamics": {"mad": mad(e["dynamics"])},
                "timbre": {"mad": mad(e["timbre"])},
                "tempo": {"logMedian": median(e["tempo"]), "mad": mad(e["tempo"])},
            }
            for piece_num, e in sorted(piece_pool.items())
        },
        "cycle": {
            "dynamics": {"mad": mad(cycle_pool["dynamics"])},
            "timbre": {"mad": mad(cycle_pool["timbre"])},
            "tempo": {"logMedian": median(all_log_tempo), "mad": mad(all_log_tempo)},
        },
        # No tempo centre here: these figures share the per-piece centres
        # above and differ from them only in the scale.
        "piecesPooled": {
            "dynamics": {"mad": mad(pooled_dynamics)},
            "timbre": {"mad": mad(pooled_timbre)},
            "tempo": {"mad": mad(pooled_tempo)},
        },
        "counts": {
            "recordings": len(recordings),
            "pieces": sum(len(r["pieces"]) for r in recordings.values()),
            "distinctPieces": len(piece_pool),
            "tempoValues": len(all_log_tempo),
            "dynamicsResiduals": len(pooled_dynamics),
            "timbreResiduals": len(pooled_timbre),
        },
    }

    write_output(root, corpus, recordings, problems)


def write_output(root: Path, corpus: dict, recordings: dict, problems: list) -> None:
    out = root / OUTPUT_DIR
    (out / "recordings").mkdir(parents=True, exist_ok=True)

    (out / "corpus.json").write_text(
        json.dumps(corpus, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # Remove stale files so that a deleted recording disappears from stats too.
    existing = {p.stem for p in (out / "recordings").glob("*.json")}
    for name in existing - set(recordings):
        (out / "recordings" / f"{name}.json").unlink()

    for name, data in recordings.items():
        (out / "recordings" / f"{name}.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    report = build_report(corpus, recordings, problems)
    (out / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"{corpus['counts']['recordings']} recordings, "
          f"{corpus['counts']['pieces']} pieces "
          f"({corpus['counts']['distinctPieces']} distinct)")

    # Print the pooled figures against the cycle ones: the gap between them is
    # the variation between pieces, which is what the two levels differ by.
    print(f"  {'':9s} {'pooled':>12s} {'cycle':>12s}")
    for param in ("tempo", "dynamics", "timbre"):
        pooled = corpus["piecesPooled"][param]["mad"]
        cycle = corpus["cycle"][param]["mad"]
        fmt = lambda v: f"{v:12.4f}" if v is not None else f"{'-':>12s}"
        print(f"  {param:9s} {fmt(pooled)} {fmt(cycle)}")
    if report["warnings"]:
        print(f"\n{len(report['warnings'])} warning(s):")
        for w in report["warnings"][:20]:
            print(f"  - {w}")
    if problems:
        print(f"\n{len(problems)} problem(s) — see stats/report.json")


def build_report(corpus: dict, recordings: dict, problems: list) -> dict:
    """
    Flag the data conditions that silently distort comparisons: uneven beat
    counts across recordings (which inflate DTW distances for the shorter
    sequence) and pieces with many unusable measurements.
    """
    warnings: list[str] = []

    # Beat counts per piece across recordings — these should agree closely,
    # since every performance file mirrors the same score.
    per_piece: dict[str, dict[str, int]] = {}
    for name, data in recordings.items():
        for piece, entry in data["pieces"].items():
            per_piece.setdefault(piece, {})[name] = entry["usable"]["tempo"]

    for piece, counts in sorted(per_piece.items(), key=lambda kv: int(kv[0])):
        if len(counts) < 2:
            continue
        lo, hi = min(counts.values()), max(counts.values())
        if hi > 0 and (hi - lo) / hi > 0.05:
            low = [n for n, c in counts.items() if c == lo]
            warnings.append(
                f"piece {piece}: usable tempo values range {lo}–{hi} "
                f"({(hi - lo) / hi:.0%} spread; fewest in {', '.join(low)})"
            )

    for name, data in recordings.items():
        for piece, entry in data["pieces"].items():
            beats = entry["beats"]
            if beats == 0:
                continue
            for kind, count in entry["usable"].items():
                if count / beats < 0.9:
                    warnings.append(
                        f"{name} piece {piece}: only {count}/{beats} "
                        f"usable {kind} values"
                    )

    return {
        "generated": corpus["generated"],
        "counts": corpus["counts"],
        "problems": problems,
        "warnings": warnings,
    }


if __name__ == "__main__":
    main()
