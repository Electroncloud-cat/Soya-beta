from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
import requests as req
import json, os, datetime, base64, re, time, queue, threading, logging
from memory import get_memory_summary, save_memory as mem_save, load_all as mem_load_all, delete_memory as mem_delete, get_feel_summary
from emotion_occ import load_state, save_state, apply_time_decay, on_session_start, on_message_received, build_prompt_block
from analysis_helper import run_analysis

# ─────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler('soya.log', encoding='utf-8')]
)
logger = logging.getLogger('soya')

app = Flask(__name__)

# ─────────────────────────────────────────
#  SSE — Server-Sent Events
# ─────────────────────────────────────────
_sse_clients = []          # list of queue.Queue, one per connected browser tab
_sse_lock = threading.Lock()
_last_proactive_time = 0.0  # timestamp of last proactive message sent
_last_user_message_time = 0.0   # 仅在用户（人类）发消息时更新

def push_sse_event(event_type: str, data: dict):
    """Broadcast an SSE event to every connected client."""
    payload = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            try:
                _sse_clients.remove(q)
            except ValueError:
                pass

# Shared activity status (updated by frontend, read by monitor widget)
_activity_status = {
    'page_visible': True,
    'mouse_idle': False,
    'active_window': '',
    'last_update': ''
}
_last_push_time = 0.0   # 上次前端推送时间戳，用于超时自动离线检测

SETTINGS_FILE   = 'settings.json'
HISTORY_FILE    = 'chat_history.json'
BOOK_CACHE_FILE = 'book_cache.json'
INJECTIONS_FILE = 'prompt_injections.json'
FRONTEND_DIR    = os.path.join(os.path.dirname(__file__), 'frontend')
AVATAR_DIR      = os.path.join(FRONTEND_DIR, 'avatars')
os.makedirs(AVATAR_DIR, exist_ok=True)

# Thread lock for analysis
ANALYSIS_LOCK = threading.Lock()

# Book store — persisted to book_cache.json so it survives server restarts
def _load_book_cache():
    if os.path.exists(BOOK_CACHE_FILE):
        try:
            with open(BOOK_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_book_cache(data):
    try:
        with open(BOOK_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

_book_data = _load_book_cache()

# ─────────────────────────────────────────
#  Settings / History helpers
# ─────────────────────────────────────────
def load_settings():
    defaults = {
        "api_base": "", "api_key": "",
        "model": "claude-sonnet-4-20250514",
        "models": ["claude-sonnet-4-20250514", "gpt-4o", "gpt-4o-mini"],
        "user_name": "初惠夏", "user_desc": "",
        "user_avatar": "", "ai_avatar": "",
        "live2d_enabled": False, "live2d_eye_tracking": True,
        "live2d_breathing": True, "live2d_ai_control": True,
        "live2d_model_path": "",
        "summary_prompt": "请用简洁的第三人称总结以上对话的主要内容、情感变化和关键信息，控制在200字以内。",
        "summary_keep_recent": 10,
        "context_rounds": 20,
        "proactive_require_page_visible": True,
        "proactive_interval_minutes": 20,
        "proactive_idle_minutes": 15,
        "analysis_threshold": 8000,  # Character count threshold for personality/relationship analysis
        "memory_engine": "simple",  # simple or ombre
        "ombre_buckets_dir": "./ombre_buckets",
        "ombre_vector_db": "./ombre_buckets/vectors.db",
        "ombre_decay_enabled": True,
        "ombre_decay_interval": 24,
        "ombre_archive_threshold": 0.3,
        "emotion_system": "occ"  # simple or occ
    }
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        defaults.update(saved)
    return defaults

def save_settings_file(data):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # Add default fields for analysis feature
        if 'personality_summary' not in data:
            data['personality_summary'] = ''
        if 'relationship_summary' not in data:
            data['relationship_summary'] = ''
        if 'chars_since_last_analysis' not in data:
            data['chars_since_last_analysis'] = 0
        return data
    return {
        "messages": [],
        "last_seen": None,
        "summary": "",
        "hidden_count": 0,
        "personality_summary": "",
        "relationship_summary": "",
        "chars_since_last_analysis": 0
    }

def save_history_file(data):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ─────────────────────────────────────────
#  Prompt Injections
# ─────────────────────────────────────────
def load_injections():
    """加载 prompt 注入条目"""
    if os.path.exists(INJECTIONS_FILE):
        try:
            with open(INJECTIONS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_injections(data):
    """保存 prompt 注入条目"""
    with open(INJECTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def apply_injections(system: str, messages: list) -> tuple:
    """将启用的注入条目插入到 system 或 messages 对应位置"""
    entries = [e for e in load_injections() if e.get('enabled', True)]
    entries.sort(key=lambda e: e.get('priority', 0))

    tops, bottoms, before_last, after_last = [], [], [], []
    for e in entries:
        pos = e.get('position', 'system_bottom')
        content = e.get('content', '').strip()
        if not content:
            continue
        if pos == 'system_top':
            tops.append(content)
        elif pos == 'system_bottom':
            bottoms.append(content)
        elif pos == 'before_last_user':
            before_last.append(content)
        elif pos == 'after_last_user':
            after_last.append(content)

    # 拼接 system
    if tops:
        system = '\n\n'.join(tops) + '\n\n' + system
    if bottoms:
        system = system + '\n\n' + '\n\n'.join(bottoms)

    # 插入 messages
    msgs = list(messages)
    # 找最后一条 user 消息的索引
    last_user_idx = None
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get('role') == 'user':
            last_user_idx = i
            break

    if last_user_idx is not None:
        # before_last: 在最后一条 user 消息之前插入
        for content in reversed(before_last):
            msgs.insert(last_user_idx, {'role': 'user', 'content': content})
        # after_last: 在最后一条 user 消息之后插入
        insert_after = last_user_idx + len(before_last) + 1
        for content in after_last:
            msgs.insert(insert_after, {'role': 'user', 'content': content})
            insert_after += 1

    return system, msgs

# ─────────────────────────────────────────
#  Personality & Relationship Analysis
# ─────────────────────────────────────────
def maybe_trigger_analysis(hist_data):
    """检查是否需要触发性格/关系分析，超阈值则异步执行"""
    try:
        s = load_settings()
        threshold = s.get('analysis_threshold', 8000)

        # 统计 hidden_count 之后所有消息的字符数
        msgs = hist_data.get('messages', [])
        hidden = hist_data.get('hidden_count', 0)
        recent = msgs[hidden:]

        total_chars = sum(len(str(m.get('content', ''))) for m in recent)

        # 更新字符计数（加锁）
        with ANALYSIS_LOCK:
            current_data = load_history()
            current_data['chars_since_last_analysis'] = total_chars
            save_history_file(current_data)

        if total_chars >= threshold:
            logger.info(f"[analysis] 触发分析：{total_chars} 字符 >= {threshold} 阈值")
            # 启动后台分析线程
            threading.Thread(
                target=run_analysis,
                args=(hist_data, s, load_history, save_history_file, ANALYSIS_LOCK),
                daemon=True
            ).start()
    except Exception as e:
        logger.error(f"[maybe_trigger_analysis] 异常：{e}")

# ─────────────────────────────────────────
#  Screenshot helper
# ─────────────────────────────────────────
def take_screenshot_b64():
    try:
        import mss, mss.tools
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            sct_img = sct.grab(monitor)
            png_bytes = mss.tools.to_png(sct_img.rgb, sct_img.size)
            return base64.b64encode(png_bytes).decode()
    except ImportError:
        pass
    except Exception as e:
        raise RuntimeError(f"mss截图失败: {e}")
    try:
        import pyautogui, io
        img = pyautogui.screenshot()
        buf = io.BytesIO(); img.save(buf, format='PNG')
        return base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        pass
    try:
        from PIL import ImageGrab
        import io
        img = ImageGrab.grab()
        buf = io.BytesIO(); img.save(buf, format='PNG')
        return base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        raise RuntimeError("截图失败：请安装 mss（pip install mss）")
    except Exception as e:
        raise RuntimeError(f"ImageGrab截图失败: {e}")

# ─────────────────────────────────────────
#  Book parser
# ─────────────────────────────────────────
def parse_book(path, ext):
    """Parse book file into list of {title, content} chapters."""
    chapters = []
    try:
        if ext in ('.txt', '.md'):
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            # Try to split by common chapter markers
            parts = re.split(r'(?=\n第[一二三四五六七八九十百千零\d]+[章节回部卷篇])', content)
            if len(parts) <= 2:
                # No chapters found, split by size (~2000 chars each)
                chunk = 2000
                for i, start in enumerate(range(0, len(content), chunk)):
                    seg = content[start:start + chunk]
                    chapters.append({"title": f"第 {i+1} 段", "content": seg})
            else:
                for part in parts:
                    part = part.strip()
                    if not part:
                        continue
                    lines = part.split('\n', 1)
                    title = lines[0].strip()[:60] if lines else f"第{len(chapters)+1}章"
                    body  = lines[1].strip() if len(lines) > 1 else part
                    chapters.append({"title": title, "content": body})

        elif ext == '.pdf':
            # Try pymupdf (fitz) first — handles more Chinese PDFs correctly
            parsed = False
            try:
                import fitz  # pip install pymupdf
                doc = fitz.open(path)
                for i, page in enumerate(doc):
                    text = page.get_text('text') or ''
                    text = re.sub(r'\n{3,}', '\n\n', text).strip()
                    if text:
                        chapters.append({"title": f"第 {i+1} 页", "content": text})
                doc.close()
                parsed = True
            except ImportError:
                pass
            except Exception:
                pass
            # Fallback to pdfplumber
            if not parsed:
                try:
                    import pdfplumber
                    with pdfplumber.open(path) as pdf:
                        for i, page in enumerate(pdf.pages):
                            text = page.extract_text() or ''
                            text = re.sub(r'\n{3,}', '\n\n', text).strip()
                            if text:
                                chapters.append({"title": f"第 {i+1} 页", "content": text})
                except ImportError:
                    raise RuntimeError("请安装 PDF 解析库：pip install pymupdf 或 pip install pdfplumber")

        elif ext == '.epub':
            try:
                import ebooklib
                from ebooklib import epub
                from html.parser import HTMLParser
            except ImportError:
                raise RuntimeError("请安装 ebooklib：pip install ebooklib")

            class TextExtractor(HTMLParser):
                def __init__(self): super().__init__(); self.text = []; self.in_style = False
                def handle_starttag(self, tag, attrs):
                    if tag in ('style', 'script'): self.in_style = True
                def handle_endtag(self, tag):
                    if tag in ('style', 'script'): self.in_style = False
                def handle_data(self, d):
                    if not self.in_style: self.text.append(d)

            book = epub.read_epub(path)
            items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
            for item in items:
                parser = TextExtractor()
                parser.feed(item.get_content().decode('utf-8', errors='ignore'))
                text = ''.join(parser.text).strip()
                # Strip excessive whitespace
                text = re.sub(r'\n{3,}', '\n\n', text)
                if len(text) < 30:
                    continue
                name = item.get_name().split('/')[-1]
                name = re.sub(r'\.(xhtml?|html?)$', '', name)
                chapters.append({"title": name, "content": text})
    except Exception as e:
        chapters.append({"title": "解析错误", "content": str(e)})

    return chapters if chapters else [{"title": "空文档", "content": "（未找到内容）"}]

# ─────────────────────────────────────────
#  Static routes (defined BEFORE API routes so API takes priority)
# ─────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')

@app.route('/reader')
def reader():
    return send_from_directory(FRONTEND_DIR, 'reader.html')

@app.route('/help')
def help_page():
    return send_from_directory(FRONTEND_DIR, 'help.html')

@app.route('/settings')
def settings_page():
    return send_from_directory(FRONTEND_DIR, 'settings.html')

@app.route('/avatars/<path:filename>')
def serve_avatar(filename):
    return send_from_directory(AVATAR_DIR, filename)

@app.route('/<path:filename>')
def serve_static(filename):
    full = os.path.join(FRONTEND_DIR, filename)
    if os.path.isfile(full):
        return send_from_directory(FRONTEND_DIR, filename)
    return jsonify({"error": "not found"}), 404

# ─────────────────────────────────────────
#  Settings
# ─────────────────────────────────────────
@app.route('/api/settings', methods=['GET'])
def get_settings():
    return jsonify(load_settings())

@app.route('/api/settings', methods=['POST'])
def post_settings():
    s = load_settings()
    s.update(request.json)
    save_settings_file(s)
    return jsonify({"ok": True})


# ─────────────────────────────────────────
#  Emotion
# ─────────────────────────────────────────
@app.route('/api/emotion', methods=['GET'])
def get_emotion():
    return jsonify(load_state())

@app.route('/api/emotion', methods=['POST'])
def set_emotion():
    body = request.get_json(force=True) or {}
    state = load_state()
    if 'values' in body:
        for k, v in body['values'].items():
            if k in state['values']:
                state['values'][k] = v
    if 'coefficients' in body:
        for k, v in body['coefficients'].items():
            if k in state['coefficients']:
                state['coefficients'][k] = v
    save_state(state)
    return jsonify({'ok': True})


@app.route('/api/emotion/tick', methods=['POST'])
def emotion_tick():
    """前端每分钟调用此接口，触发情感值的时间衰减计算"""
    state = apply_time_decay()
    return jsonify({'ok': True, 'current_emotion': state.get('current_emotion', {})})


@app.route('/api/emotion/prompt-template', methods=['GET'])
def get_emotion_prompt_template():
    """获取情感prompt模板（供前端自定义）"""
    # 返回当前使用的prompt模板结构
    return jsonify({
        'template': {
            'header': '【宗也当前内部状态——仅作行为参考，请勿在回复中提及任何数字】',
            'ai_task': '【AI任务：生成情感状态描述】\n请在内心根据以上数值，为自己生成当前的：\n1. 亲密阶段（如：陌生人/初识/熟人/朋友/亲密/深度依赖等，用你自己的词汇）\n2. 情感倾向（如：冷淡/警惕/好奇/温和/依恋/占有欲等，用你自己的词汇）\n3. 当前心境（用一句话概括你此刻的内心感受）',
            'instruction': '这些描述仅供你内心参考，不要在回复中明说。\n根据这些内在状态，以涟宗也的方式自然地在回复中体现出来。\n数值高的情绪应当微妙地渗透进语气、用词或沉默的选择里。\n不要解释，不要提数字，不要表演——只是成为他。'
        }
    })


@app.route('/api/emotion/prompt-template', methods=['POST'])
def set_emotion_prompt_template():
    """保存自定义的情感prompt模板（暂存到settings）"""
    body = request.get_json(force=True) or {}
    s = load_settings()
    s['emotion_prompt_template'] = body.get('template', {})
    save_settings_file(s)
    return jsonify({'ok': True})


@app.route('/api/proactive-reset', methods=['POST'])
def proactive_reset():
    """Reset the proactive message cooldown so the next loop check can fire immediately."""
    global _last_proactive_time
    _last_proactive_time = 0.0
    return jsonify({"ok": True})

@app.route('/api/proactive-status', methods=['GET'])
def proactive_status():
    """返回主动消息触发条件的实时状态，用于调试"""
    s = load_settings()
    now = time.time()
    PROACTIVE_INTERVAL = s.get('proactive_interval_minutes', 20) * 60
    IDLE_THRESHOLD     = s.get('proactive_idle_minutes', 15) * 60

    with _sse_lock:
        has_client = len(_sse_clients) > 0

    page_visible = _activity_status.get('page_visible', True)

    cooldown_remaining = max(0, PROACTIVE_INTERVAL - (now - _last_proactive_time))

    if _last_user_message_time > 0:
        idle_secs = now - _last_user_message_time
    elif _last_push_time > 0:
        idle_secs = now - _last_push_time
    else:
        idle_secs = -1

    return jsonify({
        "sse_client_connected":    has_client,
        "page_visible":            page_visible,
        "cooldown_remaining_secs": round(cooldown_remaining),
        "idle_secs":               round(idle_secs),
        "idle_threshold_secs":     IDLE_THRESHOLD,
        "idle_satisfied":          idle_secs >= IDLE_THRESHOLD if idle_secs >= 0 else False,
        "api_configured":          bool(s.get('api_base') and s.get('api_key')),
        "last_proactive_time":     datetime.datetime.fromtimestamp(_last_proactive_time).isoformat() if _last_proactive_time > 0 else None,
        "last_user_message_time":  datetime.datetime.fromtimestamp(_last_user_message_time).isoformat() if _last_user_message_time > 0 else None,
    })

# ─────────────────────────────────────────
# test
# ─────────────────────────────────────────
@app.route('/api/proactive-test', methods=['POST'])
def proactive_test():
    """立即强制触发一条主动消息（绕过所有冷却/空闲检查，仅用于测试）。"""
    global _last_proactive_time

    s = load_settings()
    api_base = s.get('api_base', '').rstrip('/')
    api_key  = s.get('api_key', '')
    model    = s.get('model', '')
    if not api_base or not api_key:
        return jsonify({"error": "请先在设置里填写 API 地址和密钥"}), 400

    with _sse_lock:
        if not _sse_clients:
            return jsonify({"error": "没有已连接的 SSE 客户端，请确认聊天页面已打开且 SSE 状态为「已连接」"}), 400

    try:
        from config import build_character_card
        user_name = s.get('user_name', '初惠夏')
        user_desc = s.get('user_desc', '')
        now       = datetime.datetime.now()
        hour      = now.hour

        if   5 <= hour <  9: time_ctx = '清晨'
        elif 9 <= hour < 12: time_ctx = '上午'
        elif 12 <= hour < 14: time_ctx = '中午'
        elif 14 <= hour < 18: time_ctx = '下午'
        elif 18 <= hour < 21: time_ctx = '傍晚'
        elif 21 <= hour < 24: time_ctx = '晚上'
        else:                  time_ctx = '深夜'

        system = build_character_card(s)
        system += f"\n\n【用户信息】\n- 用户名字：{user_name}\n"
        if user_desc:
            system += f"- 用户描述：{user_desc}\n"
        system += f"\n【涟宗也的记忆】\n{get_memory_summary(max_tokens_estimate=600)}\n"
        feel = get_feel_summary()
        if feel:
            system += f"\n【涟宗也的自省感受（仅供内心参考，不要直接提及）】\n{feel}\n"
        system += f"\n当前时间：{now.strftime('%Y-%m-%d %H:%M')}（{time_ctx}）"

        # 使用自定义 prompt 或默认 prompt（测试模式使用简化版）
        custom_prompt = s.get('proactive_prompt', '').strip()
        if custom_prompt:
            # 替换变量（测试模式没有 idle_minutes，使用 0）
            prompt_text = custom_prompt.replace('{user_name}', user_name)
            prompt_text = prompt_text.replace('{idle_minutes}', '0')
            prompt_text = prompt_text.replace('{time_context}', time_ctx)
            system += f"\n\n【当前情境】\n{prompt_text}"
        else:
            # 默认 prompt（测试模式简化版）
            system += (
                f"\n\n【当前情境】\n"
                f"请以涟宗也的角色主动打招呼——可以问对方在做什么、是不是睡着了、或者随口说一句话。"
                f"风格要符合角色设定：简短、平静、冷淡中带一点关心，不要热情，不要感叹号。"
                f"回复最后一行必须附上动作指令。"
            )

        hist = load_history()
        msgs = hist.get('messages', [])
        recent = msgs[-6:] if len(msgs) >= 6 else msgs
        api_msgs = [{'role': 'system', 'content': system}]
        for m in recent:
            if isinstance(m.get('content'), str):
                api_msgs.append({'role': m['role'], 'content': m['content']})

        # 应用 Prompt 注入（测试模式也需要注入）
        system_injected, api_msgs_injected = apply_injections(system, api_msgs[1:])
        api_msgs = [{'role': 'system', 'content': system_injected}] + api_msgs_injected

        r = req.post(
            f"{api_base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "max_tokens": 200, "temperature": 0.9,
                  "messages": api_msgs},
            timeout=300
        )
        r.raise_for_status()
        raw = r.json()['choices'][0]['message']['content'] or ''

        motion = 'idle'; expr = 'neutral'; clean = []
        for line in raw.strip().split('\n'):
            if '[MOTION:' in line or '[EXPR:' in line:
                m2 = re.search(r'\[MOTION:(\w+)\]', line)
                e2 = re.search(r'\[EXPR:(\w+)\]', line)
                if m2: motion = m2.group(1)
                if e2: expr   = e2.group(1)
            else:
                clean.append(line)
        content = '\n'.join(clean).strip()
        if not content:
            return jsonify({"error": "AI 返回了空内容"}), 500

        push_sse_event('proactive_message', {
            'content': content,
            'motion':  motion,
            'expr':    expr,
            'time':    now.strftime('%H:%M'),
            'ts':      int(now.timestamp() * 1000)
        })

        hist_data = load_history()
        hist_data.setdefault('messages', []).append({
            'role':      'assistant',
            'content':   content,
            'time':      now.strftime('%H:%M'),
            'ts':        int(now.timestamp() * 1000),
            'proactive': True
        })
        hist_data['last_seen'] = now.isoformat()
        save_history_file(hist_data)

        _last_proactive_time = time.time()
        return jsonify({"ok": True, "content": content})

    except Exception as e:
        return jsonify({"error": str(e)}), 502


# ─────────────────────────────────────────
#  Avatar
# ─────────────────────────────────────────
@app.route('/api/avatar', methods=['POST'])
def upload_avatar():
    body = request.json or {}
    kind = body.get('kind', 'user')
    data_url = body.get('data', '')
    if not data_url:
        return jsonify({"error": "no data"}), 400
    if ',' in data_url:
        header, b64data = data_url.split(',', 1)
        ext = 'png'
        if 'jpeg' in header or 'jpg' in header: ext = 'jpg'
        elif 'gif' in header: ext = 'gif'
        elif 'webp' in header: ext = 'webp'
    else:
        b64data = data_url; ext = 'png'
    filename = f"{kind}_avatar.{ext}"
    filepath = os.path.join(AVATAR_DIR, filename)
    with open(filepath, 'wb') as f:
        f.write(base64.b64decode(b64data))
    url = f"/avatars/{filename}?t={int(datetime.datetime.now().timestamp())}"
    s = load_settings()
    s[f"{kind}_avatar"] = url
    save_settings_file(s)
    return jsonify({"ok": True, "url": url})

# ─────────────────────────────────────────
#  History
# ─────────────────────────────────────────
@app.route('/api/history', methods=['GET'])
def get_history():
    data = load_history()
    away_text = ""
    if data.get("last_seen"):
        try:
            diff = datetime.datetime.now() - datetime.datetime.fromisoformat(data["last_seen"])
            secs = int(diff.total_seconds())
            if secs < 60:       away_text = f"{secs}秒"
            elif secs < 3600:   away_text = f"{secs//60}分钟"
            elif secs < 86400:
                h, m = divmod(secs // 60, 60)
                away_text = f"{h}小时{m}分钟" if m else f"{h}小时"
            else:
                d, h = divmod(secs // 3600, 24)
                away_text = f"{d}天{h}小时" if h else f"{d}天"
        except:
            pass
    data["away_text"] = away_text
    return jsonify(data)

@app.route('/api/history', methods=['POST'])
def post_history():
    global _last_user_message_time
    body = request.json; data = load_history()

    # 检测是否有新的用户消息（对比消息数组，找出新增的消息）
    old_messages = data.get('messages', [])
    new_messages = body.get('messages', [])

    # 找出新增的消息（从旧消息长度开始的所有消息）
    if len(new_messages) > len(old_messages):
        added_messages = new_messages[len(old_messages):]
        # 检查新增消息中是否有用户消息
        has_new_user_msg = any(msg.get('role') == 'user' for msg in added_messages)
        if has_new_user_msg:
            _last_user_message_time = time.time()
            logger.info(f"[post_history] 检测到新用户消息，更新空闲计时器")

    for k in ("messages", "last_seen", "summary", "hidden_count"):
        if k in body:
            data[k] = body[k]
    save_history_file(data)

    # 触发性格/关系分析检查（非阻塞）
    threading.Thread(target=maybe_trigger_analysis, args=(data,), daemon=True).start()

    return jsonify({"ok": True})

@app.route('/api/reader-history', methods=['POST'])
def push_reader_history():
    """Reader page pushes its messages here to sync with main chat history."""
    body = request.json or {}
    reader_messages = body.get('messages', [])
    if not reader_messages:
        return jsonify({"ok": True})
    data = load_history()
    existing = data.get('messages', [])
    # Append reader messages that don't already exist (by content+time)
    existing_keys = {(m.get('content',''), m.get('time','')) for m in existing}
    for m in reader_messages:
        k = (m.get('content',''), m.get('time',''))
        if k not in existing_keys:
            existing.append(m)
            existing_keys.add(k)
    data['messages'] = existing
    data['last_seen'] = datetime.datetime.now().isoformat()
    save_history_file(data)
    return jsonify({"ok": True})


def clear_history():
    save_history_file({"messages": [], "last_seen": None, "summary": "", "hidden_count": 0})
    return jsonify({"ok": True})

# ─────────────────────────────────────────
#  Chat
# ─────────────────────────────────────────
@app.route('/api/chat', methods=['POST'])
def chat():
    body = request.json or {}
    messages = body.get('messages', [])
    s = load_settings()
    api_base = s.get('api_base', '').rstrip('/')
    api_key  = s.get('api_key', '')
    model    = s.get('model', 'claude-sonnet-4-20250514')
    if not api_base or not api_key:
        return jsonify({"error": "请先在设置里填写 API 地址和密钥"}), 400

    from config import build_character_card
    from tools import TOOLS, execute_tool
    user_name = s.get('user_name', '初惠夏')
    user_desc = s.get('user_desc', '')

    # 根据配置选择情感系统
    emotion_system = s.get('emotion_system', 'occ')
    if emotion_system == 'occ':
        from emotion_occ import on_message_received, build_prompt_block
        emotion_state = on_message_received()
    else:
        from emotion import on_message_received, build_prompt_block
        emotion_state = on_message_received()

    system = build_character_card(s)
    system += f"\n\n【用户信息】\n- 用户名字：{user_name}\n"
    if user_desc:
        system += f"- 用户描述：{user_desc}\n"

    # 根据配置选择记忆引擎
    memory_engine = s.get('memory_engine', 'simple')
    if memory_engine == 'ombre':
        try:
            from memory_ombre import get_memory_summary_ombre, get_ombre_status
            # 使用 Ombre 的浮现模式获取记忆
            system += f"\n【涟宗也的记忆】\n{get_memory_summary_ombre(max_tokens_estimate=600)}\n"
            # 获取 feel 类型记忆
            feel_summary = get_memory_summary_ombre(domain='feel')
            if feel_summary and '涟宗也的自省感受' in feel_summary:
                system += f"\n{feel_summary}\n"
        except Exception as e:
            # Fallback to simple mode
            system += f"\n【涟宗也的记忆】\n{get_memory_summary(max_tokens_estimate=600)}\n"
            feel = get_feel_summary()
            if feel:
                system += f"\n【涟宗也的自省感受（仅供内心参考，不要直接提及）】\n{feel}\n"
    else:
        system += f"\n【涟宗也的记忆】\n{get_memory_summary(max_tokens_estimate=600)}\n"
        feel = get_feel_summary()
        if feel:
            system += f"\n【涟宗也的自省感受（仅供内心参考，不要直接提及）】\n{feel}\n"

    # 注入性格与关系总结
    hist_data = load_history()
    p_sum = hist_data.get('personality_summary', '')
    r_sum = hist_data.get('relationship_summary', '')
    if p_sum:
        system += f"\n\n【对用户的性格洞察】\n{p_sum}"
    if r_sum:
        system += f"\n\n【与用户的关系记录】\n{r_sum}"

    # 添加详细的当前时间信息
    now = datetime.datetime.now()
    weekday_names = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
    weekday = weekday_names[now.weekday()]
    hour = now.hour
    if   5 <= hour <  9: time_ctx = '清晨'
    elif 9 <= hour < 12: time_ctx = '上午'
    elif 12 <= hour < 14: time_ctx = '中午'
    elif 14 <= hour < 18: time_ctx = '下午'
    elif 18 <= hour < 21: time_ctx = '傍晚'
    elif 21 <= hour < 24: time_ctx = '晚上'
    else:                  time_ctx = '深夜'
    system += f"\n当前时间：{now.strftime('%Y年%m月%d日')} {weekday} {now.strftime('%H:%M')}（{time_ctx}）"

    # 注入情感状态
    system += build_prompt_block(emotion_state)

    # 根据情感系统类型添加不同的使用指南
    if emotion_system == 'occ':
        system += (
            "\n\n【记忆系统使用指南】\n"
            "你拥有长期记忆能力，必须主动、频繁地使用 save_memory 工具。\n"
            "每次对话中，当用户分享个人信息、表达偏好、提到重要事件、情绪变化时，立即保存记忆。\n"
            "不要等到对话结束才保存，而是在获得信息的当下就记录。\n"
            "记忆会帮助你在未来的对话中更好地理解用户，建立更深的关系。\n"
            "\n【OCC 认知评价系统使用指南】\n"
            "每次对话后，你需要：\n"
            "1. 使用 appraise_event 工具对事件进行认知评价（新奇感、安全感、威胁感等）\n"
            "2. 使用 add_inner_thought 工具记录你的内心想法（用户看不到）\n"
            "3. 使用 update_long_term_emotion 工具更新长期情感（亲密度、信任度、依赖度）\n"
            "你的性格特质会影响认知评价，评价结果会自动转换为情绪状态。\n"
            "根据当前情绪状态调整回复方式：高唤醒时可以连发消息，低唤醒时保持简短。\n"
            "\n【消息格式说明】\n"
            "你可以在回复中使用换行（\\n）来将内容拆成多条独立消息气泡发送，"
            "每行将显示为一条单独的聊天气泡。"
            "适合用来分步骤回复、先说一句再补充、或模拟连续发消息的自然感。"
            "不需要刻意分行，正常回复即可；但如果情景适合多条发出，可以自然地换行。"
        )
    else:
        system += (
            "\n\n【记忆系统使用指南】\n"
            "你拥有长期记忆能力，必须主动、频繁地使用 save_memory 工具。\n"
            "每次对话中，当用户分享个人信息、表达偏好、提到重要事件、情绪变化时，立即保存记忆。\n"
            "不要等到对话结束才保存，而是在获得信息的当下就记录。\n"
            "记忆会帮助你在未来的对话中更好地理解用户，建立更深的关系。\n"
            "\n【情感系统使用指南】\n"
            "每次对话结束前，必须调用 update_emotion 工具更新你的情感状态。\n"
            "根据对话内容的情感色彩，调整 loneliness（孤独感）、intimacy（亲密感）、excitement（兴奋）、"
            "irritation（烦躁）、curiosity（好奇）、melancholy（忧郁）、affection（亲密度）等维度。\n"
            "温暖的对话应减少孤独感、增加亲密感；被关心时增加亲密度；有趣的话题增加兴奋和好奇。\n"
            "\n【消息格式说明】\n"
            "你可以在回复中使用换行（\\n）来将内容拆成多条独立消息气泡发送，"
            "每行将显示为一条单独的聊天气泡。"
            "适合用来分步骤回复、先说一句再补充、或模拟连续发消息的自然感。"
            "不需要刻意分行，正常回复即可；但如果情景适合多条发出，可以自然地换行。"
        )

    # Away notice
    away = body.get('away_notice', '')
    if away:
        away_hint = "\n\n【提示】用户刚刚回来，已离开了 " + away + "。请在回复中自然地提及（冷淡风格，可以只说一句'这么久'之类的）。"
        system += away_hint

    # Activity context — use request body first, fall back to last known status
    activity = body.get('activity_context', '')
    if not activity:
        # Build from server-stored status (populated by frontend polling)
        st = _activity_status
        parts = []
        if st.get('active_window'): parts.append('当前活动窗口：' + st['active_window'])
        if not st.get('page_visible', True): parts.append('用户已切离聊天页面')
        if st.get('mouse_idle'): parts.append('鼠标已停止活动超过5分钟')
        if st.get('is_typing'): parts.append('用户正在输入消息')
        if st.get('session_clicks', 0) > 0: parts.append(f"本次会话共点击 {st['session_clicks']} 次")
        activity = '\n'.join(parts)
    if activity:
        system += f"\n\n【用户电脑活动状态】\n{activity}\n（当用户问起自己在做什么、打开了什么软件、刚才在哪个页面等问题时，必须根据以上信息如实回答。）"

    # Also inject recent activity log entries (last 5)
    try:
        recent_log = load_activity_log()[-5:]
        if recent_log:
            log_lines = []
            for entry in recent_log:
                t = entry.get('timestamp', '')
                d = entry.get('data', {})
                win = d.get('active_window', '')
                ev = entry.get('type', '')
                if win:
                    log_lines.append(f"[{t}] {ev} | 窗口：{win}")
            if log_lines:
                system += "\n\n【最近活动记录（时间顺序）】\n" + "\n".join(log_lines)
    except Exception:
        pass

    # Book context (if reading together)
    book_ctx = body.get('book_context', '')
    if book_ctx:
        system += f"\n\n【正在与用户共同阅读】\n{book_ctx}"

    # Quote context (user quoting a previous message)
    quoted_text = body.get('quoted_text', '')
    quoted_role = body.get('quoted_role', 'assistant')
    if quoted_text:
        role_label = '涟宗也' if quoted_role == 'assistant' else user_name
        # Truncate to 150 chars to avoid token bloat
        quote_preview = quoted_text[:150] + ('...' if len(quoted_text) > 150 else '')
        quote_hint = f"[用户引用了{role_label}的消息：「{quote_preview}」并回复]"
        # Inject into the last user message
        if messages and messages[-1]['role'] == 'user':
            messages[-1]['content'] = f"{quote_hint}\n{messages[-1]['content']}"

    # 处理消息历史，添加日期分隔符让 AI 感知时间变化
    processed_messages = []
    last_date = None
    for msg in messages:
        # 如果消息有时间戳，检查日期是否变化
        if 'ts' in msg and msg['ts']:
            try:
                msg_time = datetime.datetime.fromtimestamp(msg['ts'] / 1000)
                msg_date = msg_time.strftime('%Y年%m月%d日')
                weekday_names = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
                weekday = weekday_names[msg_time.weekday()]

                # 如果日期变化，插入日期分隔符
                if last_date != msg_date:
                    processed_messages.append({
                        'role': 'system',
                        'content': f"【{msg_date} {weekday}】"
                    })
                    last_date = msg_date
            except Exception:
                pass

        # 添加原始消息
        processed_messages.append(msg)

    # 应用 Prompt 注入
    system, processed_messages = apply_injections(system, processed_messages)

    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}] + processed_messages,
        "tools": TOOLS, "tool_choice": "auto",
        "max_tokens": 800, "temperature": 0.88
    }
    try:
        r = req.post(f"{api_base}/chat/completions",
                     headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                     json=payload, timeout=300)
        r.raise_for_status()
        result = r.json()
    except req.exceptions.HTTPError:
        return jsonify({"error": f"API错误 {r.status_code}: {r.text[:300]}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    msg    = result['choices'][0]['message']
    finish = result['choices'][0]['finish_reason']

    for _ in range(5):
        if finish != 'tool_calls' or not msg.get('tool_calls'):
            break
        call_msgs = [{"role": "system", "content": system}] + messages + [msg]
        for tc in msg['tool_calls']:
            res = execute_tool(tc['function']['name'], json.loads(tc['function']['arguments']))
            call_msgs.append({"role": "tool", "tool_call_id": tc['id'], "content": str(res)})
        try:
            r2 = req.post(f"{api_base}/chat/completions",
                          headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                          json={**payload, "messages": call_msgs}, timeout=300)
            r2.raise_for_status()
            res2 = r2.json()
            msg    = res2['choices'][0]['message']
            finish = res2['choices'][0]['finish_reason']
        except:
            break

    content = msg.get('content', '') or ''
    motion = 'idle'; expr = 'neutral'; clean = []
    for line in content.strip().split('\n'):
        if '[MOTION:' in line or '[EXPR:' in line:
            m = re.search(r'\[MOTION:(\w+)\]', line)
            e = re.search(r'\[EXPR:(\w+)\]', line)
            if m: motion = m.group(1)
            if e: expr   = e.group(1)
        else:
            clean.append(line)
    return jsonify({"content": '\n'.join(clean).strip(), "motion": motion, "expr": expr})

# ─────────────────────────────────────────
#  Screenshot
# ─────────────────────────────────────────
@app.route('/api/screenshot', methods=['POST'])
def screenshot_understand():
    body = request.json or {}
    question = body.get('question', '请描述屏幕上显示的内容')
    s = load_settings()
    api_base = s.get('api_base', '').rstrip('/')
    api_key  = s.get('api_key', '')
    model    = s.get('model', '')
    if not api_base or not api_key:
        return jsonify({"error": "请先配置API"}), 400
    try:
        import time; time.sleep(0.3)
        b64 = take_screenshot_b64()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    try:
        from config import build_character_card
        user_name = s.get('user_name', '初惠夏')
        user_desc = s.get('user_desc', '')
        system = build_character_card(s)
        system += f"\n\n【用户信息】\n- 用户名字：{user_name}\n"
        if user_desc:
            system += f"- 用户描述：{user_desc}\n"
        system += f"\n【涟宗也的记忆】\n{get_memory_summary(max_tokens_estimate=600)}\n"
        feel = get_feel_summary()
        if feel:
            system += f"\n【涟宗也的自省感受（仅供内心参考，不要直接提及）】\n{feel}\n"
        system += f"\n当前时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        system += "\n\n用户分享了一张屏幕截图给你看。请用涟宗也的角色风格回答用户的问题，语气保持角色设定，简洁冷淡，回复最后一行附上动作指令。"
        r = req.post(f"{api_base}/chat/completions",
                     headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                     json={"model": model, "max_tokens": 800, "messages": [
                         {"role": "system", "content": system},
                         {"role": "user", "content": [
                             {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                             {"type": "text", "text": question}
                         ]}
                     ]}, timeout=300)
        r.raise_for_status()
        raw = r.json()['choices'][0]['message']['content'] or ''
        # Parse motion/expr directives (same as /api/chat)
        motion = 'idle'; expr = 'neutral'; clean = []
        for line in raw.strip().split('\n'):
            if '[MOTION:' in line or '[EXPR:' in line:
                m2 = re.search(r'\[MOTION:(\w+)\]', line)
                e2 = re.search(r'\[EXPR:(\w+)\]', line)
                if m2: motion = m2.group(1)
                if e2: expr   = e2.group(1)
            else:
                clean.append(line)
        answer = '\n'.join(clean).strip()
        return jsonify({"answer": answer, "screenshot": b64, "motion": motion, "expr": expr})
    except Exception as e:
        return jsonify({"error": f"视觉API失败:{e}"}), 502

# ─────────────────────────────────────────
#  Active Window (Windows only)
# ─────────────────────────────────────────
@app.route('/api/active-window', methods=['GET'])
def get_active_window():
    """Return the title and process name of the currently focused window."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()

        # Get window title
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value

        # Get process name
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            import psutil
            proc = psutil.Process(pid.value)
            process = proc.name()
        except Exception:
            process = 'unknown'

        return jsonify({"title": title, "process": process, "ok": True})
    except Exception as e:
        return jsonify({"title": "", "process": "", "ok": False, "error": str(e)})

# ─────────────────────────────────────────
#  Book upload & navigation
# ─────────────────────────────────────────
@app.route('/api/upload-book', methods=['POST'])
def upload_book():
    global _book_data
    body = request.json or {}
    filename = body.get('filename', 'book.txt')
    b64data  = body.get('data', '')
    if not b64data:
        return jsonify({"error": "no data"}), 400

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ('.txt', '.pdf', '.epub', '.md'):
        return jsonify({"error": f"不支持的格式 {ext}，请上传 .txt / .pdf / .epub"}), 400

    tmp_path = os.path.join(os.path.dirname(__file__), f'_tmp_book{ext}')
    try:
        with open(tmp_path, 'wb') as f:
            f.write(base64.b64decode(b64data))
        chapters = parse_book(tmp_path, ext)
        book_title = os.path.splitext(filename)[0]
        _book_data = {
            "title": book_title,
            "filename": filename,
            "chapters": chapters,
            "total": len(chapters)
        }
        _save_book_cache(_book_data)
        # Also save to library for bookshelf
        _book_library[book_title] = _book_data
        _save_book_library(_book_library)
        return jsonify({"ok": True, "title": book_title, "total": len(chapters)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/book', methods=['GET'])
def get_book_meta():
    if not _book_data:
        return jsonify({"error": "no book loaded"}), 404
    return jsonify({
        "title": _book_data.get("title"),
        "filename": _book_data.get("filename"),
        "total": _book_data.get("total"),
        "chapters": [{"title": c["title"], "idx": i} for i, c in enumerate(_book_data.get("chapters", []))]
    })

@app.route('/api/book/chapter/<int:idx>', methods=['GET'])
def get_chapter(idx):
    if not _book_data:
        return jsonify({"error": "no book loaded"}), 404
    chapters = _book_data.get("chapters", [])
    if idx < 0 or idx >= len(chapters):
        return jsonify({"error": "chapter not found"}), 404
    ch = chapters[idx]
    return jsonify({
        "title": ch["title"],
        "content": ch["content"],
        "idx": idx,
        "total": len(chapters),
        "prev_title": chapters[idx-1]["title"] if idx > 0 else None,
        "next_title": chapters[idx+1]["title"] if idx < len(chapters)-1 else None
    })

# ─────────────────────────────────────────
#  Book Library (multi-book shelf)
# ─────────────────────────────────────────
BOOK_LIBRARY_FILE = 'book_library.json'

def _load_book_library():
    if os.path.exists(BOOK_LIBRARY_FILE):
        try:
            with open(BOOK_LIBRARY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_book_library(lib):
    try:
        with open(BOOK_LIBRARY_FILE, 'w', encoding='utf-8') as f:
            json.dump(lib, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

_book_library = _load_book_library()

@app.route('/api/bookshelf', methods=['GET'])
def get_bookshelf():
    """Return list of all books in library (no chapter content, just meta)."""
    items = []
    for title, bdata in _book_library.items():
        items.append({
            "title": bdata.get("title", title),
            "filename": bdata.get("filename", ""),
            "total": bdata.get("total", 0)
        })
    return jsonify({"books": items})

@app.route('/api/bookshelf/load', methods=['POST'])
def load_from_shelf():
    """Switch current book to one from library."""
    global _book_data
    body = request.json or {}
    title = body.get('title', '')
    if title not in _book_library:
        return jsonify({"error": "book not found"}), 404
    _book_data = _book_library[title]
    _save_book_cache(_book_data)
    return jsonify({"ok": True, "title": title, "total": _book_data.get("total", 0)})

@app.route('/api/bookshelf/<path:title>', methods=['DELETE'])
def delete_from_shelf(title):
    global _book_library
    if title in _book_library:
        del _book_library[title]
        _save_book_library(_book_library)
    return jsonify({"ok": True})

@app.route('/api/bookshelf/<path:title>/rename', methods=['POST'])
def rename_in_shelf(title):
    global _book_library
    body = request.json or {}
    new_title = body.get('new_title', '').strip()
    if not new_title:
        return jsonify({"error": "新标题不能为空"}), 400
    if title not in _book_library:
        return jsonify({"error": "书籍不存在"}), 404
    entry = _book_library.pop(title)
    entry['title'] = new_title
    _book_library[new_title] = entry
    _save_book_library(_book_library)
    return jsonify({"ok": True})

@app.route('/api/book/close', methods=['POST'])
def close_book():
    """Close the current book (clear active book)."""
    global _book_data
    _book_data = {}
    _save_book_cache({})
    return jsonify({"ok": True})

# ─────────────────────────────────────────
#  Summarize
# ─────────────────────────────────────────
@app.route('/api/summarize', methods=['POST'])
def summarize():
    body = request.json or {}
    messages = body.get('messages', [])
    s = load_settings()
    api_base = s.get('api_base', '').rstrip('/')
    api_key  = s.get('api_key', '')
    model    = s.get('model', '')
    prompt   = body.get('prompt') or s.get('summary_prompt', '请总结以上对话')
    if not api_base or not api_key:
        return jsonify({"error": "请先配置API"}), 400
    conv = '\n'.join([f"{'用户' if m['role']=='user' else '涟宗也'}：{m['content']}"
                      for m in messages if isinstance(m.get('content'), str)])
    try:
        r = req.post(f"{api_base}/chat/completions",
                     headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                     json={"model": model, "max_tokens": 500,
                           "messages": [{"role": "user", "content": f"{conv}\n\n---\n{prompt}"}]},
                     timeout=300)
        r.raise_for_status()
        return jsonify({"summary": r.json()['choices'][0]['message']['content']})
    except Exception as e:
        return jsonify({"error": str(e)}), 502

# ─────────────────────────────────────────
#  Memory
# ─────────────────────────────────────────
@app.route('/api/memory', methods=['GET'])
def get_memory():
    return jsonify(mem_load_all())

@app.route('/api/memory', methods=['POST'])
def add_memory():
    d = request.json
    mem_save(d['key'], d['value'])
    return jsonify({"ok": True})

@app.route('/api/memory/<key>', methods=['DELETE'])
def del_memory(key):
    mem_delete(key)
    return jsonify({"ok": True})

@app.route('/api/memory/export', methods=['GET'])
def export_memory():
    return jsonify(mem_load_all()), 200, {
        'Content-Disposition': 'attachment; filename=memory_export.json',
        'Content-Type': 'application/json; charset=utf-8'
    }

@app.route('/api/memory/<key>/touch', methods=['POST'])
def touch_mem(key):
    """AI 回复中引用某条记忆时，由前端调用，增加激活计数。"""
    from memory import touch_memory
    touch_memory(key)
    return jsonify({"ok": True})

@app.route('/api/memory/feels', methods=['GET'])
def get_feels():
    """返回所有 feel 类型记忆。"""
    data = mem_load_all()
    feels = {k: v for k, v in data.items() if v.get("type") == "feel"}
    return jsonify(feels)

@app.route('/api/memory/archived', methods=['GET'])
def get_archived():
    """返回已遗忘（archived）的记忆，供查阅。"""
    data = mem_load_all()
    archived = {k: v for k, v in data.items() if v.get("type") == "archived"}
    return jsonify(archived)

@app.route('/api/memory/<key>/restore', methods=['POST'])
def restore_memory(key):
    """将 archived 记忆恢复为 dynamic。"""
    from memory import _write
    data = mem_load_all()
    if key in data and data[key].get("type") == "archived":
        data[key]["type"] = "dynamic"
        data[key]["resolved"] = False
        data[key]["last_active"] = datetime.datetime.now().isoformat()
        _write(data)
    return jsonify({"ok": True})

# ─────────────────────────────────────────
#  Prompt Injections API
# ─────────────────────────────────────────
@app.route('/api/injections', methods=['GET'])
def get_injections():
    """获取所有 prompt 注入条目"""
    return jsonify(load_injections())

@app.route('/api/injections', methods=['POST'])
def create_injection():
    """创建新的 prompt 注入条目"""
    body = request.json or {}
    items = load_injections()
    new_item = {
        "id": f"inj_{int(time.time()*1000)}",
        "name": body.get("name", "未命名条目"),
        "content": body.get("content", ""),
        "position": body.get("position", "system_bottom"),
        "enabled": body.get("enabled", True),
        "priority": body.get("priority", 0)
    }
    items.append(new_item)
    save_injections(items)
    return jsonify({"ok": True, "id": new_item["id"]})

@app.route('/api/injections/<inj_id>', methods=['PUT'])
def update_injection(inj_id):
    """更新指定的 prompt 注入条目"""
    body = request.json or {}
    items = load_injections()
    for item in items:
        if item["id"] == inj_id:
            item.update({k: v for k, v in body.items() if k != "id"})
            break
    save_injections(items)
    return jsonify({"ok": True})

@app.route('/api/injections/<inj_id>', methods=['DELETE'])
def delete_injection(inj_id):
    """删除指定的 prompt 注入条目"""
    items = [i for i in load_injections() if i["id"] != inj_id]
    save_injections(items)
    return jsonify({"ok": True})

# ─────────────────────────────────────────
#  Analysis Status API
# ─────────────────────────────────────────
@app.route('/api/analysis-status', methods=['GET'])
def get_analysis_status():
    """返回当前累计字符数和上次分析摘要"""
    hist_data = load_history()
    s = load_settings()
    return jsonify({
        "chars": hist_data.get('chars_since_last_analysis', 0),
        "threshold": s.get('analysis_threshold', 8000),
        "personality_summary": hist_data.get('personality_summary', ''),
        "relationship_summary": hist_data.get('relationship_summary', ''),
        "last_analysis_time": hist_data.get('last_analysis_time', None)
    })

READER_STATE_FILE = 'reader_state.json'
ACTIVITY_LOG_FILE = 'activity_log.json'

def load_reader_state():
    if os.path.exists(READER_STATE_FILE):
        with open(READER_STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_reader_state_file(data):
    with open(READER_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_activity_log():
    if os.path.exists(ACTIVITY_LOG_FILE):
        with open(ACTIVITY_LOG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_activity_log_file(data):
    with open(ACTIVITY_LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ─────────────────────────────────────────
#  Reader State
# ─────────────────────────────────────────
@app.route('/api/reader-state', methods=['GET'])
def get_reader_state():
    return jsonify(load_reader_state())

@app.route('/api/reader-state', methods=['POST'])
def post_reader_state():
    body = request.json or {}
    state = load_reader_state()
    state.update(body)
    save_reader_state_file(state)
    return jsonify({"ok": True})

# ─────────────────────────────────────────
#  Activity Log (persistent, AI-manageable)
# ─────────────────────────────────────────
@app.route('/api/activity-log', methods=['GET'])
def get_activity_log():
    return jsonify(load_activity_log())

@app.route('/api/activity-log', methods=['POST'])
def post_activity_log():
    body = request.json or {}
    log = load_activity_log()
    entry = {
        "time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "type": body.get('type', 'general'),
        "data": body.get('data', {})
    }
    log.append(entry)
    # Keep last 500 entries to avoid bloat
    if len(log) > 500:
        log = log[-500:]
    save_activity_log_file(log)
    return jsonify({"ok": True})

@app.route('/api/activity-log/clear', methods=['POST'])
def clear_activity_log():
    save_activity_log_file([])
    return jsonify({"ok": True})

@app.route('/api/activity-log/<int:idx>', methods=['DELETE'])
def delete_activity_log_entry(idx):
    log = load_activity_log()
    if 0 <= idx < len(log):
        log.pop(idx)
        save_activity_log_file(log)
    return jsonify({"ok": True})

@app.route('/api/activity-log/ai-cleanup', methods=['POST'])
def ai_cleanup_activity_log():
    """Ask AI to review and clean up the activity log."""
    s = load_settings()
    api_base = s.get('api_base', '').rstrip('/')
    api_key  = s.get('api_key', '')
    model    = s.get('model', '')
    if not api_base or not api_key:
        return jsonify({"error": "请先配置API"}), 400
    log = load_activity_log()
    if not log:
        return jsonify({"kept": 0, "removed": 0})
    log_text = json.dumps(log, ensure_ascii=False)
    try:
        r = req.post(f"{api_base}/chat/completions",
                     headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                     json={"model": model, "max_tokens": 800,
                           "messages": [{"role": "user", "content": f"以下是用户活动日志（JSON数组），请删除重复的、不重要的条目，只保留有意义的信息。返回清理后的JSON数组，不要任何解释。\n{log_text[:3000]}"}]},
                     timeout=300)
        r.raise_for_status()
        content = r.json()['choices'][0]['message']['content']
        cleaned = json.loads(content.strip().strip('`').replace('json','',1).strip())
        removed = len(log) - len(cleaned)
        save_activity_log_file(cleaned)
        return jsonify({"kept": len(cleaned), "removed": removed})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route('/api/activity-status', methods=['GET'])
def get_activity_status():
    status = dict(_activity_status)
    # 超过 15 秒没有收到前端推送 → 自动判定为离线（兜底 beforeunload 发不出去的情况）
    if _last_push_time > 0 and (time.time() - _last_push_time) > 15:
        status['page_visible'] = False
        status['timed_out'] = True
    else:
        status['timed_out'] = False
    status['last_update'] = datetime.datetime.now().strftime('%H:%M:%S')
    return jsonify(status)

@app.route('/api/activity-status', methods=['POST'])
def post_activity_status():
    global _activity_status, _last_push_time
    body = request.json or {}
    _activity_status.update({
        'page_visible':   body.get('page_visible', True),
        'mouse_idle':     body.get('mouse_idle', False),
        'active_window':  body.get('active_window', ''),
        'is_typing':      body.get('is_typing', False),
        'session_clicks': body.get('session_clicks', 0),
        'last_update':    datetime.datetime.now().strftime('%H:%M:%S')
    })
    _last_push_time = time.time()   # 记录推送时间，供超时检测使用
    return jsonify({'ok': True})

# ─────────────────────────────────────────
#  SSE — /api/stream-events
# ─────────────────────────────────────────
@app.route('/api/stream-events')
def stream_events():
    """
    Long-lived SSE endpoint.  The browser opens EventSource('/api/stream-events')
    and receives push events (e.g. proactive_message) without polling.
    """
    client_queue: queue.Queue = queue.Queue()
    with _sse_lock:
        _sse_clients.append(client_queue)

    @stream_with_context
    def generate():
        # Tell the client the connection is alive
        yield "event: connected\ndata: {\"ok\": true}\n\n"
        try:
            while True:
                try:
                    payload = client_queue.get(timeout=25)
                    yield payload
                except queue.Empty:
                    # Heartbeat keeps nginx / proxies from killing the connection
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            # 保证无论何种方式退出都能清理
            with _sse_lock:
                try:
                    _sse_clients.remove(client_queue)
                except ValueError:
                    pass

    resp = Response(generate(), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'  # disable nginx buffering
    return resp


# ─────────────────────────────────────────
#  SSE — Proactive message background thread
# ─────────────────────────────────────────
def _proactive_message_loop():
    """
    Background daemon thread.
    Every CHECK_INTERVAL seconds it decides whether to send a proactive message.
    Conditions (all must be true):
      1. At least one SSE client is connected (someone has the page open).
      2. The page is still marked visible by the frontend.
      3. At least PROACTIVE_INTERVAL has passed since the last proactive message.
      4. The user has been idle (no new chat messages) for at least IDLE_THRESHOLD seconds.
      5. API is configured.
    """
    global _last_proactive_time

    CHECK_INTERVAL = 60   # seconds between each check (fixed)

    time.sleep(35)   # let the server finish starting up first

    while True:
        time.sleep(CHECK_INTERVAL)
        try:
            # Dynamically reload settings every iteration so UI changes take effect immediately
            s = load_settings()
            PROACTIVE_INTERVAL = s.get('proactive_interval_minutes', 20) * 60
            IDLE_THRESHOLD     = s.get('proactive_idle_minutes', 15) * 60
            require_visible    = s.get('proactive_require_page_visible', True)

            # ① SSE client connected?
            with _sse_lock:
                has_clients = len(_sse_clients) > 0
                if not has_clients:
                    logger.debug("[proactive_loop] 跳过：无 SSE 客户端连接")
                    continue

            # ② Page visible? (only enforced when setting is True)
            if require_visible and not _activity_status.get('page_visible', True):
                logger.debug("[proactive_loop] 跳过：页面不可见")
                continue

            # ③ Cooldown
            cooldown_remaining = PROACTIVE_INTERVAL - (time.time() - _last_proactive_time)
            if cooldown_remaining > 0:
                logger.debug(f"[proactive_loop] 跳过：冷却中，剩余 {cooldown_remaining:.0f} 秒")
                continue

            # ④ How long since the user last sent a message?
            if _last_user_message_time > 0:
                idle_secs = time.time() - _last_user_message_time
            elif _last_push_time > 0:
                idle_secs = time.time() - _last_push_time
            else:
                logger.debug("[proactive_loop] 跳过：无用户活动记录")
                continue
            if idle_secs < IDLE_THRESHOLD:
                logger.debug(f"[proactive_loop] 跳过：用户空闲 {idle_secs:.0f}s < 阈值 {IDLE_THRESHOLD}s")
                continue

            # ⑤ API configured?
            api_base = s.get('api_base', '').rstrip('/')
            api_key  = s.get('api_key', '')
            model    = s.get('model', '')
            if not api_base or not api_key:
                logger.debug("[proactive_loop] 跳过：API 未配置")
                continue

            logger.info(f"[proactive_loop] 触发条件满足，准备发送主动消息（空闲 {idle_secs:.0f}s）")

            # ─── Build the proactive prompt ───
            from config import build_character_card
            user_name = s.get('user_name', '初惠夏')
            user_desc = s.get('user_desc', '')
            now       = datetime.datetime.now()
            hour      = now.hour
            idle_min  = int(idle_secs // 60)

            if   5 <= hour <  9: time_ctx = '清晨'
            elif 9 <= hour < 12: time_ctx = '上午'
            elif 12 <= hour < 14: time_ctx = '中午'
            elif 14 <= hour < 18: time_ctx = '下午'
            elif 18 <= hour < 21: time_ctx = '傍晚'
            elif 21 <= hour < 24: time_ctx = '晚上'
            else:                  time_ctx = '深夜'

            system = build_character_card(s)
            system += f"\n\n【用户信息】\n- 用户名字：{user_name}\n"
            if user_desc:
                system += f"- 用户描述：{user_desc}\n"
            system += f"\n【涟宗也的记忆】\n{get_memory_summary(max_tokens_estimate=600)}\n"
            feel = get_feel_summary()
            if feel:
                system += f"\n【涟宗也的自省感受（仅供内心参考，不要直接提及）】\n{feel}\n"
            system += f"\n当前时间：{now.strftime('%Y-%m-%d %H:%M')}（{time_ctx}）"

            # 使用自定义 prompt 或默认 prompt
            custom_prompt = s.get('proactive_prompt', '').strip()
            if custom_prompt:
                # 替换变量
                prompt_text = custom_prompt.replace('{user_name}', user_name)
                prompt_text = prompt_text.replace('{idle_minutes}', str(idle_min))
                prompt_text = prompt_text.replace('{time_context}', time_ctx)
                system += f"\n\n【当前情境】\n{prompt_text}"
            else:
                # 默认 prompt
                system += (
                    f"\n\n【当前情境】\n"
                    f"{user_name} 已经 {idle_min} 分钟没有发消息了，但仍然停留在聊天页面上。"
                    f"请以涟宗也的角色主动打招呼——可以问对方在做什么、是不是睡着了、或者随口说一句话。"
                    f"风格要符合角色设定：简短、平静、冷淡中带一点关心，不要热情，不要感叹号。"
                    f"回复最后一行必须附上动作指令。"
                )

            # Include last few turns as context
            hist = load_history()
            msgs = hist.get('messages', [])
            recent = msgs[-6:] if len(msgs) >= 6 else msgs
            api_msgs = [{'role': 'system', 'content': system}]
            for m in recent:
                if isinstance(m.get('content'), str):
                    api_msgs.append({'role': m['role'], 'content': m['content']})

            # 应用 Prompt 注入（主动消息也需要注入）
            system_injected, api_msgs_injected = apply_injections(system, api_msgs[1:])
            api_msgs = [{'role': 'system', 'content': system_injected}] + api_msgs_injected

            r = req.post(
                f"{api_base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "max_tokens": 200, "temperature": 0.9,
                      "messages": api_msgs},
                timeout=300
            )
            r.raise_for_status()
            raw = r.json()['choices'][0]['message']['content'] or ''

            # Parse MOTION / EXPR directives
            motion = 'idle'; expr = 'neutral'; clean = []
            for line in raw.strip().split('\n'):
                if '[MOTION:' in line or '[EXPR:' in line:
                    m2 = re.search(r'\[MOTION:(\w+)\]', line)
                    e2 = re.search(r'\[EXPR:(\w+)\]', line)
                    if m2: motion = m2.group(1)
                    if e2: expr   = e2.group(1)
                else:
                    clean.append(line)
            content = '\n'.join(clean).strip()
            if not content:
                logger.warning("[proactive_loop] AI 返回空内容，跳过")
                continue

            logger.info(f"[proactive_loop] 成功生成主动消息：{content[:50]}...")

            # ─── Push SSE event ───
            push_sse_event('proactive_message', {
                'content': content,
                'motion':  motion,
                'expr':    expr,
                'time':    now.strftime('%H:%M'),
                'ts':      int(now.timestamp() * 1000)
            })

            # ─── Persist to history so AI remembers it sent this ───
            hist_data = load_history()
            hist_data.setdefault('messages', []).append({
                'role':      'assistant',
                'content':   content,
                'time':      now.strftime('%H:%M'),
                'ts':        int(now.timestamp() * 1000),
                'proactive': True
            })
            # 不更新 last_seen，避免重置空闲计时器
            save_history_file(hist_data)

            _last_proactive_time = time.time()

        except Exception as e:
            logger.error(f"[proactive_loop] 异常: {e}", exc_info=True)


# ─────────────────────────────────────────
#  Start
# ─────────────────────────────────────────
if __name__ == '__main__':
    import webbrowser, threading, subprocess, sys

    # 启动时初始化情感系统
    on_session_start()

    def _open_browser():
        __import__('time').sleep(1.2)
        webbrowser.open('http://localhost:5000')

    def _launch_widget():
        __import__('time').sleep(0.8)
        widget_path = os.path.join(os.path.dirname(__file__), 'monitor_widget.py')
        if os.path.exists(widget_path):
            subprocess.Popen([sys.executable, widget_path],
                             creationflags=0x08000000)  # CREATE_NO_WINDOW on Windows

    threading.Thread(target=_open_browser, daemon=True).start()
    threading.Thread(target=_launch_widget, daemon=True).start()
    threading.Thread(target=_proactive_message_loop, daemon=True).start()
    print("涟宗也已启动 -> http://localhost:5000")
    print("  阅读页面    -> http://localhost:5000/reader")
    print("  设置页面    -> http://localhost:5000/settings")
    print("  监控小窗口  -> 自动弹出")
    print("  情感系统    -> 已初始化（OCC认知评价模型）")
    app.run(debug=False, port=5000, threaded=True)
