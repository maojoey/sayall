#!/usr/bin/env python3
"""Render content-free comparisons without automatically declaring a default."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def number(value: float | None, scale: float = 1, digits: int = 2) -> str:
    return "—" if value is None else f"{value * scale:.{digits}f}"


def render(report: dict) -> str:
    rows = [f'## {report["corpus_id"]}', "",
            "CPU-only offline engine measurements; lower error and latency are better.", "",
            "| Model | Status | WER | CER | Warm p50 | Warm p95 | Peak RSS | Failures |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for model in report["models"]:
        s = model["summary"]
        complete = model["status"] == "complete" and s["cases"] == model["expected_cases"]
        if not complete:
            rows.append(f'| {model["id"]} | INCOMPLETE—do not rank | — | — | — | — | — | — |')
            continue
        rows.append(f'| {model["id"]} | complete | {number(s["wer"], 100)}% | {number(s["cer"], 100)}% | '
                    f'{number(s["request_to_result_p50_ms"], digits=0)} ms | '
                    f'{number(s["request_to_result_p95_ms"], digits=0)} ms | '
                    f'{number(s["peak_process_rss_bytes"], 1 / 2**20, 0)} MiB | {s["failed_cases"]} |')
    rows.extend(["", "Latency includes PCM read/conversion, inference and IPC, not recording or text delivery.",
                 "Peak RSS is process-lifetime; warmup excluded from accuracy/latency summaries.",
                 "Repeated clips are not independent speakers. No automatic default selection.", ""])
    for model in report["models"]:
        if model["status"] != "complete" or model["summary"]["cases"] != model["expected_cases"]:
            continue
        rows.extend([f'### {model["id"]}: conditions', "",
                     "| Condition | WER (%) | CER (%) | False speech |",
                     "| --- | ---: | ---: | ---: |"])
        for condition, s in model["conditions"].items():
            rows.append(f'| {condition} | {number(s["wer"], 100)} | {number(s["cer"], 100)} | {s["false_speech_cases"]} |')
        rows.append("")
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    text = "\n".join(render(json.loads(path.read_text())) for path in args.reports)
    if args.output:
        args.output.write_text(text)
    else:
        print(text)


if __name__ == "__main__":
    main()
