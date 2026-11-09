"""Load the same search implementation used by the sibling web backend."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / "web/backend/app/search_pipeline.py"
_spec = importlib.util.spec_from_file_location("exaone_search_pipeline", _path)
pipeline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pipeline)
