"""Disposable subprocess: bounds extraction time and prevents cancelled work lingering."""
import json
import sys
from .attachments import AttachmentError, MAX_FILE, extract


def main():
    try:
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (1024 ** 3, 1024 ** 3))
        except ImportError:
            pass
        result = extract(sys.argv[1], sys.stdin.buffer.read(MAX_FILE + 1))
    except AttachmentError as error:
        result = {"error": dict(code=error.code, message=error.message, status=error.status)}
    except Exception:
        result = {"error": dict(code="extraction_failed", message="파일에서 텍스트를 추출할 수 없습니다.", status=422)}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
