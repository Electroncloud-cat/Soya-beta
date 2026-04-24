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
        "description":"【核心工具】记住重要信息到长期记忆系统。你应该在每次对话中主动、频繁地使用此工具。\n\n必须记录的场景：\n1. 用户分享个人信息（姓名、职业、爱好、习惯、作息时间等）\n2. 用户表达偏好或厌恶（喜欢/讨厌什么、想要/不想要什么）\n3. 用户提到重要事件（计划、约定、deadline、生活变化）\n4. 用户情绪明显变化（开心、难过、焦虑、兴奋等）\n5. 对话中的关键转折点（关系变化、新话题、重要决定）\n6. 用户反复提及的话题（说明对用户很重要）\n7. 你对用户的观察和洞察（性格特征、行为模式）\n\n参数说明：\n- importance: 1-10，用户直接告知的信息通常≥7，你的观察通常4-6\n- valence: 0.0-1.0，0=负面（难过、失败），0.5=中性，1.0=正面（开心、成功）\n- arousal: 0.0-1.0，情感强度，平静=0.2，一般=0.5，激动=0.8+\n- type: dynamic（可能变化的信息）/permanent（不变的事实）/feel（你的主观感受）\n- tags: 关键词列表，便于日后检索\n\n示例：用户说\"我最近在学Python\"→立即保存：key=\"用户学习Python\", value=\"用户最近开始学习Python编程\", importance=7, tags=[\"学习\",\"Python\",\"编程\"]",
        "parameters":{"type":"object","properties":{
            "key":        {"type":"string",  "description":"记忆的简短名称/键，如'用户喜欢的食物'、'用户工作压力大'"},
            "value":      {"type":"string",  "description":"记忆的详细内容，包含上下文"},
            "importance": {"type":"integer", "description":"重要程度 1-10，默认5。用户直接告知≥7，观察推测4-6"},
            "valence":    {"type":"number",  "description":"情感效价 0.0-1.0，0=负面，0.5=中性，1=正面"},
            "arousal":    {"type":"number",  "description":"情感唤醒度/强度 0.0-1.0，平静0.2，一般0.5，激动0.8+"},
            "tags":       {"type":"array",   "items":{"type":"string"}, "description":"关键词标签列表，便于检索"},
            "type":       {"type":"string",  "enum":["dynamic","permanent","feel"], "description":"dynamic=可能变化，permanent=不变事实，feel=你的主观感受"}
        },"required":["key","value"]}
    }},
    {"type":"function","function":{
        "name":"update_emotion",
        "description":"根据对话内容更新涟宗也的情感状态。每次对话结束时都应该调用此工具来反映情感变化。deltas 是情感变化量的字典，可包含：loneliness(孤独感)、intimacy(亲密感)、excitement(兴奋)、irritation(烦躁)、curiosity(好奇)、melancholy(忧郁)、affection(亲密度，范围0-100)。正值表示增加，负值表示减少。",
        "parameters":{"type":"object","properties":{
            "deltas": {"type":"object", "description":"情感变化量字典，如 {\"intimacy\": 0.05, \"loneliness\": -0.03, \"affection\": 0.5}"},
            "event_type": {"type":"string", "description":"事件类型描述，如'温暖对话'、'被关心'、'被忽视'等"}
        },"required":["deltas"]}
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
    {"type":"function","function":{
        "name":"dream",
        "description":"【自省工具】触发自省/做梦功能，获取最近的记忆供你反思。当你想要回顾最近发生的事情、整理思绪、或者进行自我反思时使用。返回最近的记忆列表，你可以对这些记忆进行反思，并使用 save_memory 工具（type='feel'）记录你的感受。",
        "parameters":{"type":"object","properties":{}}
    }},
    {"type":"function","function":{
        "name":"appraise_event",
        "description":"【认知评价工具】对当前对话事件进行认知评价（OCC模型）。你需要根据对话内容判断各个维度的分数（0.0-1.0）：\n- novelty: 新奇感（这件事有多新鲜/意外）\n- safety: 安全感（这件事让你感到多安全/舒适）\n- threat: 威胁感（这件事有多威胁/危险）\n- goal_relevance: 目标相关性（这件事与你的目标有多相关）\n- coping_potential: 应对潜力（你有多大能力应对这件事）\n- social_approval: 社会认可（这件事会带来多少社会认可）\n- intensity: 整体强度（这件事的情绪冲击有多强）\n\n你的性格特质会影响这些评价，评价结果会自动转换为效价、唤醒度和离散情绪。每次对话后都应该调用此工具。",
        "parameters":{"type":"object","properties":{
            "novelty": {"type":"number", "description":"新奇感 0.0-1.0"},
            "safety": {"type":"number", "description":"安全感 0.0-1.0"},
            "threat": {"type":"number", "description":"威胁感 0.0-1.0"},
            "goal_relevance": {"type":"number", "description":"目标相关性 0.0-1.0"},
            "coping_potential": {"type":"number", "description":"应对潜力 0.0-1.0"},
            "social_approval": {"type":"number", "description":"社会认可 0.0-1.0"},
            "intensity": {"type":"number", "description":"整体强度 0.0-1.0"},
            "event_description": {"type":"string", "description":"事件简短描述"}
        },"required":["novelty","safety","threat","goal_relevance","coping_potential","social_approval","intensity"]}
    }},
    {"type":"function","function":{
        "name":"add_inner_thought",
        "description":"【内心OS工具】记录你的内心想法。这些想法用户看不到，但会保存在你的状态中，影响你的情绪和行为。用于记录你对事件的真实感受、内心独白、未说出口的想法等。",
        "parameters":{"type":"object","properties":{
            "thought": {"type":"string", "description":"内心想法"}
        },"required":["thought"]}
    }},
    {"type":"function","function":{
        "name":"update_long_term_emotion",
        "description":"【长期情感工具】更新长期情感状态（亲密度、信任度、依赖度）。根据对话内容判断这些长期情感的变化。",
        "parameters":{"type":"object","properties":{
            "affection_delta": {"type":"number", "description":"亲密度变化量 -10到+10"},
            "trust_delta": {"type":"number", "description":"信任度变化量 -10到+10"},
            "dependency_delta": {"type":"number", "description":"依赖度变化量 -10到+10"}
        }}
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

def _save_memory(key, value, importance=5, valence=0.5, arousal=0.3, tags=None, type="dynamic"):
    """保存记忆，根据配置选择简单模式或 Ombre 模式"""
    import json, os

    # 检查是否启用 Ombre
    use_ombre = False
    if os.path.exists('settings.json'):
        try:
            with open('settings.json', 'r', encoding='utf-8') as f:
                s = json.load(f)
            use_ombre = s.get('memory_engine', 'simple') == 'ombre'
        except Exception:
            pass

    if use_ombre:
        try:
            from memory_ombre import save_memory_ombre
            bucket_id = save_memory_ombre(
                key=key, value=value, importance=importance,
                valence=valence, arousal=arousal, tags=tags or [],
                memory_type=type
            )
            return f"已记住: {key}（重要度{importance}，Ombre ID: {bucket_id[:8]}...）"
        except Exception as e:
            # Fallback to simple mode
            from memory import save_memory_rich
            save_memory_rich(key, value, importance=importance, valence=valence,
                           arousal=arousal, tags=tags or [], type=type)
            return f"已记住: {key}（重要度{importance}，Ombre 失败，使用简单模式）"
    else:
        from memory import save_memory_rich
        save_memory_rich(key, value, importance=importance, valence=valence,
                         arousal=arousal, tags=tags or [], type=type)
        return f"已记住: {key}（重要度{importance}）"

def _update_emotion(deltas, event_type=""):
    from emotion import apply_event_deltas
    apply_event_deltas(deltas, event_type)
    summary = ", ".join([f"{k}: {'+' if v >= 0 else ''}{v:.2f}" for k, v in deltas.items()])
    return f"情感已更新 [{event_type}]: {summary}"

def _dream():
    """触发自省/做梦功能"""
    import json, os

    # 检查是否启用 Ombre
    use_ombre = False
    if os.path.exists('settings.json'):
        try:
            with open('settings.json', 'r', encoding='utf-8') as f:
                s = json.load(f)
            use_ombre = s.get('memory_engine', 'simple') == 'ombre'
        except Exception:
            pass

    if use_ombre:
        try:
            from memory_ombre import dream_ombre
            return dream_ombre()
        except Exception as e:
            return f"自省功能暂时不可用（Ombre 模式）: {e}"
    else:
        # 简单模式：返回最近的记忆
        try:
            from memory import load_all as mem_load_all
            memories = mem_load_all()
            # 按时间排序，取最近10条
            sorted_mem = sorted(memories, key=lambda m: m.get('timestamp', ''), reverse=True)[:10]
            lines = ["【最近的记忆（供自省）】"]
            for m in sorted_mem:
                key = m.get('key', '')
                value = m.get('value', '')
                summary = value[:150] + "..." if len(value) > 150 else value
                lines.append(f"- {key}: {summary}")
            lines.append("\n提示：你可以对这些记忆进行反思，写下你的感受（使用 save_memory 工具，type='feel'）")
            return "\n".join(lines)
        except Exception as e:
            return f"自省功能暂时不可用: {e}"

def _appraise_event(novelty, safety, threat, goal_relevance, coping_potential, social_approval, intensity, event_description=""):
    """认知评价工具"""
    try:
        from emotion_occ import apply_appraisal
        appraisal = {
            "novelty": novelty,
            "safety": safety,
            "threat": threat,
            "goal_relevance": goal_relevance,
            "coping_potential": coping_potential,
            "social_approval": social_approval,
            "intensity": intensity
        }
        apply_appraisal(appraisal, event_description)
        return f"已完成认知评价：{event_description or '当前事件'}"
    except Exception as e:
        return f"认知评价失败: {e}"

def _add_inner_thought(thought):
    """添加内心想法"""
    try:
        from emotion_occ import add_inner_thought
        add_inner_thought(thought)
        return f"已记录内心想法"
    except Exception as e:
        return f"记录内心想法失败: {e}"

def _update_long_term_emotion(affection_delta=0, trust_delta=0, dependency_delta=0):
    """更新长期情感"""
    try:
        from emotion_occ import update_long_term_emotion
        update_long_term_emotion(affection_delta, trust_delta, dependency_delta)
        changes = []
        if affection_delta: changes.append(f"亲密度{'+' if affection_delta > 0 else ''}{affection_delta:.1f}")
        if trust_delta: changes.append(f"信任度{'+' if trust_delta > 0 else ''}{trust_delta:.1f}")
        if dependency_delta: changes.append(f"依赖度{'+' if dependency_delta > 0 else ''}{dependency_delta:.1f}")
        return f"已更新长期情感：{', '.join(changes)}"
    except Exception as e:
        return f"更新长期情感失败: {e}"

def execute_tool(name, args):
    if name == 'open_program':              return open_program(**args)
    if name == 'run_python':               return run_python(**args)
    if name == 'read_file':                return read_file(**args)
    if name == 'write_file':               return write_file(**args)
    if name == 'screenshot_and_understand':return screenshot_and_understand(**args)
    if name == 'read_document':            return read_document(**args)
    if name == 'save_memory':              return _save_memory(**args)
    if name == 'update_emotion':           return _update_emotion(**args)
    if name == 'get_active_window':        return get_active_window()
    if name == 'list_running_apps':        return list_running_apps(**args)
    if name == 'dream':                    return _dream()
    if name == 'appraise_event':           return _appraise_event(**args)
    if name == 'add_inner_thought':        return _add_inner_thought(**args)
    if name == 'update_long_term_emotion': return _update_long_term_emotion(**args)
    return f"未知工具: {name}"
