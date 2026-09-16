#!/usr/bin/env python3
"""Stream-merge resource traces and report aggregate peak usage."""

import argparse
import csv
import heapq
from contextlib import ExitStack
from pathlib import Path


VALUE_COLUMNS = ("allocated_nodes", "avgpcon", "minpcon", "maxpcon")


def read_row(reader, indexes, path):
    line = next(reader, None)
    if line is None:
        return None
    fields = line.rstrip("\r\n").split(",")
    try:
        timestamp = int(float(fields[indexes["time"]]))
        allocated = int(fields[indexes["allocated_nodes"]])
        values = (
            allocated,
            float(fields[indexes["avgpcon"]]),
            float(fields[indexes["minpcon"]]),
            float(fields[indexes["maxpcon"]]),
        )
    except (IndexError, ValueError) as error:
        raise ValueError(f"invalid resource row in {path}: {line!r}") from error
    return timestamp, values


def summarize(paths):
    states = [(0, 0.0, 0.0, 0.0) for _ in paths]
    heap = []
    readers = []

    with ExitStack() as stack:
        for index, path in enumerate(paths):
            stream = stack.enter_context(path.open())
            header = stream.readline().rstrip("\r\n").split(",")
            required = ("time",) + VALUE_COLUMNS
            missing = [column for column in required if column not in header]
            if missing:
                raise ValueError(f"{path} is missing: {', '.join(missing)}")
            indexes = {column: header.index(column) for column in required}
            readers.append((iter(stream), indexes, path))
            row = read_row(readers[-1][0], indexes, path)
            if row is not None:
                heapq.heappush(heap, (row[0], index, row[1]))

        peak_nodes = 0
        peak_nodes_time = 0
        peak_avgpcon = 0.0
        peak_avgpcon_time = 0
        timestamps = 0

        while heap:
            timestamp = heap[0][0]
            while heap and heap[0][0] == timestamp:
                _, index, values = heapq.heappop(heap)
                states[index] = values
                reader, indexes, path = readers[index]
                row = read_row(reader, indexes, path)
                if row is not None:
                    heapq.heappush(heap, (row[0], index, row[1]))

            allocated = sum(state[0] for state in states)
            avgpcon = sum(state[1] for state in states)
            if allocated > peak_nodes:
                peak_nodes = allocated
                peak_nodes_time = timestamp
            if avgpcon > peak_avgpcon:
                peak_avgpcon = avgpcon
                peak_avgpcon_time = timestamp
            timestamps += 1

    return peak_nodes, peak_nodes_time, peak_avgpcon, peak_avgpcon_time, timestamps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resource_traces", nargs="+", type=Path)
    args = parser.parse_args()

    result = summarize(args.resource_traces)
    print(f"input_files={len(args.resource_traces)}")
    print(f"resource_timestamps={result[4]}")
    print(f"peak_allocated_nodes={result[0]}")
    print(f"peak_allocated_time={result[1]}")
    print(f"peak_avgpcon={result[2]:.6f}")
    print(f"peak_avgpcon_time={result[3]}")


if __name__ == "__main__":
    main()
