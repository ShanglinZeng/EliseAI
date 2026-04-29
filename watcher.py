import shutil
import time
from pathlib import Path
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from process_lead import run_pipeline_on_file

WATCH_DIR = Path("inputs")
PROCESSED_DIR = WATCH_DIR / "processed"
FAILED_DIR = WATCH_DIR / "failed"
WATCH_DIR.mkdir(exist_ok=True)
PROCESSED_DIR.mkdir(exist_ok=True)
FAILED_DIR.mkdir(exist_ok=True)


def _wait_for_file_ready(path: Path, max_wait: int = 30) -> bool:
    """Wait until file size stops changing, indicating write is complete."""
    last_size = -1
    for _ in range(max_wait):
        try:
            current_size = path.stat().st_size
        except FileNotFoundError:
            return False
        if current_size == last_size and current_size > 0:
            return True
        last_size = current_size
        time.sleep(1)
    return False


class LeadFileHandler(FileSystemEventHandler):
    def on_created(self, event):
        self._handle(event)

    def on_modified(self, event):
        self._handle(event)

    def _handle(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".csv":
            return
        # Skip files that are inside processed/ or failed/ subdirs
        if PROCESSED_DIR in path.parents or FAILED_DIR in path.parents:
            return

        print(f"\n[trigger] New file detected: {path.name}")

        if not _wait_for_file_ready(path):
            print(f"[trigger] File not ready, skipping: {path.name}")
            return

        try:
            run_pipeline_on_file(str(path))
            target = PROCESSED_DIR / path.name
            shutil.move(str(path), str(target))
            print(f"[trigger] Done. Moved to {target}")
        except Exception as e:
            print(f"[trigger] Pipeline failed for {path.name}: {e}")
            target = FAILED_DIR / path.name
            shutil.move(str(path), str(target))
            print(f"[trigger] Moved to {target} for review")


def main():
    print(f"Watching {WATCH_DIR.resolve()} for new lead CSVs...")
    print(f"  Processed files -> {PROCESSED_DIR}/")
    print(f"  Failed files    -> {FAILED_DIR}/")
    print("Press Ctrl-C to stop.\n")

    observer = Observer()
    observer.schedule(LeadFileHandler(), str(WATCH_DIR), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\nWatcher stopped.")
    observer.join()


if __name__ == "__main__":
    main()