"""Verify the deployed Qwen model's Chinese chat and compatible HTTP API."""
import argparse
import datetime
import json
from pathlib import Path
import re
import time
import urllib.request


def request(base, path, payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(base + path, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=300) as response:
        return json.load(response)


def payload(model, messages, **extra):
    return {'model': model, 'messages': messages, 'temperature': 0,
            'max_tokens': 256, 'chat_template_kwargs': {'enable_thinking': False}, **extra}


def verify(base, model):
    report = {'base_url': base, 'model': model, 'checks': {},
              'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    health = request(base, '/health')
    assert health['status'] == 'ok', health
    models = request(base, '/v1/models')
    assert model in [item['id'] for item in models['data']], models
    report['checks']['health_and_models'] = {'health': health, 'models': models}
    cases = {
        'chinese_explanation': [{'role': 'user', 'content': '请用中文，用两句话解释CPU和GPU共享统一内存的好处。'}],
        'chinese_multiturn': [
            {'role': 'user', 'content': '我叫小林，我最喜欢的饮品是绿茶。请记住。'},
            {'role': 'assistant', 'content': '记住了，你叫小林，最喜欢绿茶。'},
            {'role': 'user', 'content': '我叫什么？我最喜欢什么饮品？请用一句中文回答。'}],
        'arithmetic': [{'role': 'user', 'content': '只输出一个整数，不要解释：123+456等于多少？'}],
    }
    for name, messages in cases.items():
        started = time.monotonic()
        response = request(base, '/v1/chat/completions', payload(model, messages))
        content = response['choices'][0]['message'].get('content') or ''
        if name == 'chinese_explanation':
            assert re.search(r'[\u4e00-\u9fff]', content), response
        elif name == 'chinese_multiturn':
            assert '小林' in content and '绿茶' in content, response
        else:
            assert content.strip() == '579', response
        report['checks'][name] = {'elapsed_seconds': round(time.monotonic() - started, 3),
                                  'content': content, 'response': response}
    ordinary = payload(model, [{'role': 'user', 'content': '请只用一句中文回答：你好，很高兴认识你。'}])
    ordinary.pop('chat_template_kwargs')
    started = time.monotonic()
    response = request(base, '/v1/chat/completions', ordinary)
    message = response['choices'][0]['message']
    content = message.get('content') or ''
    assert re.search(r'[\u4e00-\u9fff]', content), response
    assert not message.get('reasoning_content') and '<think>' not in content, response
    report['checks']['ordinary_client_default'] = {
        'elapsed_seconds': round(time.monotonic() - started, 3),
        'content': content, 'response': response}
    data = json.dumps(payload(model, [{'role': 'user', 'content': '请用中文给出三个学习Python的建议，每个建议一句话。'}],
                              stream=True), ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(base + '/v1/chat/completions', data=data,
                                 headers={'Content-Type': 'application/json'})
    chunks = []
    done = False
    started = time.monotonic()
    first_content = None
    with urllib.request.urlopen(req, timeout=300) as response:
        for raw in response:
            line = raw.decode('utf-8').strip()
            if not line.startswith('data: '):
                continue
            if line[6:] == '[DONE]':
                done = True
                break
            event = json.loads(line[6:])
            for choice in event.get('choices', []):
                text = choice.get('delta', {}).get('content')
                if text:
                    if first_content is None:
                        first_content = time.monotonic() - started
                    chunks.append(text)
    content = ''.join(chunks)
    assert done and re.search(r'[\u4e00-\u9fff]', content), {'done': done, 'content': content}
    report['checks']['streaming'] = {'done': done, 'chunks': len(chunks), 'content': content,
                                     'first_content_seconds': round(first_content, 3),
                                     'elapsed_seconds': round(time.monotonic() - started, 3)}
    report['passed'] = True
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:18080')
    parser.add_argument('--model', default='qwen38-27b-uncensored-q6-k')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = verify(args.base_url.rstrip('/'), args.model)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
