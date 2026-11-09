"""Local, standard-library-only OpenAI-compatible test server. No real API calls."""
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs


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

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == '/':
            return self.json_response(200, {'ok': True})
        if url.path != '/search':
            return self.json_response(404, {'error': 'not found'})
        query = parse_qs(url.query)
        if query.get('format') != ['json']:
            return self.json_response(400, {'error': 'format required'})
        title = query.get('q', ['EXAONE'])[0]
        rows = [{'url': f'https://example.test/source/{index}', 'title': title + f' 자료 {index}', 'content': '<b>검색 근거</b> ' + ('설명 ' * 300)} for index in range(1, 10)]
        rows.insert(1, {'url': 'https://example.test/source/1#fragment', 'title': 'duplicate', 'content': 'duplicate'})
        return self.json_response(200, {'results': rows})

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            if not self.path.endswith('/chat/completions'):
                return self.json_response(404, {'error': {'message': 'unknown path'}})
            mode = self.path.strip('/').split('/')[0]
            if mode == 'unauthorized':
                return self.json_response(401, {'error': {'message': 'invalid key mock-key'}})
            if mode == 'servererror':
                return self.json_response(500, {'error': {'message': 'mock server error'}})
            if mode == 'exaone401':
                return self.json_response(401, {'result_code': 'UNAUTHORIZED', 'description': '공식 키 오류', 'message': 'fallback'})
            if mode == 'forbidden':
                data = ('denied ' * 100).encode()
                self.send_response(403)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if self.headers.get('Authorization') == 'Bearer limited':
                data = b'{"error":{"message":"Rate limit"}}'
                self.send_response(429); self.send_header('Retry-After', '3'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data); return
            if self.headers.get('Authorization') == 'Bearer bad':
                return self.json_response(401, {'error': {'message': 'Invalid key'}})
            assert self.headers.get('Authorization') in ('Bearer mock-key', 'Bearer slow')
            assert self.headers.get('Content-Type', '').startswith('application/json')
            assert body['model'] == 'mock-exaone'
            assert 'parse_reasoning' not in body
            if not body['stream'] and body['max_tokens'] == 512:
                assert 'EXAONE_SEARCH_QUERIES' in body['messages'][0]['content']
                assert body.get('chat_template_kwargs') == {'enable_thinking': False}
                question = body['messages'][-1]['content']
                answer = 'not JSON' if question == 'fallback' else json.dumps({'queries': [question, question + ' 근거']}, ensure_ascii=False)
                return self.json_response(200, {'choices': [{'message': {'role': 'assistant', 'content': answer}}]})
            assert body['temperature'] == 1 and body['top_p'] == .95
            if not body['stream']:
                assert body['messages'] == [{'role': 'user', 'content': 'hi'}]
                assert body['max_tokens'] == 8
                assert body['chat_template_kwargs'] == {'enable_thinking': False}
                if mode == 'checkempty':
                    return self.json_response(200, {'choices': [{'message': {'role': 'assistant', 'reasoning_content': '생각', 'content': ''}}]})
                return self.json_response(200, {'choices': [{'message': {'role': 'assistant', 'content': 'hi'}}]})
            assert body['max_tokens'] == 8192
            if mode == 'parallel':
                assert body['chat_template_kwargs'] == {'enable_thinking': body['messages'][-1]['content'] != '대화 B'}
            else:
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
            if mode == 'parallel':
                user = body['messages'][-1]['content']
                chunk(reasoning_content='생각: ' + user)
                for part in ['시작: ' + user + '\n', '중간: ' + user + '\n', '완료: ' + user]:
                    chunk(content=part)
                    time.sleep(.3)
            elif mode == 'separate':
                chunk(reasoning_content='분리된 ')
                chunk(reasoning_content='생각', content='최종 ')
                chunk(content='답변')
            elif mode == 'think':
                for text in ['앞', '<thi', 'nk>경계 ', '생각</th', 'ink>', '뒤 답변']:
                    chunk(content=text)
            elif mode == 'thought':
                for text in ['<th', 'ought>다른 ', '생각</tho', 'ught>답변']:
                    chunk(content=text)
            elif mode == 'usage':
                chunk(reasoning_content='사용량 생각')
                self.wfile.write(b'data: {"choices": [], "usage": {"completion_tokens": 2}}\r\n\r\n')
                chunk(content='사용량 답변')
                self.wfile.write(b'data: {"choices": [], "usage": {"total_tokens": 12}}\r\n\r\n')
                self.wfile.write(b'data: {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 567, "reasoning_tokens": 1234}}\r\n\r\n')
            elif mode == 'leading':
                for text in ['\n', '\n  ', '<thi', 'nk>생각</think>', '\n\n', '  첫 줄', '\n\n  둘째 줄  ']:
                    chunk(content=text)
            elif mode == 'fragmented':
                payload = ''.join('data: ' + json.dumps({'choices': [{'delta': delta}]}, ensure_ascii=False) + '\r\n\r\n'
                                  for delta in [{'reasoning_content': '잘린 줄 생각'}, {'content': '잘린 줄 답변'}])
                data = (payload + 'data: [DONE]\r\n\r\n').encode('utf-8')
                for start in range(0, len(data), 3):
                    self.wfile.write(data[start:start + 3])
                    self.wfile.flush()
                    time.sleep(.001)
                return
            elif mode == 'slow':
                chunk(content='부분 응답')
                time.sleep(3)
                chunk(content='이 부분은 취소 뒤')
            elif mode == 'aftererror':
                chunk(content='partial')
                self.wfile.write(b'data: {"error":{"message":"429 after streaming"}}\n\n'); self.wfile.flush()
            elif mode == 'disconnect':
                chunk(content='받은 내용')
                return
            elif mode == 'search':
                context = '\n'.join(message['content'] for message in body['messages'] if message['role'] == 'system')
                assert 'EXAONE_SEARCH_CONTEXT' in context and 'Asia/Seoul' in context
                chunk(reasoning_content='검색 근거를 검토합니다.')
                time.sleep(.03)
                if '검색 실패:' in context:
                    chunk(content='검색 없이 일반 지식으로 답변합니다.')
                else:
                    assert '[1]' in context and 'https://example.test/source/1' in context
                    chunk(content='검색 근거에 따라 답변합니다. [1][2]')
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
