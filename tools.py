import os, subprocess, base64, json
import requests as req

def _get_cfg():
    from config import CHARACTER_CARD
    import json, os
    if os.path.exists('settings.json'):
        with open('settings.json') as f:
            s = json.load(f)
        return s.get('api_base','').rstrip('/'), s.get('api_key',''), s.get('model','')
    return '', '', ''

TOOLS = [
    {"type":"function","function":{
        "name":"open_program",
        "description":"打开Windows程序或文件",
        "parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}
    }},
    {"type":"function","function":{
        "name":"run_python",
        "description":"执行Python代码并返回输出",
        "parameters":{"type":"object","properties":{"code":{"type":"string"}},"required":["code"]}
    }},
    {"type":"function","function":{
        "name":"read_file",
        "description":"读取文本文件内容",
        "parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}
    }},
    {"type":"function","function":{
        "name":"write_file",
        "description":"写入内容到文件",
        "parameters":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}
    }},
    {"type":"function","function":{
        "name":"screenshot_and_understand",
        "description":"截屏并用AI理解屏幕内容",
        "parameters":{"type":"object","properties":{"question":{"type":"string"}},"required":["question"]}
    }},
    {"type":"function","function":{
        "name":"read_document",
        "description":"读取PDF/EPUB/TXT文档",
        "parameters":{"type":"object","properties":{"path":{"type":"string"},"page_or_section":{"type":"string"}},"required":["path"]}
    }},
    {"type":"function","function":{
        "name":"save_memory",
        "description":"记住重要信息",
        "parameters":{"type":"object","properties":{"key":{"type":"string"},"value":{"type":"string"}},"required":["key","value"]}
    }},
    {"type":"function","function":{
        "name":"get_active_window",
        "description":"获取用户当前正在操作的窗口/软件名称和标题（Windows）",
        "parameters":{"type":"object","properties":{}}
    }},
    {"type":"function","function":{
        "name":"list_running_apps",
        "description":"列出用户当前正在运行的应用程序",
        "parameters":{"type":"object","properties":{"filter":{"type":"string","description":"可选，过滤关键词"}}},
    }},
]

# ── Tool implementations ──────────────────────────

def open_program(path):
    try: os.startfile(path); return f"已打开: {path}"
    except Exception as e: return f"失败: {e}"

def run_python(code):
    try:
        with open('_tmp.py','w',encoding='utf-8') as f: f.write(code)
        r = subprocess.run(['python','_tmp.py'],capture_output=True,text=True,timeout=30)
        return (r.stdout+r.stderr).strip()[:2000] or '（无输出）'
    except Exception as e: return f"失败: {e}"

def read_file(path):
    try:
        with open(path,'r',encoding='utf-8') as f: return f.read()[:4000]
    except Exception as e: return f"失败: {e}"

def write_file(path, content):
    try:
        with open(path,'w',encoding='utf-8') as f: f.write(content)
        return f"已写入: {path}"
    except Exception as e: return f"失败: {e}"

def screenshot_and_understand(question):
    try:
        # Use mss first
        try:
            import mss, mss.tools, io
            with mss.mss() as sct:
                sct_img = sct.grab(sct.monitors[1])
                b64 = base64.b64encode(mss.tools.to_png(sct_img.rgb, sct_img.size)).decode()
        except ImportError:
            import pyautogui, io
            img = pyautogui.screenshot()
            buf = io.BytesIO(); img.save(buf, format='PNG')
            b64 = base64.b64encode(buf.getvalue()).decode()

        api_base, api_key, model = _get_cfg()
        if not api_base: return "未配置API"
        resp = req.post(f"{api_base}/chat/completions",
            headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},
            json={"model":model,"max_tokens":800,"messages":[{"role":"user","content":[
                {"type":"image_url","image_url":{"url":f"data:image/png;base64,{b64}"}},
                {"type":"text","text":f"请回答：{question}，用中文简洁回答。"}
            ]}]})
        return resp.json()['choices'][0]['message']['content']
    except Exception as e: return f"失败: {e}"

def read_document(path, page_or_section=None):
    import os, re
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in ('.txt','.md'):
            with open(path,'r',encoding='utf-8') as f: return f.read()[:5000]
        elif ext == '.pdf':
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                pages = pdf.pages[:3]
                if page_or_section:
                    nums = re.findall(r'\d+', page_or_section)
                    if nums: pages = [pdf.pages[int(nums[0])-1]]
                return '\n---\n'.join([p.extract_text() or '' for p in pages])[:5000]
        elif ext == '.epub':
            import ebooklib; from ebooklib import epub; from html.parser import HTMLParser
            class E(HTMLParser):
                def __init__(self): super().__init__(); self.t=[]
                def handle_data(self,d): self.t.append(d)
            book = epub.read_epub(path)
            items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))[:2]
            res = ''
            for item in items:
                p = E(); p.feed(item.get_content().decode('utf-8',errors='ignore'))
                res += ''.join(p.t) + '\n---\n'
            return res[:5000]
        return f"不支持: {ext}"
    except Exception as e: return f"失败: {e}"

def get_active_window():
    """Get the currently focused window title and process (Windows)."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            import psutil
            process = psutil.Process(pid.value).name()
        except Exception:
            process = 'unknown'
        return f"当前活动窗口：{title}（进程：{process}）"
    except Exception as e:
        return f"获取失败（非Windows系统？）: {e}"

def list_running_apps(filter=None):
    """List running application window titles."""
    try:
        import ctypes
        EnumWindows = ctypes.windll.user32.EnumWindows
        GetWindowText = ctypes.windll.user32.GetWindowTextW
        GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
        IsWindowVisible = ctypes.windll.user32.IsWindowVisible

        results = []
        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
        def foreach_window(hwnd, lParam):
            if IsWindowVisible(hwnd):
                length = GetWindowTextLength(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    GetWindowText(hwnd, buf, length + 1)
                    title = buf.value.strip()
                    if title and (not filter or filter.lower() in title.lower()):
                        results.append(title)
            return True
        EnumWindows(foreach_window, 0)
        return "正在运行的窗口：\n" + '\n'.join(results[:30])
    except Exception as e:
        # Fallback: use psutil
        try:
            import psutil
            procs = set()
            for p in psutil.process_iter(['name']):
                try:
                    n = p.info['name']
                    if n and (not filter or filter.lower() in n.lower()):
                        procs.add(n)
                except: pass
            return "运行中的进程：\n" + '\n'.join(sorted(procs)[:40])
        except Exception as e2:
            return f"失败: {e}, {e2}"

def _save_memory(key, value):
    from memory import save_memory; save_memory(key, value); return f"已记住: {key}"

def execute_tool(name, args):
    if name == 'open_program':              return open_program(**args)
    if name == 'run_python':               return run_python(**args)
    if name == 'read_file':                return read_file(**args)
    if name == 'write_file':               return write_file(**args)
    if name == 'screenshot_and_understand':return screenshot_and_understand(**args)
    if name == 'read_document':            return read_document(**args)
    if name == 'save_memory':              return _save_memory(**args)
    if name == 'get_active_window':        return get_active_window()
    if name == 'list_running_apps':        return list_running_apps(**args)
    return f"未知工具: {name}"
