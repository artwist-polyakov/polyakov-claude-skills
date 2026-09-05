#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Offline integration checks: uv run --script scripts/tests/test_conversions.py."""

import csv
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlsplit


SCRIPTS = Path(__file__).resolve().parents[1]
SOURCES = ['Ad traffic, "campaign"', "Direct traffic"]
PERIODS = ["2024-01-01", "2024-01-02"]
SUFFIXES = ("visits", "reaches", "conversionRate")
LABELS = ("Conversions", "Goals reached", "Conversion rate")


def goal_metrics(goals):
    return [f"ym:s:goal{goal}{suffix}" for goal in goals for suffix in SUFFIXES]


def metric_label(metric, grouped=False):
    if metric == "ym:s:visits":
        return "Sessions"
    match = re.fullmatch(r"ym:s:goal(\d+)(visits|reaches|conversionRate)", metric)
    goal, suffix = match.groups()
    label = LABELS[SUFFIXES.index(suffix)]
    return label if grouped else f'{label} (Goal {goal}, "purchase"\r\nnext line)'


def metric_value(metric, source, period=0):
    if metric == "ym:s:visits":
        return str(10000 + source * 100 + period)
    match = re.fullmatch(r"ym:s:goal(\d+)(visits|reaches|conversionRate)", metric)
    goal, suffix = match.groups()
    # The second source has no conversions in the first batch, but has visits.
    if source == 1 and int(goal) <= 106:
        return "0"
    value = (int(goal) - 100) * 100 + source * 10 + period
    if suffix == "conversionRate":
        return f"{value // 100}.{value % 100:02d}"
    return str(value + (1 if suffix == "reaches" else 0))


def fake_curl(args):
    """Write API-shaped CSV and record parameters, never credentials or URLs."""
    params = {}
    output = headers = url = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-D", "-o", "-H", "--data-urlencode"):
            value = args[index + 1]
            if arg == "-D":
                headers = Path(value)
            elif arg == "-o":
                output = Path(value)
            elif arg == "--data-urlencode":
                key, value = value.split("=", 1)
                params[key] = value
            index += 2
        else:
            if arg.startswith("https://"):
                url = arg
            index += 1
    log = Path(os.environ["FAKE_CURL_LOG"])
    requests = log.read_text().splitlines() if log.exists() else []
    number = len(requests) + 1
    with log.open("a") as stream:
        stream.write(json.dumps({"path": urlsplit(url).path, "params": params}) + "\n")
    failure = os.environ.get("FAKE_CURL_FAILURE")
    if failure == "http" and number == 2:
        headers.write_text("HTTP/1.1 500 Internal Server Error\r\n\r\n")
        output.write_text("Synthetic API failure")
        return 0
    headers.write_text("HTTP/1.1 200 OK\r\n\r\n")
    if failure == "csv" and number == 2:
        output.write_text('\ufeff"unterminated\r\n', encoding="utf-8")
        return 0
    if failure == "header":
        output.write_text('\ufeff\n', encoding="utf-8")
        return 0
    grouped = urlsplit(url).path.endswith("/bytime.csv")
    metrics = params["metrics"].split(",")
    limit = int(params.get("top_keys" if grouped else "limit", "100"))
    sources = list(range(min(limit, len(SOURCES))))
    # Model Metrika's suppression of rows where all requested metrics are zero.
    sources = [source for source in sources
               if any(float(metric_value(metric, source)) for metric in metrics)]
    if number == 2 and failure == "sources":
        sources.pop()
    if number == 2 and failure == "source_order":
        sources.reverse()
    if os.environ.get("FAKE_CURL_EMPTY"):
        sources = []
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        # Equivalent CSV quoting must not affect source or period matching.
        writer = csv.writer(stream, quoting=csv.QUOTE_ALL if number % 2 == 0 else csv.QUOTE_MINIMAL)
        if grouped:
            writer.writerow(["Period"] + [f"{SOURCES[source]} ({metric_label(metric, True)})"
                                          for metric in metrics for source in sources])
            periods = list(range(len(PERIODS)))
            if number % 2 == 0:
                periods.reverse()
            for period in periods:
                writer.writerow([PERIODS[period]] + [metric_value(metric, source, period)
                                                     for metric in metrics for source in sources])
        else:
            writer.writerow(["Traffic source"] + [metric_label(metric) for metric in metrics])
            if number % 2 == 0:
                sources.reverse()
            for source in sources:
                writer.writerow([SOURCES[source]] + [metric_value(metric, source) for metric in metrics])
    return 0


class ConversionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="metrika-conversions-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        for name in ("common.sh", "conversions.sh", "merge_conversions.awk"):
            shutil.copy2(SCRIPTS / name, self.scripts / name)
        config = self.root / "config"
        config.mkdir()
        (config / ".env").write_text("YANDEX_METRIKA_TOKEN=synthetic-test-token\n")
        self.counter = self.root / "cache" / "counter_12345"
        self.counter.mkdir(parents=True)
        self.output = self.root / "result.csv"
        self.log = self.root / "requests.jsonl"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        # The tested script can use only standard shell tools and fake curl.
        # Python is exclusively the test harness, invoked by its absolute path.
        for command in ("sh", "dirname", "mkdir", "cut", "tr", "sed", "grep", "cksum",
                        "awk", "date", "head", "wc", "mktemp", "rm", "cp", "mv", "cat", "sleep"):
            executable = shutil.which(command, path=os.defpath)
            self.assertIsNotNone(executable, f"Required shell command is missing: {command}")
            (bin_dir / command).symlink_to(executable)
        curl = bin_dir / "curl"
        curl.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} "
                        f"{shlex.quote(str(Path(__file__).resolve()))} --fake-curl \"$@\"\n")
        curl.chmod(0o755)
        self.env = dict(os.environ, PATH=str(bin_dir),
                        TMPDIR=str(self.root / "tmp"), FAKE_CURL_LOG=str(self.log))
        self.env.pop("FAKE_CURL_FAILURE", None)
        self.env.pop("FAKE_CURL_EMPTY", None)
        self.assertIsNone(shutil.which("python3", path=self.env["PATH"]))
        self.assertIsNone(shutil.which("uv", path=self.env["PATH"]))
        self.configure(7)

    def configure(self, count):
        self.goals = list(range(101, 101 + count))
        (self.counter / "goals.tsv").write_text("".join(f"{goal}\tGoal {goal}\n" for goal in self.goals))
        (self.counter / "config.json").write_text(json.dumps({
            "conversion_goals": [{"id": goal, "name": f"Goal {goal}"} for goal in self.goals]
        }))

    def run_report(self, *args, success=True):
        result = subprocess.run(["sh", str(self.scripts / "conversions.sh"),
                                 "--counter", "12345", "--date1", PERIODS[0],
                                 "--date2", PERIODS[-1], "--csv", str(self.output), *args],
                                env=self.env, capture_output=True, text=True, timeout=20)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def requests(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def read_csv(self):
        with self.output.open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.reader(stream))

    def assert_batches(self, grouped=False, limit=None):
        requests = self.requests()
        self.assertEqual(len(requests), (len(self.goals) + 5) // 6)
        anchor = f"ym:s:goal{self.goals[0]}visits"
        for index, request in enumerate(requests):
            params = request["params"]
            expected = goal_metrics(self.goals[index * 6:(index + 1) * 6]) + ["ym:s:visits"]
            if index:
                expected.append(anchor)
            self.assertEqual(params["metrics"].split(","), expected)
            self.assertLessEqual(len(expected), 20)
            self.assertEqual(params["keys_sort" if grouped else "sort"],
                             f"-{anchor},ym:s:lastsignTrafficSource")
            self.assertEqual(request["path"], "/stat/v1/data/bytime.csv" if grouped else "/stat/v1/data.csv")
            self.assertEqual(params["top_keys" if grouped else "limit"], str(limit or (30 if grouped else 100)))
            self.assertEqual(params["filters"], "ym:s:isRobot=='No'")
            self.assertEqual(params["accuracy"], "1")

    def assert_table(self, source_count=2):
        rows = self.read_csv()
        metrics = goal_metrics(self.goals)
        self.assertEqual(rows[0], ["Traffic source"] + [metric_label(metric) for metric in metrics])
        self.assertEqual(rows[1:], [[SOURCES[source]] + [metric_value(metric, source) for metric in metrics]
                                    for source in range(source_count)])

    def test_all_goal_counts_and_selection_methods(self):
        for count in (6, 7, 12, 21):
            self.configure(count)
            for method in ("explicit", "all", "configured"):
                with self.subTest(count=count, method=method):
                    self.log.unlink(missing_ok=True)
                    args = {"explicit": ["--goals", ",".join(map(str, self.goals))],
                            "all": ["--all-goals"], "configured": []}[method]
                    self.run_report(*args, "--no-cache")
                    self.assert_batches()
                    self.assert_table()

    def test_bytime_preserves_periods_and_metric_source_order(self):
        for group, limit in (("day", None), ("week", 2), ("month", 1)):
            with self.subTest(group=group, limit=limit):
                self.log.unlink(missing_ok=True)
                args = ["--group", group, "--no-cache"]
                if limit:
                    args.extend(["--limit", str(limit)])
                self.run_report(*args)
                self.assert_batches(grouped=True, limit=limit)
                self.assertTrue(all(request["params"]["group"] == group for request in self.requests()))
                sources = range(min(limit or 30, 2))
                metrics = goal_metrics(self.goals)
                expected = [["Period"] + [f"{SOURCES[source]} ({metric_label(metric, True)})"
                                           for metric in metrics for source in sources]]
                expected += [[period] + [metric_value(metric, source, index)
                                         for metric in metrics for source in sources]
                             for index, period in enumerate(PERIODS)]
                self.assertEqual(self.read_csv(), expected)

    def test_cache_reuse_and_limit_filter_separation(self):
        self.run_report()
        original = self.output.read_bytes()
        self.run_report()
        self.assertEqual(len(self.requests()), 2)
        self.assertEqual(self.output.read_bytes(), original)
        self.run_report("--limit", "1")
        self.assertEqual(len(self.requests()), 4)
        self.assert_table(source_count=1)
        filters = "ym:s:isRobot=='No' AND ym:s:deviceCategory=='mobile'"
        self.run_report("--filters", filters)
        self.assertEqual(len(self.requests()), 6)
        self.assertTrue(all(request["params"]["filters"] == filters for request in self.requests()[-2:]))
        self.run_report("--filters", filters)
        self.assertEqual(len(self.requests()), 6)
        self.assertEqual(len(list((self.counter / "reports").glob("*.csv"))), 3)

    def test_empty_reports(self):
        self.env["FAKE_CURL_EMPTY"] = "1"
        for grouped in (False, True):
            with self.subTest(grouped=grouped):
                self.log.unlink(missing_ok=True)
                self.run_report(*(["--group", "day"] if grouped else []), "--no-cache")
                self.assert_batches(grouped=grouped)
                if grouped:
                    self.assertEqual(self.read_csv(), [["Period"], *[[period] for period in PERIODS]])
                else:
                    self.assert_table(source_count=0)

    def test_cache_from_previous_single_request_format_is_ignored(self):
        goals = ",".join(map(str, self.goals))
        old_key = f"conv_12345_{PERIODS[0]}_{PERIODS[-1]}__{goals}___lastsign"
        checksum = subprocess.run(["cksum"], input=old_key, capture_output=True,
                                  text=True, check=True).stdout.split()[0]
        reports = self.counter / "reports"
        reports.mkdir()
        old_cache = reports / f"conversions_{PERIODS[0]}_{PERIODS[-1]}_{checksum}.csv"
        old_cache.write_text("old incomplete report\n")
        self.run_report()
        self.assert_batches()
        self.assert_table()

    def test_failed_later_batch_does_not_publish_partial_report(self):
        self.env["FAKE_CURL_FAILURE"] = "http"
        self.run_report(success=False)
        self.assertEqual(len(self.requests()), 2)
        self.assertFalse(self.output.exists())
        self.assertEqual(list((self.counter / "reports").iterdir()), [])
        self.assertEqual(list((self.root / "tmp").iterdir()), [])

    def test_inconsistent_batches_do_not_publish_report(self):
        for failure, args in (("sources", []), ("source_order", ["--group", "day"]),
                              ("csv", []), ("header", ["--group", "day"])):
            with self.subTest(failure=failure):
                self.log.unlink(missing_ok=True)
                self.env["FAKE_CURL_FAILURE"] = failure
                self.run_report(*args, success=False)
                self.assertFalse(self.output.exists())
                self.assertEqual(list((self.counter / "reports").iterdir()), [])

    def test_limits_rejected_before_network(self):
        for args in (("--limit", "0"), ("--limit", "-1"), ("--limit", "nope"),
                     ("--limit", "100001"), ("--group", "day", "--limit", "31")):
            with self.subTest(args=args):
                self.run_report(*args, success=False)
                self.assertEqual(self.requests(), [])


if __name__ == "__main__":
    if sys.argv[1:2] == ["--fake-curl"]:
        sys.exit(fake_curl(sys.argv[2:]))
    unittest.main()
