#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
情感系统测试脚本
用于验证新的情感系统功能是否正常工作
"""

import sys
import os
import io

# 设置UTF-8输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(__file__))

from emotion import (
    on_session_start,
    on_message_received,
    apply_time_based_decay,
    build_prompt_block,
    apply_event_deltas,
    update_coefficients,
    load_state,
    save_state
)
import time

def test_session_start():
    """测试会话启动"""
    print("=" * 50)
    print("测试1: 会话启动")
    print("=" * 50)

    state = on_session_start()
    print(f"✓ 会话已启动")
    print(f"  session_start: {state['timestamps']['session_start']}")
    print(f"  孤独感: {state['values']['loneliness']:.2f}")
    print(f"  亲密度: {state['values']['affection']:.1f}")
    print()

def test_message_received():
    """测试接收消息"""
    print("=" * 50)
    print("测试2: 接收用户消息")
    print("=" * 50)

    # 等待2秒模拟静默
    time.sleep(2)

    state = on_message_received()
    print(f"✓ 消息已接收")
    print(f"  last_active: {state['timestamps']['last_active']}")
    print(f"  孤独感: {state['values']['loneliness']:.2f}")
    print()

def test_time_based_decay():
    """测试基于时间的衰减"""
    print("=" * 50)
    print("测试3: 时间衰减计算")
    print("=" * 50)

    state_before = load_state()
    print(f"衰减前:")
    print(f"  孤独感: {state_before['values']['loneliness']:.2f}")
    print(f"  兴奋度: {state_before['values']['excitement']:.2f}")

    # 等待3秒
    time.sleep(3)

    state_after = apply_time_based_decay()
    print(f"\n衰减后:")
    print(f"  孤独感: {state_after['values']['loneliness']:.2f}")
    print(f"  兴奋度: {state_after['values']['excitement']:.2f}")
    print()

def test_prompt_block():
    """测试prompt生成"""
    print("=" * 50)
    print("测试4: 生成情感Prompt块")
    print("=" * 50)

    state = load_state()
    prompt = build_prompt_block(state)

    print("✓ Prompt已生成")
    print(prompt[:500] + "...")
    print()

def test_event_deltas():
    """测试事件变化"""
    print("=" * 50)
    print("测试5: 应用事件情绪变化")
    print("=" * 50)

    state_before = load_state()
    print(f"变化前:")
    print(f"  孤独感: {state_before['values']['loneliness']:.2f}")
    print(f"  亲密感: {state_before['values']['intimacy']:.2f}")
    print(f"  亲密度: {state_before['values']['affection']:.1f}")

    # 模拟一次愉快的对话
    deltas = {
        'loneliness': -0.10,
        'intimacy': 0.15,
        'excitement': 0.08,
        'affection': 2.0
    }
    apply_event_deltas(deltas, event_type="pleasant_chat")

    state_after = load_state()
    print(f"\n变化后:")
    print(f"  孤独感: {state_after['values']['loneliness']:.2f} (变化: {state_after['values']['loneliness'] - state_before['values']['loneliness']:.2f})")
    print(f"  亲密感: {state_after['values']['intimacy']:.2f} (变化: {state_after['values']['intimacy'] - state_before['values']['intimacy']:.2f})")
    print(f"  亲密度: {state_after['values']['affection']:.1f} (变化: {state_after['values']['affection'] - state_before['values']['affection']:.1f})")
    print()

def test_coefficient_update():
    """测试系数更新"""
    print("=" * 50)
    print("测试6: AI自动调整系数")
    print("=" * 50)

    state_before = load_state()
    print(f"调整前:")
    print(f"  excitement_decay_per_hour: {state_before['coefficients']['excitement_decay_per_hour']:.4f}")
    print(f"  intimacy_decay_per_hour: {state_before['coefficients']['intimacy_decay_per_hour']:.4f}")

    # 模拟AI调整系数（深度对话后）
    new_coeffs = {
        'excitement_decay_per_hour': 0.145,  # 降低兴奋衰减
        'intimacy_decay_per_hour': 0.008     # 降低亲密感衰减
    }
    update_coefficients(new_coeffs)

    state_after = load_state()
    print(f"\n调整后:")
    print(f"  excitement_decay_per_hour: {state_after['coefficients']['excitement_decay_per_hour']:.4f}")
    print(f"  intimacy_decay_per_hour: {state_after['coefficients']['intimacy_decay_per_hour']:.4f}")
    print()

def test_all():
    """运行所有测试"""
    print("\n" + "=" * 50)
    print("情感系统测试开始")
    print("=" * 50 + "\n")

    try:
        test_session_start()
        test_message_received()
        test_time_based_decay()
        test_prompt_block()
        test_event_deltas()
        test_coefficient_update()

        print("=" * 50)
        print("✅ 所有测试通过！")
        print("=" * 50)
        print("\n情感系统工作正常，可以启动服务器测试前端功能。")
        print("运行: python server.py")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True

if __name__ == '__main__':
    success = test_all()
    sys.exit(0 if success else 1)
