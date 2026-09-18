# Combine conversion batches using POSIX awk; keep the original CSV escaping.
# Usage: LC_ALL=C awk -v mode=table -f merge_conversions.awk FILE GOALS HELPERS ...

function fail(message) {
    print "Error: Cannot combine conversion reports: " message > "/dev/stderr"
    exit 1
}

function value(raw) {
    if (substr(raw, 1, 1) == "\"") {
        raw = substr(raw, 2, length(raw) - 2)
        gsub(/""/, "\"", raw)
    }
    return raw
}

# Return the number of fields in one logical CSV record, or zero at EOF.
# Fields stay escaped: only delimiters outside quotes split them.
function read_csv(path, fields, bom,    status, line, i, c, field, count, state) {
    count = 1
    field = ""
    state = 0                 # 0: unquoted, 1: quoted, 2: closing quote
    while ((status = (getline line < path)) > 0) {
        if (bom) {
            sub(/^\357\273\277/, "", line)
            bom = 0
        }
        for (i = 1; i <= length(line); i++) {
            c = substr(line, i, 1)
            if (c == "\r" && i == length(line) && state != 1) continue
            if (state == 1) {
                if (c == "\"") state = 2
            } else if (c == ",") {
                fields[count++] = field
                field = ""
                state = 0
                continue
            } else if (c == "\"") {
                if (state != 2 && field != "") fail("Unexpected quote in " path)
                state = 1
            } else if (state == 2) {
                fail("Unexpected text after closing quote in " path)
            }
            field = field c
        }
        if (state != 1) {
            fields[count] = field
            return count
        }
        field = field "\n"
    }
    if (status < 0) fail("Cannot read " path)
    if (state == 1) fail("Unclosed quote in " path)
    return 0
}

function goal_columns(fields, keep,    i, result) {
    result = ""
    for (i = 2; i <= keep + 1; i++) result = result "," fields[i]
    return result
}

BEGIN {
    if ((mode != "table" && mode != "bytime") || ARGC < 4 || (ARGC - 1) % 3)
        fail("Expected mode and FILE GOALS HELPERS triples")
    for (arg = 1; arg < ARGC; arg += 3) {
        path = ARGV[arg]
        goal_metrics = ARGV[arg + 1] * 3
        metric_count = goal_metrics + ARGV[arg + 2]
        if (goal_metrics < 3 || metric_count <= goal_metrics) fail("Invalid batch metadata")
        batch++
        width = read_csv(path, fields, 1)
        if (!width || value(fields[1]) == "") fail("Empty CSV header: " path)
        if (mode == "table") {
            if (width - 1 != metric_count) fail("Unexpected metric columns in " path)
            keep = goal_metrics
        } else {
            # Bytime: period, then all sources for metric 1, metric 2, ...
            if ((width - 1) % metric_count) fail("Unexpected time-series columns in " path)
            sources = (width - 1) / metric_count
            keep = goal_metrics * sources
            if (batch > 1 && sources != source_count) fail("Sources changed between batches")
            source_count = sources
            for (i = 1; i <= sources; i++) {
                name = value(fields[1 + keep + i])
                if (batch > 1 && name != source_names[i]) fail("Source order changed between batches")
                source_names[i] = name
            }
        }
        if (batch == 1) {
            key_name = value(fields[1])
            header = fields[1]
        } else if (value(fields[1]) != key_name) fail("Row dimension changed between batches")
        header = header goal_columns(fields, keep)
        rows = 0
        while ((count = read_csv(path, fields, 0)) > 0) {
            if (count != width) fail("Inconsistent CSV row width in " path)
            key = value(fields[1])
            if (seen[key] == batch) fail("Duplicate row key in " path)
            seen[key] = batch
            rows++
            if (batch == 1) {
                row_keys[rows] = key
                combined[key] = fields[1]
            } else if (!(key in combined)) fail("Row keys changed between batches")
            combined[key] = combined[key] goal_columns(fields, keep)
        }
        close(path)
        if (batch > 1 && rows != row_count) fail("Row keys changed between batches")
        row_count = rows
    }
    # Nothing reaches stdout until all batches pass validation.
    print "\357\273\277" header
    for (i = 1; i <= row_count; i++) print combined[row_keys[i]]
    exit
}
