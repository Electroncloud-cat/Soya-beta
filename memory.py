import json, os, datetime, math

MEMORY_FILE = 'memory.json'

def _migrate_old_entry(key: str, entry: dict) -> dict:
    """将旧格式 {value, time} 升级为新格式，保留原有值。"""
    if "created" not in entry:
        now = datetime.datetime.now().isoformat()
        entry.setdefault("created", now)
        entry.setdefault("last_active", now)
        entry.setdefault("importance", 5)
        entry.setdefault("valence", 0.5)
        entry.setdefault("arousal", 0.3)
        entry.setdefault("activation_count", 0)
        entry.setdefault("tags", [])
        entry.setdefault("type", "dynamic")
        entry.setdefault("resolved", False)
        entry.setdefault("pinned", False)
        entry.setdefault("digested", False)
    return entry

def calculate_decay_score(entry: dict) -> float:
    """
    计算记忆的当前活跃度分数（改进版艾宾浩斯遗忘曲线）。
    pinned / permanent → 999.0（永不衰减）
    feel              → 50.0（固定，不参与普通排序）
    archived          → 0.0
    """
    t = entry.get("type", "dynamic")
    if entry.get("pinned") or t == "permanent":
        return 999.0
    if t == "feel":
        return 50.0
    if t == "archived":
        return 0.0

    importance       = max(1, min(10, int(entry.get("importance", 5))))
    activation_count = max(1.0, float(entry.get("activation_count", 0) or 0))
    arousal          = max(0.0, min(1.0, float(entry.get("arousal", 0.3) or 0.3)))

    last_active_str = entry.get("last_active") or entry.get("created") or entry.get("time", "")
    try:
        if "T" in last_active_str:
            last_active = datetime.datetime.fromisoformat(last_active_str)
        else:
            last_active = datetime.datetime.strptime(last_active_str, "%Y-%m-%d %H:%M")
        days_since = max(0.0, (datetime.datetime.now() - last_active).total_seconds() / 86400)
    except Exception:
        days_since = 30.0

    hours = days_since * 24.0
    freshness_bonus = 1.0 + math.exp(-hours / 36.0)   # 刚存入×2.0，36h后×1.5，72h后趋近×1.0
    emotion_weight  = 1.0 + arousal * 0.8

    if days_since <= 3.0:
        combined = freshness_bonus * 0.7 + emotion_weight * 0.3
    else:
        combined = emotion_weight * 0.7 + freshness_bonus * 0.3

    LAMBDA     = 0.05
    base_score = importance * (activation_count ** 0.3) * math.exp(-LAMBDA * days_since) * combined

    resolved = entry.get("resolved", False)
    digested = entry.get("digested", False)
    if resolved and digested:
        resolved_factor = 0.02
    elif resolved:
        resolved_factor = 0.05
    else:
        resolved_factor = 1.0

    urgency = 1.5 if (arousal > 0.7 and not resolved) else 1.0

    return round(base_score * resolved_factor * urgency, 4)

def auto_archive_pass():
    """将衰减分低于阈值的 dynamic 记忆标记为 archived。每 6 小时执行一次。"""
    if not os.path.exists(MEMORY_FILE):
        return

    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return

    # 检查上次归档时间
    last_check_str = data.get('_last_archive_check', '')
    now = datetime.datetime.now()

    if last_check_str:
        try:
            last_check = datetime.datetime.fromisoformat(last_check_str)
            hours_since = (now - last_check).total_seconds() / 3600
            if hours_since < 6:
                return  # 距上次检查不足 6 小时，跳过
        except Exception:
            pass

    # 执行归档
    changed = False
    for key, entry in data.items():
        if key.startswith('_'):  # 跳过元数据字段
            continue
        if entry.get('type') == 'dynamic':
            score = calculate_decay_score(entry)
            if score < 0.3:
                entry['type'] = 'archived'
                changed = True

    # 更新检查时间
    data['_last_archive_check'] = now.isoformat()

    if changed:
        with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

def load_all() -> dict:
    """加载所有记忆，自动迁移旧格式，执行自动归档。"""
    if not os.path.exists(MEMORY_FILE):
        return {}

    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return {}

    # 迁移旧格式
    changed = False
    for key, entry in data.items():
        if key.startswith('_'):  # 跳过元数据字段
            continue
        if isinstance(entry, dict) and "created" not in entry:
            _migrate_old_entry(key, entry)
            changed = True

    if changed:
        with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # 执行自动归档检查
    auto_archive_pass()

    # 重新加载（如果归档修改了文件）
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            pass

    # 过滤掉元数据字段
    return {k: v for k, v in data.items() if not k.startswith('_')}

def _write(data):
    """写入记忆数据，保留元数据字段。"""
    # 读取现有元数据
    metadata = {}
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                existing = json.load(f)
                metadata = {k: v for k, v in existing.items() if k.startswith('_')}
        except Exception:
            pass

    # 合并数据和元数据
    full_data = {**metadata, **data}

    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(full_data, f, ensure_ascii=False, indent=2)

def save_memory(key: str, value: str):
    """兼容旧接口，自动补默认元数据。"""
    save_memory_rich(key, value)

def save_memory_rich(key: str, value: str, importance: int = 5,
                     valence: float = 0.5, arousal: float = 0.3,
                     tags: list = None, type: str = "dynamic",
                     pinned: bool = False):
    """保存记忆（完整版，支持所有元数据）。"""
    data = load_all()
    now = datetime.datetime.now()
    existing = data.get(key, )
    data[key] = {
        "value":            value,
        "time":             now.strftime('%Y-%m-%d %H:%M'),
        "created":          existing.get("created") or now.isoformat(),
        "last_active":      now.isoformat(),
        "importance":       max(1, min(10, int(importance))),
        "valence":          max(0.0, min(1.0, float(valence))),
        "arousal":          max(0.0, min(1.0, float(arousal))),
        "activation_count": float(existing.get("activation_count", 0)),
        "tags":             tags or [],
        "type":             type,
        "resolved":         existing.get("resolved", False),
        "pinned":           pinned,
        "digested":         existing.get("digested", False),
    }
    _write(data)

def touch_memory(key: str):
    """记忆被引用时调用，增加激活计数，施加涟漪加成。"""
    data = load_all()
    if key not in data:
        return

    entry = data[key]
    entry["activation_count"] = float(entry.get("activation_count", 0)) + 1.0
    entry["last_active"] = datetime.datetime.now().isoformat()

    # 涟漪加成：对 48h 内创建的相邻记忆 +0.3，最多 3 条
    ripple_target = datetime.datetime.now()
    ripple_count = 0
    for k, e in data.items():
        if k == key or ripple_count >= 3:
            continue
        try:
            c = datetime.datetime.fromisoformat(e.get("created", ""))
            if abs((c - ripple_target).total_seconds()) < 172800:  # 48h
                e["activation_count"] = float(e.get("activation_count", 0)) + 0.3
                ripple_count += 1
        except Exception:
            pass

    _write(data)

def save_feel(key: str, content: str):
    """保存自省感受（feel 类型记忆）。"""
    save_memory_rich(key, content, importance=7, type="feel", valence=0.5, arousal=0.2)

def get_memory_summary(max_tokens_estimate: int = 800) -> str:
    """按衰减分数排序，返回最重要的记忆摘要。"""
    data = load_all()
    if not data:
        return '（暂无记忆）'

    pinned_entries  = []
    dynamic_entries = []

    for k, v in data.items():
        t = v.get("type", "dynamic")
        if t == "archived" or t == "feel":
            continue
        score = calculate_decay_score(v)
        if v.get("pinned") or t == "permanent":
            pinned_entries.append((k, v, score))
        else:
            dynamic_entries.append((k, v, score))

    pinned_entries.sort(key=lambda x: -x[2])
    dynamic_entries.sort(key=lambda x: -x[2])

    lines = []
    char_budget = max_tokens_estimate * 3  # 粗估：1 token ≈ 3 中文字

    for k, v, score in pinned_entries:
        tag  = "【核心】" if v.get("pinned") else "【长期】"
        line = f"- {tag} {k}: {v['value']}（重要度 {v.get('importance', 5)}）"
        lines.append(line)
        char_budget -= len(line)

    for k, v, score in dynamic_entries:
        if char_budget <= 0:
            break
        resolved_tag = "（已释怀）" if v.get("resolved") else ""
        line = f"- {k}: {v['value']}{resolved_tag}（重要度 {v.get('importance', 5)}，活跃度 {score:.1f}）"
        lines.append(line)
        char_budget -= len(line)

    return '\n'.join(lines) if lines else '（暂无记忆）'

def get_feel_summary() -> str:
    """返回所有 feel 类型记忆。"""
    data = load_all()
    feels = [(k, v) for k, v in data.items() if v.get("type") == "feel"]
    if not feels:
        return ""
    lines = [f"- {k}: {v['value']}" for k, v in feels]
    return '\n'.join(lines)

def delete_memory(key: str):
    """删除指定记忆。"""
    data = load_all()
    data.pop(key, None)
    _write(data)
