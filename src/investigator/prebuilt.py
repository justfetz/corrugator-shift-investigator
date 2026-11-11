"""Write the prebuilt store folder: `python -m investigator.prebuilt [--as-of YYYY-MM-DD] <folder>`.

Run once at image build time (see Dockerfile). The folder holds the rolling year that ends on
`--as-of`, else on INVESTIGATOR_AS_OF (the golden set's window), else the served data from
1 January 2025 to yesterday (issues #35, #100): the window is fixed when the image is built,
and the nightly deploy builds the next one. At boot, a
RecordStore pointed at the folder (INVESTIGATOR_PREBUILT) takes that window and loads it in
a fraction of the time generating takes; a folder that does not match the code or the
inputs is ignored.
"""
import argparse
import sys
import time

from .record_store import RecordStore, build_prebuilt, dataset_window


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m investigator.prebuilt", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--as-of", help="last calendar day of a rolling year (YYYY-MM-DD); default INVESTIGATOR_AS_OF, else 2025-01-01 to yesterday")
    parser.add_argument("folder")
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exit:
        return exit.code
    window = dataset_window(as_of=args.as_of, prebuilt="")
    started = time.perf_counter()
    manifest = build_prebuilt(args.folder, window=window)
    built = time.perf_counter() - started
    store = RecordStore(window=window, prebuilt=args.folder)
    try:
        print(f"prebuilt {args.folder}: window {manifest['first_day']} to {manifest['last_day']} "
              f"({manifest['production_days']} production days) {manifest['counts']} hash {manifest['content_hash'][:12]} "
              f"built in {built:.1f}s; boots as {store.boot_source} in {store.boot_seconds*1000:.0f} ms")
    finally:
        store.close()
    return 0 if store.boot_source == "prebuilt" else 1


if __name__ == "__main__":
    sys.exit(main())
