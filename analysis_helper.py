# -*- coding: utf-8 -*-
"""
性格与关系分析后台任务
"""
import time
import datetime
import json
import requests as req
import logging

logger = logging.getLogger(__name__)


def run_analysis(hist_data, settings, load_history_func, save_history_func, analysis_lock):
    """
    后台线程执行性格与关系分析

    Args:
        hist_data: 历史数据字典
        settings: 设置字典
        load_history_func: 加载历史的函数
        save_history_func: 保存历史的函数
        analysis_lock: 线程锁
    """
    try:
        api_base = settings.get('api_base', '').rstrip('/')
        api_key = settings.get('api_key', '')
        model = settings.get('model', '')
        user_name = settings.get('user_name', '初惠夏')

        if not api_base or not api_key:
            logger.warning("[analysis] API 未配置，跳过分析")
            return

        # 提取最近未被压缩的消息
        msgs = hist_data.get('messages', [])
        hidden = hist_data.get('hidden_count', 0)
        recent = msgs[hidden:]

        if len(recent) < 5:
            logger.info("[analysis] 消息数量不足，跳过分析")
            return

        # 构建对话文本
        conversation_text = ""
        for m in recent:
            role_label = user_name if m.get('role') == 'user' else '涟宗也'
            content = m.get('content', '')
            if isinstance(content, str):
                conversation_text += f"{role_label}：{content}\n"

        logger.info(f"[analysis] 开始分析，对话长度：{len(conversation_text)} 字符")

        # 1. 性格分析
        personality_prompt = (
            f"请从以上对话中总结 {user_name} 的性格特征、情绪倾向、喜好与禁忌，"
            f"100字以内，用于角色长期记忆。\n\n{conversation_text}"
        )

        try:
            r1 = req.post(
                f"{api_base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "max_tokens": 200,
                    "temperature": 0.7,
                    "messages": [{"role": "user", "content": personality_prompt}]
                },
                timeout=60
            )
            r1.raise_for_status()
            personality_summary = r1.json()['choices'][0]['message']['content'].strip()
            logger.info(f"[analysis] 性格分析完成：{personality_summary[:50]}...")
        except Exception as e:
            logger.error(f"[analysis] 性格分析失败：{e}")
            personality_summary = ""

        # 2. 关系分析
        relationship_prompt = (
            f"请总结涟宗也与 {user_name} 当前的关系状态：亲密度、称呼变化、关键转折事件，"
            f"100字以内。\n\n{conversation_text}"
        )

        try:
            r2 = req.post(
                f"{api_base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "max_tokens": 200,
                    "temperature": 0.7,
                    "messages": [{"role": "user", "content": relationship_prompt}]
                },
                timeout=60
            )
            r2.raise_for_status()
            relationship_summary = r2.json()['choices'][0]['message']['content'].strip()
            logger.info(f"[analysis] 关系分析完成：{relationship_summary[:50]}...")
        except Exception as e:
            logger.error(f"[analysis] 关系分析失败：{e}")
            relationship_summary = ""

        # 3. 写回历史文件（加锁）
        with analysis_lock:
            current_data = load_history_func()
            current_data['personality_summary'] = personality_summary
            current_data['relationship_summary'] = relationship_summary
            current_data['chars_since_last_analysis'] = 0
            current_data['last_analysis_time'] = datetime.datetime.now().isoformat()
            save_history_func(current_data)

        logger.info("[analysis] 分析结果已保存")

    except Exception as e:
        logger.error(f"[analysis] 分析任务异常：{e}")
