# -*- coding: utf-8 -*-
"""
Ombre-Brain 适配层
将 Ombre-Brain 的记忆系统适配到涟宗也项目
"""
import os
import json
import logging
import datetime
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# 全局实例（延迟初始化）
_bucket_mgr = None
_dehydrator = None
_decay_engine = None
_embedding_engine = None
_initialized = False


def _ensure_initialized():
    """确保 Ombre 组件已初始化"""
    global _bucket_mgr, _dehydrator, _decay_engine, _embedding_engine, _initialized

    if _initialized:
        return

    try:
        from ombre.bucket_manager import BucketManager
        from ombre.dehydrator import Dehydrator
        from ombre.decay_engine import DecayEngine
        from ombre.embedding_engine import EmbeddingEngine

        # 加载配置
        config = _load_ombre_config()

        # 初始化组件
        _embedding_engine = EmbeddingEngine(config)
        _bucket_mgr = BucketManager(config, embedding_engine=_embedding_engine)
        _dehydrator = Dehydrator(config)
        _decay_engine = DecayEngine(config, _bucket_mgr)

        # 启动衰减引擎
        _decay_engine.ensure_started()

        _initialized = True
        logger.info("[Ombre] 记忆系统初始化成功")
    except Exception as e:
        logger.error(f"[Ombre] 初始化失败: {e}")
        raise


def _load_ombre_config() -> Dict[str, Any]:
    """加载 Ombre 配置（从 settings.json 读取）"""
    try:
        if os.path.exists('settings.json'):
            with open('settings.json', 'r', encoding='utf-8') as f:
                settings = json.load(f)
        else:
            settings = {}

        # 构建 Ombre 配置
        config = {
            # API 配置（使用涟宗也的 API 配置）
            "api_base": settings.get("api_base", "").rstrip('/'),
            "api_key": settings.get("api_key", ""),
            "model": settings.get("model", "claude-sonnet-4-20250514"),

            # 记忆桶目录
            "buckets_dir": settings.get("ombre_buckets_dir", "./ombre_buckets"),

            # 向量数据库
            "vector_db_path": settings.get("ombre_vector_db", "./ombre_buckets/vectors.db"),

            # 衰减引擎配置
            "decay": {
                "enabled": settings.get("ombre_decay_enabled", True),
                "check_interval_hours": settings.get("ombre_decay_interval", 24),
                "archive_threshold": settings.get("ombre_archive_threshold", 0.3),
            },

            # 日志级别
            "log_level": "INFO",
        }

        # 确保目录存在
        os.makedirs(config["buckets_dir"], exist_ok=True)
        os.makedirs(os.path.dirname(config["vector_db_path"]), exist_ok=True)

        return config
    except Exception as e:
        logger.error(f"[Ombre] 加载配置失败: {e}")
        # 返回默认配置
        return {
            "api_base": "",
            "api_key": "",
            "model": "claude-sonnet-4-20250514",
            "buckets_dir": "./ombre_buckets",
            "vector_db_path": "./ombre_buckets/vectors.db",
            "decay": {"enabled": True, "check_interval_hours": 24, "archive_threshold": 0.3},
            "log_level": "INFO",
        }


def save_memory_ombre(key: str, value: str, importance: int = 5,
                      valence: float = 0.5, arousal: float = 0.3,
                      tags: Optional[List[str]] = None,
                      memory_type: str = "dynamic") -> str:
    """
    保存记忆到 Ombre-Brain

    Args:
        key: 记忆标题
        value: 记忆内容
        importance: 重要度 1-10
        valence: 情感效价 0-1 (0=负面, 1=正面)
        arousal: 情感唤醒度 0-1
        tags: 标签列表
        memory_type: 记忆类型 (dynamic/permanent/feel)

    Returns:
        bucket_id
    """
    try:
        _ensure_initialized()

        # 构建内容（包含标题）
        content = f"# {key}\n\n{value}"

        # 调用 Ombre 的 hold 功能
        # 注意：Ombre 会自动分析 domain/tags，但我们可以提供提示
        result = _bucket_mgr.create(
            content=content,
            importance=importance,
            valence=valence,
            arousal=arousal,
            tags=tags or [],
            bucket_type=memory_type,
            name=key,
        )

        bucket_id = result.get("id", "")
        logger.info(f"[Ombre] 保存记忆成功: {key} -> {bucket_id}")
        return bucket_id

    except Exception as e:
        logger.error(f"[Ombre] 保存记忆失败: {e}")
        return ""


def get_memory_summary_ombre(max_tokens_estimate: int = 600,
                             query: str = "",
                             domain: str = "") -> str:
    """
    获取记忆摘要（用于注入到对话上下文）

    Args:
        max_tokens_estimate: 最大 token 数
        query: 搜索关键词（空则浮现模式）
        domain: 领域过滤（如 "feel" 只返回感受类记忆）

    Returns:
        记忆摘要文本
    """
    try:
        _ensure_initialized()

        # 调用 Ombre 的 breath 功能
        if domain == "feel":
            # Feel 专用通道
            buckets = _bucket_mgr.list_all(include_archive=False)
            feel_buckets = [b for b in buckets if b.get("type") == "feel"]
            feel_buckets.sort(key=lambda x: x.get("created", ""), reverse=True)

            # 构建输出
            lines = ["【涟宗也的自省感受】"]
            for bucket in feel_buckets[:5]:  # 最多5条
                content = bucket.get("content", "").strip()
                lines.append(f"- {content[:200]}")

            return "\n".join(lines)

        elif query:
            # 搜索模式
            results = _bucket_mgr.search(
                query=query,
                max_results=10,
                include_archive=False,
            )

            lines = [f"【与"{query}"相关的记忆】"]
            for bucket in results:
                name = bucket.get("name", "未命名")
                content = bucket.get("content", "")
                # 简单压缩
                summary = content[:150] + "..." if len(content) > 150 else content
                lines.append(f"- {name}: {summary}")

            return "\n".join(lines)

        else:
            # 浮现模式（自动推荐）
            buckets = _bucket_mgr.list_all(include_archive=False)

            # 筛选钉选和高重要度记忆
            pinned = [b for b in buckets if b.get("pinned") or b.get("protected")]
            unresolved = [b for b in buckets if not b.get("resolved") and b not in pinned]

            # 按衰减分排序
            scored = []
            for bucket in unresolved:
                score = _decay_engine.calculate_score(bucket)
                scored.append((score, bucket))
            scored.sort(reverse=True, key=lambda x: x[0])

            # 构建输出
            lines = ["【涟宗也的记忆】"]

            # 钉选记忆
            if pinned:
                lines.append("\n核心准则：")
                for bucket in pinned[:3]:
                    name = bucket.get("name", "")
                    content = bucket.get("content", "")
                    summary = content[:100] + "..." if len(content) > 100 else content
                    lines.append(f"📌 {name}: {summary}")

            # 浮现记忆
            if scored:
                lines.append("\n浮现记忆：")
                for score, bucket in scored[:5]:
                    name = bucket.get("name", "")
                    content = bucket.get("content", "")
                    summary = content[:100] + "..." if len(content) > 100 else content
                    lines.append(f"[权重:{score:.2f}] {name}: {summary}")

            return "\n".join(lines)

    except Exception as e:
        logger.error(f"[Ombre] 获取记忆摘要失败: {e}")
        return "（记忆系统暂时不可用）"


def dream_ombre() -> str:
    """
    触发自省/做梦功能
    返回最近记忆供 AI 反思
    """
    try:
        _ensure_initialized()

        # 获取最近的 dynamic 记忆
        buckets = _bucket_mgr.list_all(include_archive=False)
        dynamic = [b for b in buckets if b.get("type") == "dynamic"]
        dynamic.sort(key=lambda x: x.get("last_active", x.get("created", "")), reverse=True)

        lines = ["【最近的记忆（供自省）】"]
        for bucket in dynamic[:10]:
            name = bucket.get("name", "")
            content = bucket.get("content", "")
            digested = bucket.get("digested", False)

            summary = content[:150] + "..." if len(content) > 150 else content
            status = "✓已消化" if digested else "待消化"
            lines.append(f"- [{status}] {name}: {summary}")

        lines.append("\n提示：你可以对这些记忆进行反思，写下你的感受（使用 save_memory 工具，type='feel'）")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[Ombre] Dream 失败: {e}")
        return "（自省功能暂时不可用）"


def get_ombre_status() -> Dict[str, Any]:
    """获取 Ombre 系统状态"""
    try:
        _ensure_initialized()

        buckets = _bucket_mgr.list_all(include_archive=True)

        status = {
            "total": len(buckets),
            "dynamic": len([b for b in buckets if b.get("type") == "dynamic"]),
            "permanent": len([b for b in buckets if b.get("type") == "permanent"]),
            "feel": len([b for b in buckets if b.get("type") == "feel"]),
            "archived": len([b for b in buckets if b.get("type") == "archived"]),
            "pinned": len([b for b in buckets if b.get("pinned")]),
            "unresolved": len([b for b in buckets if not b.get("resolved")]),
            "decay_engine_running": _decay_engine.is_running() if _decay_engine else False,
        }

        return status

    except Exception as e:
        logger.error(f"[Ombre] 获取状态失败: {e}")
        return {"error": str(e)}
