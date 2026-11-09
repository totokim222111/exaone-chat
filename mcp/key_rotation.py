"""Use the same header-only failover policy as the web API."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / 'web/backend/app/key_rotation.py'
_spec = importlib.util.spec_from_file_location('exaone_key_rotation', _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
KeySet, KeyPool, POOL = _module.KeySet, _module.KeyPool, _module.POOL
Exhausted, PlannerError = _module.Exhausted, _module.PlannerError
rotating_response, retry_seconds = _module.rotating_response, _module.retry_seconds
