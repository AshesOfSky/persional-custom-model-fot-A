"""
版本备份管理器
自动在每次修改前创建备份
"""

import os
import shutil
from datetime import datetime
from pathlib import Path

VERSION_DIR = "versions"


def create_backup(note: str = ""):
    """创建版本备份"""
    # 创建版本目录
    if not os.path.exists(VERSION_DIR):
        os.makedirs(VERSION_DIR)
        print(f"创建版本目录: {VERSION_DIR}")

    # 获取时间戳
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    # 备份app.py
    if os.path.exists('app.py'):
        app_backup = os.path.join(VERSION_DIR, f'app_{timestamp}.py')
        shutil.copy('app.py', app_backup)
        print(f"[OK] 已备份 app.py -> {app_backup}")

    # 备份modules目录
    if os.path.exists('modules'):
        modules_backup = os.path.join(VERSION_DIR, f'modules_{timestamp}')
        if os.path.exists(modules_backup):
            shutil.rmtree(modules_backup)
        shutil.copytree('modules', modules_backup)
        print(f"[OK] 已备份 modules/ -> {modules_backup}")

    # 记录版本信息
    version_file = os.path.join(VERSION_DIR, 'versions.txt')
    with open(version_file, 'a', encoding='utf-8') as f:
        note_str = f" - {note}" if note else ""
        f.write(f"{timestamp}{note_str}\n")

    print(f"\n备份完成: {timestamp}")
    return timestamp


def list_backups():
    """列出所有备份"""
    if not os.path.exists(VERSION_DIR):
        print("没有版本目录")
        return

    print("\n备份列表:")
    print("-" * 50)

    # 读取版本记录
    version_file = os.path.join(VERSION_DIR, 'versions.txt')
    if os.path.exists(version_file):
        with open(version_file, 'r', encoding='utf-8') as f:
            for line in f:
                print(f"  {line.strip()}")
    else:
        # 从文件列表推断
        backups = sorted([f for f in os.listdir(VERSION_DIR) if f.startswith('app_')])
        for backup in backups:
            ts = backup.replace('app_', '').replace('.py', '')
            print(f"  {ts}")


def restore_backup(timestamp: str):
    """恢复到指定版本"""
    app_backup = os.path.join(VERSION_DIR, f'app_{timestamp}.py')
    modules_backup = os.path.join(VERSION_DIR, f'modules_{timestamp}')

    if not os.path.exists(app_backup):
        print(f"错误: 找不到备份 {timestamp}")
        return False

    # 先备份当前
    create_backup("恢复前自动备份")

    # 恢复app.py
    shutil.copy(app_backup, 'app.py')
    print(f"✓ 已恢复 app.py")

    # 恢复modules
    if os.path.exists(modules_backup):
        if os.path.exists('modules'):
            shutil.rmtree('modules')
        shutil.copytree(modules_backup, 'modules')
        print(f"✓ 已恢复 modules/")

    print(f"\n已恢复到版本: {timestamp}")
    return True


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法:")
        print("  python backup_manager.py backup [note]  - 创建备份")
        print("  python backup_manager.py list           - 列出备份")
        print("  python backup_manager.py restore <timestamp> - 恢复备份")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "backup":
        note = sys.argv[2] if len(sys.argv) > 2 else ""
        create_backup(note)
    elif cmd == "list":
        list_backups()
    elif cmd == "restore":
        if len(sys.argv) < 3:
            print("请提供时间戳")
            sys.exit(1)
        restore_backup(sys.argv[2])
    else:
        print(f"未知命令: {cmd}")
