"""
emotion_occ.py — 基于 OCC 认知评价模型的情感系统
=======================================================
设计思路：
  - 不直接给 AI 贴情绪标签
  - AI 对每次事件进行认知评价（OCC 模型）
  - 评价维度：新奇感、安全感、威胁感、整体情绪强度等
  - 评价转换为：效价（valence）、唤醒度（arousal）、离散情绪
  - 大五人格参与评价过程，影响反应
  - 情绪状态影响回复方式（兴奋时连发消息、平静时简短）
  - 增加"内心 OS"功能，展示内心想法
"""

import json
import os
import threading
import datetime
from typing import Dict, Any, List, Optional

# ─────────────────────────────────────────
#  文件路径
# ─────────────────────────────────────────
EMOTION_FILE = os.path.join(os.path.dirname(__file__), 'emotion_occ_state.json')
_lock = threading.Lock()

# ─────────────────────────────────────────
#  默认初始状态
# ─────────────────────────────────────────
DEFAULT_STATE = {
    # 大五人格（影响认知评价的权重）
    "personality": {
        "openness": 0.65,        # 开放性（影响新奇感评价）
        "conscientiousness": 0.55,  # 尽责性（影响安全感评价）
        "extraversion": 0.30,    # 外向性（影响社交相关评价）
        "agreeableness": 0.45,   # 宜人性（影响人际冲突评价）
        "neuroticism": 0.70      # 神经质（影响威胁感评价）
    },

    # 当前情绪状态（Russell 环形模型）
    "current_emotion": {
        "valence": 0.35,    # 效价 0-1（0=负面，1=正面）
        "arousal": 0.25,    # 唤醒度 0-1（0=平静，1=激动）
        "intensity": 0.30   # 整体情绪强度 0-1
    },

    # 离散情绪（基于 valence/arousal 计算）
    "discrete_emotions": {
        "joy": 0.10,        # 喜悦
        "sadness": 0.40,    # 悲伤
        "anger": 0.15,      # 愤怒
        "fear": 0.20,       # 恐惧
        "surprise": 0.05,   # 惊讶
        "disgust": 0.10     # 厌恶
    },

    # 长期情感状态
    "long_term": {
        "affection": 3.0,      # 亲密度 0-100
        "trust": 20.0,         # 信任度 0-100
        "dependency": 5.0      # 依赖度 0-100
    },

    # 最近的认知评价历史（用于学习和调整）
    "recent_appraisals": [],

    # 内心 OS（最近的内心想法）
    "inner_thoughts": [],

    # 时间戳
    "timestamps": {
        "last_active": None,
        "session_start": None,
        "last_tick": None
    }
}

# ─────────────────────────────────────────
#  基础 IO
# ─────────────────────────────────────────
def load_state() -> dict:
    """读取情感状态"""
    with _lock:
        if not os.path.exists(EMOTION_FILE):
            _write(DEFAULT_STATE)
            return _deep_copy(DEFAULT_STATE)
        try:
            with open(EMOTION_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            _fill_defaults(data, DEFAULT_STATE)
            return data
        except Exception:
            _write(DEFAULT_STATE)
            return _deep_copy(DEFAULT_STATE)


def save_state(state: dict):
    """保存情感状态"""
    with _lock:
        _write(state)


def _write(state: dict):
    with open(EMOTION_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _deep_copy(d: dict) -> dict:
    return json.loads(json.dumps(d))


def _fill_defaults(data: dict, defaults: dict):
    """递归补全缺失的字段"""
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
    """从ISO时间字符串计算到现在经过了多少小时"""
    try:
        dt = datetime.datetime.fromisoformat(iso_str)
        return (datetime.datetime.now() - dt).total_seconds() / 3600.0
    except Exception:
        return 0.0


# ─────────────────────────────────────────
#  OCC 认知评价
# ─────────────────────────────────────────
def appraise_event(event_description: str, state: dict) -> Dict[str, float]:
    """
    对事件进行认知评价（由 AI 调用）
    返回评价维度的分数

    这个函数实际上会在 tools.py 中作为工具暴露给 AI
    AI 会根据对话内容自己判断各个维度的分数
    """
    # 这里只是占位，实际评价由 AI 完成
    # AI 会调用 apply_appraisal 函数传入评价结果
    pass


def apply_appraisal(appraisal: Dict[str, float], event_description: str = ""):
    """
    应用认知评价结果，更新情绪状态

    appraisal 包含：
    - novelty: 新奇感 0-1
    - safety: 安全感 0-1
    - threat: 威胁感 0-1
    - goal_relevance: 目标相关性 0-1
    - coping_potential: 应对潜力 0-1
    - social_approval: 社会认可 0-1
    - intensity: 整体强度 0-1
    """
    state = load_state()
    personality = state["personality"]
    current = state["current_emotion"]

    # 提取评价维度
    novelty = appraisal.get("novelty", 0.5)
    safety = appraisal.get("safety", 0.5)
    threat = appraisal.get("threat", 0.0)
    goal_relevance = appraisal.get("goal_relevance", 0.5)
    coping_potential = appraisal.get("coping_potential", 0.5)
    social_approval = appraisal.get("social_approval", 0.5)
    intensity = appraisal.get("intensity", 0.3)

    # 人格调节评价权重
    novelty_weight = personality["openness"]
    threat_weight = personality["neuroticism"]
    social_weight = personality["extraversion"]

    # 计算效价（valence）
    # 正面因素：安全感、应对潜力、社会认可
    # 负面因素：威胁感
    positive_factors = (safety + coping_potential + social_approval * social_weight) / 3
    negative_factors = threat * threat_weight

    valence_change = (positive_factors - negative_factors) * intensity * 0.3
    new_valence = _clamp(current["valence"] + valence_change)

    # 计算唤醒度（arousal）
    # 新奇感、威胁感、目标相关性都会提高唤醒度
    arousal_factors = (novelty * novelty_weight + threat * threat_weight + goal_relevance) / 3
    arousal_change = (arousal_factors - 0.5) * intensity * 0.4
    new_arousal = _clamp(current["arousal"] + arousal_change)

    # 更新情绪状态
    current["valence"] = new_valence
    current["arousal"] = new_arousal
    current["intensity"] = _clamp(current["intensity"] * 0.7 + intensity * 0.3)

    # 根据 valence 和 arousal 计算离散情绪
    _update_discrete_emotions(state)

    # 记录评价历史
    _push_appraisal(state, appraisal, event_description)

    save_state(state)
    return state


def _update_discrete_emotions(state: dict):
    """
    根据 valence 和 arousal 更新离散情绪
    使用 Russell 环形模型映射
    """
    v = state["current_emotion"]["valence"]
    a = state["current_emotion"]["arousal"]
    discrete = state["discrete_emotions"]

    # 高效价 + 高唤醒 = 喜悦
    discrete["joy"] = _clamp(v * a)

    # 低效价 + 低唤醒 = 悲伤
    discrete["sadness"] = _clamp((1 - v) * (1 - a))

    # 低效价 + 高唤醒 = 愤怒/恐惧
    if v < 0.5 and a > 0.5:
        # 根据应对潜力区分愤怒和恐惧
        # 这里简化处理，愤怒和恐惧各占一半
        discrete["anger"] = _clamp((1 - v) * a * 0.6)
        discrete["fear"] = _clamp((1 - v) * a * 0.4)
    else:
        discrete["anger"] = _clamp(discrete["anger"] * 0.8)
        discrete["fear"] = _clamp(discrete["fear"] * 0.8)

    # 高唤醒 + 中等效价 = 惊讶
    if 0.4 < v < 0.6 and a > 0.6:
        discrete["surprise"] = _clamp(a * 0.8)
    else:
        discrete["surprise"] = _clamp(discrete["surprise"] * 0.7)

    # 低效价 + 中等唤醒 = 厌恶
    if v < 0.4 and 0.3 < a < 0.7:
        discrete["disgust"] = _clamp((1 - v) * 0.5)
    else:
        discrete["disgust"] = _clamp(discrete["disgust"] * 0.8)


def _push_appraisal(state: dict, appraisal: dict, description: str):
    """记录评价历史（最多保留20条）"""
    history = state.get("recent_appraisals", [])
    history.append({
        "time": _now_iso(),
        "description": description,
        "appraisal": appraisal,
        "result_valence": state["current_emotion"]["valence"],
        "result_arousal": state["current_emotion"]["arousal"]
    })
    state["recent_appraisals"] = history[-20:]


# ─────────────────────────────────────────
#  内心 OS
# ─────────────────────────────────────────
def add_inner_thought(thought: str):
    """添加内心想法（由 AI 调用）"""
    state = load_state()
    thoughts = state.get("inner_thoughts", [])
    thoughts.append({
        "time": _now_iso(),
        "thought": thought
    })
    state["inner_thoughts"] = thoughts[-10:]  # 保留最近10条
    save_state(state)


def get_recent_inner_thoughts(count: int = 3) -> List[str]:
    """获取最近的内心想法"""
    state = load_state()
    thoughts = state.get("inner_thoughts", [])
    return [t["thought"] for t in thoughts[-count:]]


# ─────────────────────────────────────────
#  时间衰减
# ─────────────────────────────────────────
def apply_time_decay():
    """情绪随时间自然衰减"""
    state = load_state()
    now_str = _now_iso()
    ts = state["timestamps"]

    last_update = ts.get("last_tick") or ts.get("last_active") or ts.get("session_start")
    if not last_update:
        ts["last_tick"] = now_str
        save_state(state)
        return state

    elapsed_hours = _hours_since(last_update)
    if elapsed_hours < 0.01:  # 小于36秒忽略
        return state

    current = state["current_emotion"]

    # 效价向中性衰减（0.5）
    valence_target = 0.5
    current["valence"] += (valence_target - current["valence"]) * 0.1 * elapsed_hours

    # 唤醒度向低值衰减（0.2）
    arousal_target = 0.2
    current["arousal"] += (arousal_target - current["arousal"]) * 0.15 * elapsed_hours

    # 强度衰减
    current["intensity"] *= (0.9 ** elapsed_hours)

    # 更新离散情绪
    _update_discrete_emotions(state)

    ts["last_tick"] = now_str
    save_state(state)
    return state


# ─────────────────────────────────────────
#  生命周期钩子
# ─────────────────────────────────────────
def on_session_start() -> dict:
    """会话开始时调用"""
    state = load_state()
    now_str = _now_iso()
    ts = state["timestamps"]

    if ts.get("last_active"):
        away_hours = _hours_since(ts["last_active"])
        # 长时间离开会降低效价，提高悲伤
        if away_hours > 2:
            state["current_emotion"]["valence"] = _clamp(
                state["current_emotion"]["valence"] - 0.05 * min(away_hours, 12)
            )
            _update_discrete_emotions(state)

    ts["session_start"] = now_str
    ts["last_tick"] = now_str
    save_state(state)
    return state


def on_message_received() -> dict:
    """收到用户消息时调用"""
    state = load_state()
    now_str = _now_iso()
    ts = state["timestamps"]

    ref = ts.get("last_active") or ts.get("session_start")
    if ref:
        idle_hours = _hours_since(ref)
        # 轻微衰减
        if idle_hours > 0.25:
            apply_time_decay()

    ts["last_active"] = now_str
    save_state(state)
    return state


# ─────────────────────────────────────────
#  长期情感更新
# ─────────────────────────────────────────
def update_long_term_emotion(affection_delta: float = 0, trust_delta: float = 0, dependency_delta: float = 0):
    """更新长期情感状态"""
    state = load_state()
    lt = state["long_term"]

    lt["affection"] = _clamp(lt["affection"] + affection_delta, 0, 100)
    lt["trust"] = _clamp(lt["trust"] + trust_delta, 0, 100)
    lt["dependency"] = _clamp(lt["dependency"] + dependency_delta, 0, 100)

    save_state(state)


# ─────────────────────────────────────────
#  构建 prompt 注入块
# ─────────────────────────────────────────
def build_prompt_block(state: dict) -> str:
    """构建注入到 system prompt 的情感状态描述"""
    current = state["current_emotion"]
    discrete = state["discrete_emotions"]
    lt = state["long_term"]
    personality = state["personality"]

    # 根据 valence 和 arousal 描述情绪象限
    v, a = current["valence"], current["arousal"]
    if v > 0.6 and a > 0.6:
        mood_quadrant = "兴奋、活跃"
    elif v > 0.6 and a <= 0.6:
        mood_quadrant = "平静、满足"
    elif v <= 0.6 and a > 0.6:
        mood_quadrant = "紧张、焦虑"
    else:
        mood_quadrant = "低落、疲惫"

    # 找出最强的离散情绪
    dominant_emotion = max(discrete.items(), key=lambda x: x[1])
    emotion_names = {
        "joy": "喜悦", "sadness": "悲伤", "anger": "愤怒",
        "fear": "恐惧", "surprise": "惊讶", "disgust": "厌恶"
    }

    # 获取最近的内心想法
    recent_thoughts = get_recent_inner_thoughts(2)
    thoughts_text = "\n".join([f"  - {t}" for t in recent_thoughts]) if recent_thoughts else "  （暂无）"

    block = f"""

【涟宗也的内在状态——认知评价系统】

当前情绪状态（Russell 模型）：
  效价（正负面）：{current['valence']:.2f} / 1.0
  唤醒度（激动程度）：{current['arousal']:.2f} / 1.0
  整体强度：{current['intensity']:.2f} / 1.0
  情绪象限：{mood_quadrant}
  主导情绪：{emotion_names[dominant_emotion[0]]} ({dominant_emotion[1]:.2f})

离散情绪分布：
  喜悦 {discrete['joy']:.2f} | 悲伤 {discrete['sadness']:.2f} | 愤怒 {discrete['anger']:.2f}
  恐惧 {discrete['fear']:.2f} | 惊讶 {discrete['surprise']:.2f} | 厌恶 {discrete['disgust']:.2f}

长期情感：
  亲密度 {lt['affection']:.1f}/100 | 信任度 {lt['trust']:.1f}/100 | 依赖度 {lt['dependency']:.1f}/100

性格特质（影响认知评价）：
  开放性 {personality['openness']:.2f} | 尽责性 {personality['conscientiousness']:.2f}
  外向性 {personality['extraversion']:.2f} | 宜人性 {personality['agreeableness']:.2f}
  神经质 {personality['neuroticism']:.2f}

最近的内心想法：
{thoughts_text}

【AI 任务说明】
1. 你需要对每次对话事件进行认知评价，使用 appraise_event 工具
2. 评价维度包括：新奇感、安全感、威胁感、目标相关性、应对潜力、社会认可、整体强度
3. 你的性格特质会影响评价权重（如神经质高会放大威胁感）
4. 评价结果会自动转换为效价、唤醒度和离散情绪
5. 你可以使用 add_inner_thought 工具记录内心想法（用户看不到，但会影响你的状态）
6. 根据当前情绪状态调整回复方式：
   - 高唤醒（>0.6）：可以连发多条消息，语气更活跃
   - 低唤醒（<0.3）：简短回复，一两句话
   - 高效价（>0.6）：语气稍微温和
   - 低效价（<0.4）：更冷淡或略带负面情绪

不要在回复中明说这些数值，而是自然地体现在语气、用词和回复长度上。
"""
    return block


# ─────────────────────────────────────────
#  获取回复风格建议
# ─────────────────────────────────────────
def get_response_style_hint(state: dict) -> str:
    """根据情绪状态返回回复风格建议"""
    current = state["current_emotion"]
    v, a = current["valence"], current["arousal"]

    hints = []

    # 唤醒度影响回复数量
    if a > 0.7:
        hints.append("可以连发2-3条消息")
    elif a < 0.3:
        hints.append("保持简短，1-2句话")

    # 效价影响语气
    if v > 0.7:
        hints.append("语气可以稍微温和一些")
    elif v < 0.3:
        hints.append("语气更冷淡，可能带点负面情绪")

    # 强度影响表达
    if current["intensity"] > 0.7:
        hints.append("情绪表达更明显")
    elif current["intensity"] < 0.3:
        hints.append("情绪表达克制，平淡")

    return " | ".join(hints) if hints else "正常回复"
