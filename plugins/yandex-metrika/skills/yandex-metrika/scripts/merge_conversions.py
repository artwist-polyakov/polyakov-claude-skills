#!/usr/bin/env python3
"""Combine conversion CSV batches, removing their trailing helper metrics."""

import argparse
import csv
import sys


def merge(mode, batches):
    header = None
    combined = {}
    source_headers = None
    for path, goal_count, helper_count in batches:
        goal_metrics = int(goal_count) * 3
        metric_count = goal_metrics + int(helper_count)
        with open(path, encoding="utf-8-sig", newline="") as source:
            rows = list(csv.reader(source, strict=True))
        if not rows or not rows[0]:
            raise ValueError(f"Empty CSV batch: {path}")

        columns = len(rows[0]) - 1
        if mode == "table":
            if columns != metric_count:
                raise ValueError(f"Unexpected metric columns in {path}")
            keep = goal_metrics
        else:
            # Bytime CSV: period, then all sources for metric 1, metric 2, ...
            if columns % metric_count:
                raise ValueError(f"Unexpected time-series columns in {path}")
            sources = columns // metric_count
            keep = goal_metrics * sources
            current_sources = rows[0][1 + keep : 1 + keep + sources]
            if source_headers is not None and source_headers != current_sources:
                raise ValueError("Sources or their order changed between batches; retry the report.")
            source_headers = current_sources

        batch_rows = {}
        for row in rows[1:]:
            if len(row) != len(rows[0]):
                raise ValueError(f"Inconsistent CSV row width in {path}")
            if row[0] in batch_rows:
                raise ValueError(f"Duplicate row key in {path}")
            batch_rows[row[0]] = row[1 : 1 + keep]

        if header is None:
            header = rows[0][: 1 + keep]
            combined = batch_rows
        else:
            if rows[0][0] != header[0] or batch_rows.keys() != combined.keys():
                raise ValueError("Row keys changed between batches; retry the report.")
            header.extend(rows[0][1 : 1 + keep])
            for key, values in batch_rows.items():
                combined[key].extend(values)

    return header, combined


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("table", "bytime"))
    parser.add_argument("output")
    parser.add_argument("--batch", nargs=3, action="append", required=True,
                        metavar=("CSV", "GOALS", "HELPERS"))
    args = parser.parse_args()
    try:
        header, rows = merge(args.mode, args.batch)
        with open(args.output, "w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output, lineterminator="\n")
            writer.writerow(header)
            writer.writerows([key, *values] for key, values in rows.items())
    except (OSError, ValueError, csv.Error) as error:
        print(f"Error: Cannot combine conversion reports: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
