"""Local, standard-library-only OpenAI-compatible test server. No real API calls."""
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass  # Never log headers, keys or message contents.

    def json_response(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            if not self.path.endswith('/chat/completions'):
                return self.json_response(404, {'error': {'message': 'unknown path'}})
            mode = self.path.strip('/').split('/')[0]
            if mode == 'unauthorized':
                return self.json_response(401, {'error': {'message': 'invalid key mock-key'}})
            if mode == 'forbidden':
                data = ('denied ' * 100).encode()
                self.send_response(403)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            assert self.headers.get('Authorization') == 'Bearer mock-key'
            assert self.headers.get('Content-Type', '').startswith('application/json')
            assert body['model'] == 'mock-exaone'
            assert 'parse_reasoning' not in body
            assert body['temperature'] == 1 and body['top_p'] == .95
            if not body['stream']:
                assert body['messages'] == [{'role': 'user', 'content': 'hi'}]
                assert body['max_tokens'] == 8
                assert body['chat_template_kwargs'] == {'enable_thinking': False}
                return self.json_response(200, {'choices': [{'message': {'role': 'assistant', 'content': 'hi'}}]})
            assert body['max_tokens'] == 8192
            assert body['chat_template_kwargs'] == {'enable_thinking': True}
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True

            def chunk(**delta):
                data = json.dumps({'choices': [{'delta': delta}]}, ensure_ascii=False)
                self.wfile.write(('data: ' + data + '\r\n\r\n').encode('utf-8'))
                self.wfile.flush()

            self.wfile.write(b': heartbeat\r\n\r\n')
            if mode == 'separate':
                chunk(reasoning_content='분리된 ')
                chunk(reasoning_content='생각', content='최종 ')
                chunk(content='답변')
            elif mode == 'think':
                for text in ['앞', '<thi', 'nk>경계 ', '생각</th', 'ink>', '뒤 답변']:
                    chunk(content=text)
            elif mode == 'thought':
                for text in ['<th', 'ought>다른 ', '생각</tho', 'ught>답변']:
                    chunk(content=text)
            elif mode == 'slow':
                chunk(content='부분 응답')
                time.sleep(3)
                chunk(content='이 부분은 취소 뒤')
            elif mode == 'disconnect':
                chunk(content='받은 내용')
                return
            else:
                chunk(reasoning_content='검증 중입니다.')
                chunk(content='# 가짜 응답\n\n로컬 서버가 응답했습니다.')
            self.wfile.write(b'data: [DONE]\r\n\r\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except (AssertionError, ValueError, KeyError) as exc:
            self.json_response(400, {'error': {'message': 'mock request validation failed: ' + type(exc).__name__}})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Mock server: http://127.0.0.1:{args.port}', flush=True)
    server.serve_forever()
