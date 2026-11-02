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

Every figure is also produced in its classical form — arithmetic mean and
standard deviation — and written alongside under its own key. These are not
used for normalization; they exist so that the two families can be compared
and the choice of robust statistics can be shown to make a difference rather
than merely asserted. Both are formed in the same pass, since the cost of the
run is almost entirely the parsing of the performance files.

The residuals have to be pooled twice, because a residual depends on what it
was centred on: mean-centred and median-centred residuals are different
numbers, and the centring happens per recording while that recording is still
open.

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
    stats/recordings/<name>.json   per-recording dynamics and timbre centres
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
    """
    Parse a measurement, or None where there is none to be had.

    Dynamics carries non-numeric values such as 'extreme' in rare cases, and
    the timbre extraction has been seen to emit a literal 'inf' — which
    `float()` accepts without complaint. Infinities are rejected here rather
    than left to propagate: an infinite spectral centroid is not a measurement,
    and once it enters a pool it turns residuals into NaN, which the median
    silently sorts into an arbitrary position instead of raising.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


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


def mean(values: list[float]) -> Optional[float]:
    """Arithmetic mean — the classical counterpart of `median`."""
    return statistics.fmean(values) if values else None


def sd(values: list[float]) -> Optional[float]:
    """
    Standard deviation, the classical counterpart of `mad`.

    The divisor is N, not N−1: these figures describe the corpus that exists
    rather than estimating one it was drawn from, which is the same footing the
    MAD stands on. At the sizes involved the distinction is immaterial anyway.

    A degenerate spread is reported as None, as in `mad`, so that a caller
    dividing by it cannot silently produce infinities.
    """
    if not values:
        return None
    spread = statistics.pstdev(values)
    return spread if spread > 0 else None


# The order the covariance matrix is written in, and the fewest beats that
# make one worth reporting.
COVARIANCE_ORDER = ("tempo", "dynamics", "timbre")
MIN_COVARIANCE_BEATS = 200


def covariance(triples: list[tuple[float, float, float]]) -> Optional[dict]:
    """
    Covariance between the three parameters, in natural units: log tempo,
    decibels, log spectral centroid.

    Left unscaled on purpose. Dividing by a scale is what turns this into the
    covariance of the normalized values, and which scale that is depends on
    the method the application is asked for — mean with standard deviation or
    median with scaled MAD. Consumed as it is, one matrix serves both.

    The centring the caller used does not matter either: covariance subtracts
    its own means.
    """
    n = len(triples)
    if n < MIN_COVARIANCE_BEATS:
        return None

    sums = [0.0, 0.0, 0.0]
    cross = [[0.0] * 3 for _ in range(3)]
    for t in triples:
        for a in range(3):
            sums[a] += t[a]
            for b in range(a, 3):
                cross[a][b] += t[a] * t[b]

    means = [s / n for s in sums]
    matrix = [[0.0] * 3 for _ in range(3)]
    for a in range(3):
        for b in range(a, 3):
            value = cross[a][b] / n - means[a] * means[b]
            matrix[a][b] = matrix[b][a] = value

    return {"order": list(COVARIANCE_ORDER), "matrix": matrix, "beats": n}


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

    # The same pools once more, but with the residuals taken against the mean
    # instead of the median. They cannot be derived from the ones above after
    # the fact: a residual carries its centre with it. Tempo needs no second
    # pool, being centred across recordings rather than within one — the same
    # values serve both, and only the centre and spread drawn from them differ.
    piece_pool_mean: dict[int, dict[str, list[float]]] = defaultdict(
        lambda: {"dynamics": [], "timbre": []}
    )
    cycle_pool_mean: dict[str, list[float]] = {"dynamics": [], "timbre": []}

    # Beats where all three parameters were measured, kept as triples rather
    # than as three separate lists: only a beat's values taken together say
    # anything about how the parameters covary, which is what the Mahalanobis
    # metric needs. Estimating that from whatever recordings a user happens to
    # select gives a different shape every time — on the same data the
    # correlation between dynamics and timbre ranged from -0.17 to +0.60.
    piece_joint: dict[int, list[tuple[float, float, float]]] = defaultdict(list)
    cycle_joint: list[tuple[float, float, float]] = []

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
                # Complete beats only, and the same test the application
                # applies: the two log-transformed parameters need a positive
                # value, so a zero counts as missing here as well.
                "_complete": [
                    (r["tempo"], r["dynamics"], r["timbre"])
                    for r in rows
                    if r["tempo"] is not None and r["tempo"] > 0
                    and r["dynamics"] is not None
                    and r["timbre"] is not None and r["timbre"] > 0
                ],
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
        overall_dyn_mean = mean(overall_dynamics)
        overall_tbr_log_mean = mean(overall_log_timbre)

        piece_entries: dict[str, dict] = {}

        for piece_num, values in sorted(parsed.items()):
            log_timbre = logs(values["timbre"])
            log_tempo = logs(values["tempo"])

            dyn_median = median(values["dynamics"])
            tbr_log_median = median(log_timbre)
            dyn_mean = mean(values["dynamics"])
            tbr_log_mean = mean(log_timbre)

            # No tempo centre here: tempo is not centred per recording, so
            # there is nothing recording-specific about it to store.
            piece_entries[str(piece_num)] = {
                "dynamicsMedian": dyn_median,
                "timbreLogMedian": tbr_log_median,
                "dynamicsMean": dyn_mean,
                "timbreLogMean": tbr_log_mean,
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

            entry_mean = piece_pool_mean[piece_num]
            if dyn_mean is not None:
                entry_mean["dynamics"] += [v - dyn_mean for v in values["dynamics"]]
            if tbr_log_mean is not None:
                entry_mean["timbre"] += [v - tbr_log_mean for v in log_timbre]

            if overall_dyn_median is not None:
                cycle_pool["dynamics"] += [v - overall_dyn_median for v in values["dynamics"]]
            if overall_tbr_log_median is not None:
                cycle_pool["timbre"] += [v - overall_tbr_log_median for v in log_timbre]

            if overall_dyn_mean is not None:
                cycle_pool_mean["dynamics"] += [v - overall_dyn_mean for v in values["dynamics"]]
            if overall_tbr_log_mean is not None:
                cycle_pool_mean["timbre"] += [v - overall_tbr_log_mean for v in log_timbre]

            all_log_tempo += log_tempo

            if dyn_mean is not None and tbr_log_mean is not None:
                piece_joint[piece_num] += [
                    (math.log(t), d - dyn_mean, math.log(c) - tbr_log_mean)
                    for t, d, c in values["_complete"]
                ]
            if overall_dyn_mean is not None and overall_tbr_log_mean is not None:
                cycle_joint += [
                    (math.log(t), d - overall_dyn_mean, math.log(c) - overall_tbr_log_mean)
                    for t, d, c in values["_complete"]
                ]

        recordings[name] = {
            "pieces": piece_entries,
            "overall": {
                "dynamicsMedian": overall_dyn_median,
                "timbreLogMedian": overall_tbr_log_median,
                "dynamicsMean": overall_dyn_mean,
                "timbreLogMean": overall_tbr_log_mean,
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

    pooled_dynamics_mean = [v for e in piece_pool_mean.values() for v in e["dynamics"]]
    pooled_timbre_mean = [v for e in piece_pool_mean.values() for v in e["timbre"]]
    pooled_tempo_mean: list[float] = []
    for e in piece_pool.values():
        centre = mean(e["tempo"])
        if centre is not None:
            pooled_tempo_mean += [v - centre for v in e["tempo"]]

    corpus = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "madScaleFactor": MAD_SCALE,
        "pieces": {
            str(piece_num): {
                "dynamics": {
                    "mad": mad(e["dynamics"]),
                    "sd": sd(piece_pool_mean[piece_num]["dynamics"]),
                },
                "timbre": {
                    "mad": mad(e["timbre"]),
                    "sd": sd(piece_pool_mean[piece_num]["timbre"]),
                },
                "tempo": {
                    "logMedian": median(e["tempo"]),
                    "mad": mad(e["tempo"]),
                    "logMean": mean(e["tempo"]),
                    "sd": sd(e["tempo"]),
                },
                "covariance": covariance(piece_joint[piece_num]),
            }
            for piece_num, e in sorted(piece_pool.items())
        },
        "cycle": {
            "dynamics": {
                "mad": mad(cycle_pool["dynamics"]),
                "sd": sd(cycle_pool_mean["dynamics"]),
            },
            "timbre": {
                "mad": mad(cycle_pool["timbre"]),
                "sd": sd(cycle_pool_mean["timbre"]),
            },
            "tempo": {
                "logMedian": median(all_log_tempo),
                "mad": mad(all_log_tempo),
                "logMean": mean(all_log_tempo),
                "sd": sd(all_log_tempo),
            },
            "covariance": covariance(cycle_joint),
        },
        # No tempo centre here: these figures share the per-piece centres
        # above and differ from them only in the scale.
        "piecesPooled": {
            "dynamics": {"mad": mad(pooled_dynamics), "sd": sd(pooled_dynamics_mean)},
            "timbre": {"mad": mad(pooled_timbre), "sd": sd(pooled_timbre_mean)},
            "tempo": {"mad": mad(pooled_tempo), "sd": sd(pooled_tempo_mean)},
        },
        "counts": {
            "recordings": len(recordings),
            "pieces": sum(len(r["pieces"]) for r in recordings.values()),
            "distinctPieces": len(piece_pool),
            "tempoValues": len(all_log_tempo),
            "dynamicsResiduals": len(pooled_dynamics),
            "timbreResiduals": len(pooled_timbre),
            "completeBeats": len(cycle_joint),
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
    # Each is shown in both its robust and its classical form, so that the
    # effect of outliers on the spread is visible at a glance.
    fmt = lambda v: f"{v:9.4f}" if v is not None else f"{'-':>9s}"
    print(f"  {'':9s} {'pooled MAD':>10s} {'pooled SD':>10s}"
          f" {'cycle MAD':>10s} {'cycle SD':>10s}")
    for param in ("tempo", "dynamics", "timbre"):
        pooled = corpus["piecesPooled"][param]
        cycle = corpus["cycle"][param]
        print(f"  {param:9s} {fmt(pooled['mad'])}  {fmt(pooled['sd'])} "
              f" {fmt(cycle['mad'])}  {fmt(cycle['sd'])}")
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

    # How far the classical spread exceeds the robust one, per piece and
    # parameter. For data without outliers the two are close; a large ratio
    # means a few extreme beats are pulling the standard deviation up, and
    # points at exactly the passages worth listening to again.
    spread_ratios: dict[str, dict[str, float]] = {}
    for piece, entry in corpus["pieces"].items():
        ratios = {}
        for param in ("tempo", "dynamics", "timbre"):
            robust, classical = entry[param]["mad"], entry[param]["sd"]
            if robust and classical:
                ratios[param] = round(classical / robust, 3)
        if ratios:
            spread_ratios[piece] = ratios

    return {
        "generated": corpus["generated"],
        "counts": corpus["counts"],
        "problems": problems,
        "warnings": warnings,
        "spreadRatios": spread_ratios,
    }


if __name__ == "__main__":
    main()
