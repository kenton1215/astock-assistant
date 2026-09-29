# -*- coding: utf-8 -*-
"""激活码生成工具（管理员/站长使用）
用法: python tools/gen_codes.py --count 10 --credits 20
生成的激活码写入用户库，发给付费用户兑换即可。"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.auth import get_store  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="生成激活码")
    ap.add_argument("--count", type=int, default=10, help="生成数量")
    ap.add_argument("--credits", type=int, default=10, help="每个码兑换的分析次数")
    args = ap.parse_args()
    codes = get_store().generate_codes(args.count, args.credits)
    print(f"已生成 {len(codes)} 个激活码（每个可兑换 {args.credits} 次分析）：\n")
    for c in codes:
        print(f"  {c}")


if __name__ == "__main__":
    main()
