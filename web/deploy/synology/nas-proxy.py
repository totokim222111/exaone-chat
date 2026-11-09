"""Edit only EXAONE DSM proxy entries; --dry-run only reads ReverseProxy.json."""
import argparse
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

CONFIG = Path("/usr/syno/etc/www/ReverseProxy.json")
BACKUPS = Path("/volume1/docker/exaone/backups")
GEN = ["/usr/syno/bin/synow3tool", "--gen-all"]


def plan(data):
    result, changes = copy.deepcopy(data), []
    if not isinstance(result, dict):
        raise ValueError("Unsupported DSM proxy configuration")
    for description, port, backend in (
        ("exaone_https", 443, {"fqdn": "127.0.0.1", "port": 61480, "protocol": 0}),
        ("exaone_http", 80, {"fqdn": "exaone.8mina.com", "port": 443, "protocol": 1}),
    ):
        matches = [(k, v) for k, v in result.items() if isinstance(v, dict) and v.get("description") == description]
        conflicts = [v for v in result.values() if isinstance(v, dict) and v.get("description") != description
                     and v.get("frontend", {}).get("fqdn") == "exaone.8mina.com" and v["frontend"].get("port") == port]
        if len(matches) > 1 or conflicts:
            raise ValueError("Conflicting EXAONE proxy entries")
        if matches:
            key, entry = matches[0]
            if entry.get("backend") != backend:
                entry["backend"] = backend
                changes.append(dict(action="update-backend", description=description, backend=backend))
        else:
            entry = dict(_key=str(uuid.uuid4()), description=description,
                frontend={"acl": None, "fqdn": "exaone.8mina.com", "https": {"hsts": False}, "port": port, "protocol": 1 if port == 443 else 0},
                backend=backend, customize_headers=[], proxy_connect_timeout=60, proxy_http_version=1,
                proxy_intercept_errors=False, proxy_read_timeout=600 if port == 443 else 60,
                proxy_send_timeout=600 if port == 443 else 60)
            result[str(uuid.uuid4())] = entry
            changes.append(dict(action="add", description=description, frontend=entry["frontend"], backend=backend,
                                proxy_read_timeout=entry["proxy_read_timeout"], proxy_send_timeout=entry["proxy_send_timeout"]))
    return result, changes


def atomic_replace(path, payload, reference):
    stat = reference.stat()
    descriptor, temporary = tempfile.mkstemp(prefix=".exaone-proxy-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        shutil.copystat(str(reference), temporary)
        os.chown(temporary, stat.st_uid, stat.st_gid)
        os.chmod(temporary, stat.st_mode & 0o7777)
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def command(args):
    subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        raw = CONFIG.read_bytes()
        updated, changes = plan(json.loads(raw))
        if args.dry_run:
            print(json.dumps(dict(dryRun=True, changes=changes), ensure_ascii=False, separators=(",", ":")))
            return 0
        if not changes:
            print("EXAONE proxy unchanged")
            return 0
        BACKUPS.mkdir(parents=True, exist_ok=True)
        backup = BACKUPS / ("ReverseProxy-before-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
        shutil.copy2(str(CONFIG), str(backup))
        original_stat = CONFIG.stat()
        os.chown(str(backup), original_stat.st_uid, original_stat.st_gid)
        os.chmod(str(backup), original_stat.st_mode & 0o7777)
        try:
            atomic_replace(CONFIG, json.dumps(updated, ensure_ascii=False, indent=2).encode("utf-8"), CONFIG)
            command(GEN)
            command(["/usr/bin/nginx", "-t"])
            command(["/usr/syno/bin/synow3tool", "--nginx=reload"])
        except Exception:
            atomic_replace(CONFIG, backup.read_bytes(), backup)
            try:
                command(GEN)
            except Exception:
                print("EXAONE proxy failed; backup restored, regeneration requires attention", file=sys.stderr)
                return 1
            print("EXAONE proxy failed; backup restored and configuration regenerated", file=sys.stderr)
            return 1
        print("EXAONE proxy updated: " + ", ".join(c["description"] for c in changes))
        return 0
    except Exception:
        print("EXAONE proxy validation failed; no configuration changes applied", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
