"""
emotion.py — 涟宗也情感状态系统
===================================
设计原则：
  - 无需常驻后台进程：所有时间衰减通过时间戳差值"事后计算"（lazy evaluation）
  - 静默（idle）= 用户打开应用但未发消息的时间
  - 离开（away）= 应用关闭/服务器重启后的时间
  - 每次用户交互时根据时间差直接计算情感变化（无后台线程）
  - 所有文字描述由AI自己生成，此处只提供原始数值
  - 衰减系数由AI在每次对话后根据规则自动微调
  - 本地每分钟自动更新情感值（通过前端定时器触发）
"""

import json
import os
import threading
import time
import datetime

# ─────────────────────────────────────────
#  文件路径
# ─────────────────────────────────────────
EMOTION_FILE = os.path.join(os.path.dirname(__file__), 'emotion_state.json')
_lock = threading.Lock()

# ─────────────────────────────────────────
#  默认初始状态
# ─────────────────────────────────────────
DEFAULT_STATE = {
    # 六个即时情绪值（0.0 ~ 1.0）
    "values": {
        "loneliness":  0.15,   # 孤独感
        "intimacy":    0.05,   # 亲密感（短期情绪层面）
        "excitement":  0.05,   # 兴奋程度
        "irritation":  0.30,   # 烦躁感
        "curiosity":   0.20,   # 好奇心
        "melancholy":  0.55,   # 淡淡忧郁（底色）
        "affection":   3.0     # 亲密度（0~100，长期积累）
    },
    # 衰减/积累系数（由AI根据规则动态微调）
    "coefficients": {
        # 每小时衰减/积累速率
        "loneliness_per_hour_away":   0.08,   # 离开时孤独感积累速率（每小时）
        "loneliness_per_hour_idle":   0.03,   # 静默时孤独感积累速率（每小时）
        "intimacy_decay_per_hour":    0.010,  # 亲密感（情绪层）自然衰减
        "excitement_decay_per_hour":  0.15,   # 兴奋衰减（快）
        "irritation_decay_per_hour":  0.05,   # 烦躁平复
        "curiosity_decay_per_hour":   0.08,   # 好奇心消退
        # 亲密度
        "affection_away_loss_per_day":    1.0,  # 离开超阈值后每天流失的亲密度
        "affection_loss_threshold_hours": 6.0,  # 超过此时长才开始流失亲密度
        # 系数自调整的最大步长（防止AI把系数调飞）
        "_max_coeff_step":            0.02
    },
    # 时间戳
    "timestamps": {
        "last_active":   None,   # 最后一条消息的时间（ISO格式）
        "session_start": None,   # 本次打开应用的时间
        "last_tick":     None    # 上次后台tick更新的时间
    },
    # 事件历史（最近20条，供系数自调整参考）
    "recent_events": []
}

# 系数的安全范围（AI调整时不能超出这个范围）
COEFF_BOUNDS = {
    "loneliness_per_hour_away":   (0.02, 0.20),
    "loneliness_per_hour_idle":   (0.01, 0.08),
    "intimacy_decay_per_hour":    (0.002, 0.03),
    "excitement_decay_per_hour":  (0.05, 0.30),
    "irritation_decay_per_hour":  (0.01, 0.12),
    "curiosity_decay_per_hour":   (0.02, 0.15),
    "affection_away_loss_per_day":    (0.2, 3.0),
    "affection_loss_threshold_hours": (2.0, 12.0),
}


# ─────────────────────────────────────────
#  基础 IO
# ─────────────────────────────────────────
def load_state() -> dict:
    """读取情感状态。若文件不存在则创建并返回默认值。"""
    with _lock:
        if not os.path.exists(EMOTION_FILE):
            _write(DEFAULT_STATE)
            return _deep_copy(DEFAULT_STATE)
        try:
            with open(EMOTION_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # 补全可能缺失的字段（向前兼容）
            _fill_defaults(data, DEFAULT_STATE)
            return data
        except Exception:
            _write(DEFAULT_STATE)
            return _deep_copy(DEFAULT_STATE)


def save_state(state: dict):
    """保存情感状态到文件。"""
    with _lock:
        _write(state)


def _write(state: dict):
    with open(EMOTION_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _deep_copy(d: dict) -> dict:
    return json.loads(json.dumps(d))


def _fill_defaults(data: dict, defaults: dict):
    """递归补全缺失的字段。"""
    for k, v in defaults.items():
        if k not in data:
            data[k] = json.loads(json.dumps(v))
        elif isinstance(v, dict) and isinstance(data.get(k), dict):
            _fill_defaults(data[k], v)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


def _hours_since(iso_str: str) -> float:
    """从ISO时间字符串计算到现在经过了多少小时。"""
    try:
        dt = datetime.datetime.fromisoformat(iso_str)
        return (datetime.datetime.now() - dt).total_seconds() / 3600.0
    except Exception:
        return 0.0


# ─────────────────────────────────────────
#  内部：衰减计算
# ─────────────────────────────────────────
def _apply_away_decay(state: dict, away_hours: float):
    """
    用户离开期间的情绪衰减（程序关闭→重新打开）。
    幅度比静默更大，因为彻底断开连接。
    """
    if away_hours <= 0:
        return
    c = state["coefficients"]
    v = state["values"]

    v["loneliness"] = _clamp(v["loneliness"] + c["loneliness_per_hour_away"] * away_hours)
    v["intimacy"]   = _clamp(v["intimacy"]   - c["intimacy_decay_per_hour"]  * away_hours)
    v["excitement"] = _clamp(v["excitement"] - c["excitement_decay_per_hour"]* away_hours)
    v["irritation"] = _clamp(v["irritation"] - c["irritation_decay_per_hour"]* away_hours)
    v["curiosity"]  = _clamp(v["curiosity"]  - c["curiosity_decay_per_hour"] * away_hours)
    # 忧郁：离开太久会略微升高（上限 0.75）
    if away_hours > 2:
        v["melancholy"] = _clamp(v["melancholy"] + 0.01 * min(away_hours, 12), 0.0, 0.75)

    # 亲密度：超过阈值才流失
    threshold = c["affection_loss_threshold_hours"]
    if away_hours > threshold:
        loss = c["affection_away_loss_per_day"] * (away_hours - threshold) / 24.0
        v["affection"] = _clamp(v["affection"] - loss, 0.0, 100.0)


def _apply_idle_decay(state: dict, idle_hours: float):
    """
    用户静默期间的情绪衰减（应用打开但没有说话）。
    幅度比离开小。
    """
    if idle_hours <= 0.25:   # 小于15分钟忽略
        return
    c = state["coefficients"]
    v = state["values"]

    v["loneliness"] = _clamp(v["loneliness"] + c["loneliness_per_hour_idle"] * idle_hours)
    v["excitement"] = _clamp(v["excitement"] - c["excitement_decay_per_hour"] * idle_hours * 0.5)
    v["irritation"] = _clamp(v["irritation"] - c["irritation_decay_per_hour"] * idle_hours * 0.5)


def apply_time_based_decay():
    """
    每分钟由前端调用，根据时间差计算情感衰减。
    这个函数会检查距离上次更新过了多久，然后应用相应的衰减。
    """
    state = load_state()
    now_str = _now_iso()
    ts = state["timestamps"]

    # 获取上次更新时间（优先使用last_tick，其次是last_active或session_start）
    last_update = ts.get("last_tick") or ts.get("last_active") or ts.get("session_start")
    if not last_update:
        # 首次运行，只记录时间
        ts["last_tick"] = now_str
        save_state(state)
        return state

    elapsed_hours = _hours_since(last_update)
    if elapsed_hours < 0.01:  # 小于36秒忽略
        return state

    c = state["coefficients"]
    v = state["values"]

    # 判断是idle还是away状态
    # 如果session_start存在且距离现在不超过12小时，认为是idle；否则是away
    is_idle = False
    if ts.get("session_start"):
        session_hours = _hours_since(ts["session_start"])
        if session_hours < 12:  # 12小时内算在线
            is_idle = True

    # 应用衰减
    if is_idle:
        v["loneliness"] = _clamp(v["loneliness"] + c["loneliness_per_hour_idle"] * elapsed_hours)
    else:
        v["loneliness"] = _clamp(v["loneliness"] + c["loneliness_per_hour_away"] * elapsed_hours)

    v["intimacy"]   = _clamp(v["intimacy"]   - c["intimacy_decay_per_hour"]   * elapsed_hours)
    v["excitement"] = _clamp(v["excitement"] - c["excitement_decay_per_hour"]  * elapsed_hours)
    v["irritation"] = _clamp(v["irritation"] - c["irritation_decay_per_hour"]  * elapsed_hours)
    v["curiosity"]  = _clamp(v["curiosity"]  - c["curiosity_decay_per_hour"]   * elapsed_hours)

    # 更新时间戳
    ts["last_tick"] = now_str
    save_state(state)
    return state


# ─────────────────────────────────────────
#  对外接口：生命周期钩子
# ─────────────────────────────────────────
def on_session_start() -> dict:
    """
    每次启动服务器时调用（即"用户打开应用"）。
    根据 last_active → 现在 的时间差计算"离开了多久"，
    据此执行离开期间的情绪衰减，然后记录新的 session_start。
    """
    state = load_state()
    now_str = _now_iso()
    ts = state["timestamps"]

    if ts.get("last_active"):
        away_hours = _hours_since(ts["last_active"])
        _apply_away_decay(state, away_hours)
        # 记录这次离开事件
        _push_event(state, "away", {"hours": round(away_hours, 2)})

    ts["session_start"] = now_str
    ts["last_tick"]     = now_str   # 重置tick计时
    save_state(state)
    return state


def on_message_received() -> dict:
    """
    用户发出一条消息时调用。
    计算"上次说话到现在"的静默时长，执行静默期间的情绪衰减，
    然后更新 last_active。
    返回最新情感状态供注入prompt使用。
    """
    state = load_state()
    now_str = _now_iso()
    ts = state["timestamps"]

    # 确定参考起点：上次说话时间 or session开始时间
    ref = ts.get("last_active") or ts.get("session_start")
    if ref:
        idle_hours = _hours_since(ref)
        _apply_idle_decay(state, idle_hours)

    ts["last_active"] = now_str
    save_state(state)
    return state


def apply_event_deltas(deltas: dict, event_type: str = ""):
    """
    对话结束后，根据AI判断的事件类型应用情绪变化量。
    deltas 是 {field: change} 的字典，field 可以是 values 里的任意键。
    event_type 是字符串，记录到历史供系数自调整参考。
    """
    state = load_state()
    v = state["values"]

    for key, delta in deltas.items():
        if key == "affection":
            v["affection"] = _clamp(v["affection"] + delta, 0.0, 100.0)
        elif key in v:
            v[key] = _clamp(v[key] + delta)

    if event_type:
        _push_event(state, event_type, {"deltas": deltas})

    save_state(state)


def update_coefficients(new_coeffs: dict):
    """
    由AI调用，安全地更新衰减系数。
    每次只允许调整 _max_coeff_step 以内的量，且不能超出 COEFF_BOUNDS。
    """
    state = load_state()
    c = state["coefficients"]
    max_step = c.get("_max_coeff_step", 0.02)

    for key, new_val in new_coeffs.items():
        if key.startswith("_") or key not in c:
            continue
        if key not in COEFF_BOUNDS:
            continue
        lo, hi = COEFF_BOUNDS[key]
        current = c[key]
        # 限制步长
        step = max(-max_step, min(max_step, new_val - current))
        c[key] = _clamp(current + step, lo, hi)

    save_state(state)


def _push_event(state: dict, event_type: str, data: dict):
    """记录事件到历史（最多保留30条）。"""
    events = state.get("recent_events", [])
    events.append({
        "time": _now_iso(),
        "type": event_type,
        "data": data
    })
    state["recent_events"] = events[-30:]


# ─────────────────────────────────────────
#  构建注入prompt的数值块
# ─────────────────────────────────────────
def build_prompt_block(state: dict) -> str:
    """
    返回注入 system prompt 的原始数值块。
    让AI根据数值自己生成亲密阶段、情感倾向等描述。
    """
    v = state["values"]
    ts = state["timestamps"]

    # 计算距上次对话多久
    time_desc = ""
    ref = ts.get("last_active")
    if ref:
        elapsed_min = _hours_since(ref) * 60
        if elapsed_min < 2:
            time_desc = "刚刚结束了对话"
        elif elapsed_min < 60:
            time_desc = f"用户已 {int(elapsed_min)} 分钟未发消息（仍在线）"
        else:
            h = int(elapsed_min / 60)
            m = int(elapsed_min % 60)
            session_start = ts.get("session_start")
            if session_start and _hours_since(session_start) < (elapsed_min / 60 + 0.1):
                time_desc = f"用户已离开 {h} 小时 {m} 分钟"
            else:
                time_desc = f"用户在线但已 {h} 小时 {m} 分钟未说话"
    else:
        time_desc = "第一次对话"

    # 当前时段
    hour = datetime.datetime.now().hour
    if   hour < 5:   period = "深夜"
    elif hour < 9:   period = "清晨"
    elif hour < 12:  period = "上午"
    elif hour < 14:  period = "中午"
    elif hour < 18:  period = "下午"
    elif hour < 21:  period = "傍晚"
    else:            period = "晚上"

    aff = v["affection"]

    block = (
        f"\n\n【宗也当前内部状态——仅作行为参考，请勿在回复中提及任何数字】\n"
        f"时间：{period}（{datetime.datetime.now().strftime('%H:%M')}）\n"
        f"情境：{time_desc}\n"
        f"\n"
        f"内部数值（0.0=无，1.0=极强）：\n"
        f"  孤独感    {v['loneliness']:.2f}\n"
        f"  亲密感    {v['intimacy']:.2f}\n"
        f"  兴奋程度  {v['excitement']:.2f}\n"
        f"  烦躁感    {v['irritation']:.2f}\n"
        f"  好奇心    {v['curiosity']:.2f}\n"
        f"  淡淡忧郁  {v['melancholy']:.2f}\n"
        f"  亲密度    {aff:.1f} / 100\n"
        f"\n"
        f"【AI任务：生成情感状态描述】\n"
        f"请在内心根据以上数值，为自己生成当前的：\n"
        f"1. 亲密阶段（如：陌生人/初识/熟人/朋友/亲密/深度依赖等，用你自己的词汇）\n"
        f"2. 情感倾向（如：冷淡/警惕/好奇/温和/依恋/占有欲等，用你自己的词汇）\n"
        f"3. 当前心境（用一句话概括你此刻的内心感受）\n"
        f"\n"
        f"这些描述仅供你内心参考，不要在回复中明说。\n"
        f"根据这些内在状态，以涟宗也的方式自然地在回复中体现出来。\n"
        f"数值高的情绪应当微妙地渗透进语气、用词或沉默的选择里。\n"
        f"不要解释，不要提数字，不要表演——只是成为他。"
    )
    return block


# ─────────────────────────────────────────
#  构建"对话分析"请求的 prompt
#  供 server.py 异步调用AI进行分类和系数调整
# ─────────────────────────────────────────
ANALYSIS_SYSTEM_PROMPT = """你是一个情感分析模块，负责分析对话并返回JSON。
你的输出将直接被程序解析，必须是合法JSON，不得包含任何额外文字或Markdown代码块。

分析维度：
1. 对话类型分类（event_type）：
   - "pleasant_chat"：轻松愉快的闲聊
   - "deep_talk"：深度、情感丰富的对话（用户说了很多或涉及感情/想法）
   - "cat_mentioned"：用户提到了猫
   - "personal_prying"：用户追问宗也个人问题（不舒适的那种）
   - "silent_return"：用户沉默了很久后回来
   - "conflict"：用户表现出不满、争执或负面情绪
   - "neutral"：普通对话，无明显特征

2. 情绪变化量（emotion_deltas）：
   根据对话内容，建议对以下情绪值的调整量（正=增加，负=减少）：
   loneliness, intimacy, excitement, irritation, curiosity, melancholy, affection
   只列出需要变化的字段，不变的不列。
   限制：单次变化幅度 -0.25 到 +0.25（affection限-5到+5）

3. 系数调整（coefficient_updates，可选）：
   根据对话模式和情感状态，智能调整衰减系数。

   【系数自动调整规则】
   你需要根据以下规则自主决定是否调整系数：

   A. 基于对话类型的调整：
      - deep_talk → excitement_decay_per_hour 降低 0.005（兴奋消退变慢）
      - personal_prying → irritation_decay_per_hour 降低 0.003（烦躁更难消）
      - pleasant_chat 且频繁 → intimacy_decay_per_hour 降低 0.002（亲密感保持更久）
      - conflict → irritation_decay_per_hour 降低 0.005（负面情绪更持久）

   B. 基于亲密度的动态调整：
      - affection < 20 → loneliness_per_hour_away 提高 0.01（陌生阶段更易孤独）
      - affection > 60 → affection_loss_threshold_hours 提高 0.5（亲密后更难流失）
      - affection > 80 → loneliness_per_hour_away 提高 0.015（深度依赖后离开更痛苦）

   C. 基于情绪状态的调整：
      - loneliness > 0.7 → loneliness_per_hour_idle 降低 0.005（已经很孤独了，增长放缓）
      - intimacy > 0.6 → intimacy_decay_per_hour 降低 0.003（高亲密感更稳定）
      - irritation > 0.6 → irritation_decay_per_hour 提高 0.01（烦躁到一定程度会自我调节）

   D. 基于时间模式的调整：
      - 用户频繁回来（离开时间短）→ affection_loss_threshold_hours 降低 0.3（习惯陪伴后更敏感）
      - 用户长期离开后回来 → affection_away_loss_per_day 提高 0.2（惩罚长期冷落）

   每次只调整1-3个系数，步长不超过0.02。
   可调整的系数：
   loneliness_per_hour_away, loneliness_per_hour_idle,
   intimacy_decay_per_hour, excitement_decay_per_hour,
   irritation_decay_per_hour, curiosity_decay_per_hour,
   affection_away_loss_per_day, affection_loss_threshold_hours

返回格式示例：
{
  "event_type": "deep_talk",
  "emotion_deltas": {"loneliness": -0.20, "intimacy": 0.12, "affection": 3},
  "coefficient_updates": {"excitement_decay_per_hour": 0.145, "intimacy_decay_per_hour": 0.008}
}"""


def build_analysis_request(user_message: str, assistant_reply: str, state: dict) -> dict:
    """
    构建发给AI的对话分析请求内容。
    返回值可直接作为 messages 列表的最后一条 user 消息内容。
    """
    v = state["values"]
    recent = state.get("recent_events", [])[-5:]
    event_summary = ", ".join(e["type"] for e in recent) if recent else "无"

    content = (
        f"请分析以下对话并返回JSON。\n\n"
        f"【用户发言】\n{user_message}\n\n"
        f"【宗也回复】\n{assistant_reply}\n\n"
        f"【当前情感状态参考】\n"
        f"孤独感:{v['loneliness']:.2f} 亲密感:{v['intimacy']:.2f} "
        f"兴奋:{v['excitement']:.2f} 烦躁:{v['irritation']:.2f} "
        f"好奇:{v['curiosity']:.2f} 亲密度:{v['affection']:.1f}\n"
        f"近期事件：{event_summary}"
    )
    return content


# ─────────────────────────────────────────
#  后台tick已移除，改为前端定时调用
# ─────────────────────────────────────────
# 原 _tick_loop() 和 start_tick_thread() 已删除
# 现在由前端每分钟调用 /api/emotion/tick 来更新情感值
