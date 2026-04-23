import json, os, datetime

MEMORY_FILE = 'memory.json'

def save_memory(key: str, value: str):
    data = load_all()
    data[key] = {"value": value, "time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}
    _write(data)

def load_all() -> dict:
    if not os.path.exists(MEMORY_FILE):
        return {}
    with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def delete_memory(key: str):
    data = load_all()
    data.pop(key, None)
    _write(data)

def get_memory_summary() -> str:
    data = load_all()
    if not data:
        return '（暂无记忆）'
    return '\n'.join([f"- {k}: {v['value']}（{v['time']}）" for k, v in data.items()])

def _write(data):
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
