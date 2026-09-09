"""
Cisco 网络自动化运维平台 - 启动入口

Usage:
    python run.py              # 启动服务
    python run.py --port 9000  # 指定端口
    python run.py --host 0.0.0.0 --port 9632
    python run.py --data-dir D:\\MyData  # 指定数据存储目录（可放网络盘/其他盘）
"""
import sys
import os
import traceback
import ctypes
import atexit
from datetime import datetime

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_instance_lock_handle = None


def _release_instance_lock():
    global _instance_lock_handle
    handle = _instance_lock_handle
    if handle is None:
        return
    try:
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (OSError, ValueError):
        pass
    try:
        handle.close()
    finally:
        _instance_lock_handle = None


def _acquire_instance_lock(data_dir):
    """Prevent two processes from writing the same SQLite/data directory."""
    global _instance_lock_handle
    lock_path = os.path.join(str(data_dir), ".instance.lock")
    os.makedirs(str(data_dir), exist_ok=True)
    handle = open(lock_path, "a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        handle.close()
        raise RuntimeError(
            f"该数据目录已有 Cisco Network Manager 实例正在运行: {data_dir}"
        ) from exc
    handle.seek(1)
    handle.truncate()
    handle.write(
        f" pid={os.getpid()} started={datetime.now().isoformat()}".encode("utf-8")
    )
    handle.flush()
    _instance_lock_handle = handle
    atexit.register(_release_instance_lock)


def _parse_data_dir_early():
    """Parse --data-dir from CLI before config import, set env var."""
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--data-dir" and i + 1 < len(args):
            os.environ["CISCO_NM_DATA_DIR"] = args[i + 1]
            break
        i += 1


def check_vcredist():
    """Check if Visual C++ Redistributable is installed (Windows only)."""
    if sys.platform != 'win32':
        return True
    try:
        # Try to load a DLL that depends on VC++ runtime
        ctypes.CDLL('VCRUNTIME140.dll')
        return True
    except OSError:
        return False


def main():
    host = settings.HOST
    port = settings.PORT

    # Parse command line args
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--host" and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        elif args[i] == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        elif args[i] == "--data-dir" and i + 1 < len(args):
            # Already set as env var by _parse_data_dir_early(); skip here
            i += 2
        elif args[i] == "--help":
            print(__doc__)
            return
        else:
            i += 1

    from app.config import DATA_DIR
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║          Cisco 网络自动化运维平台 v{settings.APP_VERSION}                    ║
╠══════════════════════════════════════════════════════════════╣
║  访问地址: http://127.0.0.1:{port}                              ║
║  API文档:  http://127.0.0.1:{port}/docs                         ║
║  健康检查: http://127.0.0.1:{port}/health                       ║
║  数据目录: {str(DATA_DIR):46s} ║
╚══════════════════════════════════════════════════════════════╝
    """)

    # Use 127.0.0.1 for display, but bind to configured host
    import uvicorn
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    # Check VC++ Redistributable first
    if not check_vcredist():
        print("=" * 60)
        print("ERROR: 缺少 Visual C++ Redistributable 运行时库!")
        print("=" * 60)
        print("")
        print("请下载并安装以下组件后重试:")
        print("  https://aka.ms/vs/17/release/vc_redist.x64.exe")
        print("")
        input("按回车键退出...")
        sys.exit(1)

    # Parse --data-dir early so config.py picks it up via env var
    _parse_data_dir_early()

    try:
        from app.config import settings
        from app.config import DATA_DIR
        _acquire_instance_lock(DATA_DIR)
        from app.main import app
        main()
    except Exception as e:
        print("")
        print("=" * 60)
        print(f"ERROR: 程序启动失败!")
        print("=" * 60)
        print("")
        traceback.print_exc()
        print("")
        print(f"错误信息: {e}")
        print("")
        print("常见原因:")
        print("  1. 缺少 Visual C++ Redistributable (下载: https://aka.ms/vs/17/release/vc_redist.x64.exe)")
        print("  2. 端口 9632 被占用 (尝试: CiscoNetworkManager.exe --port 9000)")
        print("  3. 杀毒软件拦截 (请添加白名单)")
        print("  4. 数据目录不可写 (检查 --data-dir 指定的路径是否存在/有权限)")
        print("")
        input("按回车键退出...")
        sys.exit(1)
    finally:
        _release_instance_lock()
