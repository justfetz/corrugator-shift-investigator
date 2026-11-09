"""Write the prebuilt store folder: `python -m investigator.prebuilt <folder>`.

Run once at image build time (see Dockerfile). At boot, a RecordStore pointed at
the folder (INVESTIGATOR_PREBUILT) loads it in a fraction of the time generating
takes; a folder that does not match the code or the inputs is ignored.
"""
import sys
import time

from .record_store import build_prebuilt, RecordStore


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m investigator.prebuilt <folder>", file=sys.stderr)
        return 2
    started = time.perf_counter()
    manifest = build_prebuilt(argv[0])
    built = time.perf_counter() - started
    store = RecordStore(prebuilt=argv[0])
    try:
        print(f"prebuilt {argv[0]}: {manifest['counts']} hash {manifest['content_hash'][:12]} "
              f"built in {built:.1f}s; boots as {store.boot_source} in {store.boot_seconds*1000:.0f} ms")
    finally:
        store.close()
    return 0 if store.boot_source == "prebuilt" else 1


if __name__ == "__main__":
    sys.exit(main())
