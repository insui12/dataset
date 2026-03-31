"""실습실 PC 원스탭 설정 (Windows/Linux 호환)."""

import os
import subprocess
import sys
from pathlib import Path

SERVER = "selab@aise.hknu.ac.kr"
PORT = 51712


def run(cmd, **kwargs):
    """Run a command, print on failure."""
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return subprocess.run(cmd, **kwargs)


def main() -> int:
    print("=== 실습실 PC 초기 세팅 ===\n")

    # ---- 1. Python 버전 ----
    print("[1/4] Python 확인...")
    v = sys.version_info
    if (v.major, v.minor) < (3, 11):
        print(f"  [ERROR] Python {v.major}.{v.minor} → 3.11 이상 필요")
        return 1
    print(f"  Python {v.major}.{v.minor} OK ({sys.executable})")

    # ---- 2. SSH 도구 확인 ----
    print("\n[2/6] SSH 도구 확인...")
    find_cmd = "where" if sys.platform == "win32" else "which"
    for tool in ("ssh", "scp", "ssh-keygen"):
        r = run([find_cmd, tool])
        if r.returncode != 0:
            print(f"  [ERROR] '{tool}'을 찾을 수 없습니다.")
            if sys.platform == "win32":
                print("  설정 > 앱 > 선택적 기능 > OpenSSH 클라이언트 설치")
            return 1
    print("  ssh, scp, ssh-keygen OK")

    # ---- 3. 패키지 설치 ----
    print("\n[3/6] 패키지 설치...")
    packages = [
        "httpx>=0.27", "tenacity>=9.0", "pydantic>=2.7",
        "pydantic-settings>=2.3", "PyYAML>=6.0", "sqlalchemy>=2.0",
    ]
    r = run([sys.executable, "-m", "pip", "install", "--quiet"] + packages)
    if r.returncode != 0:
        # 일부 환경에서 --user 필요
        r = run([sys.executable, "-m", "pip", "install", "--user", "--quiet"] + packages)
    if r.returncode != 0:
        print(f"  [ERROR] pip install 실패:\n{r.stderr}")
        return 1

    # 검증
    try:
        import httpx, yaml, pydantic, tenacity, sqlalchemy  # noqa: F401
        print("  패키지 설치 완료!")
    except ImportError as e:
        print(f"  [ERROR] import 실패: {e}")
        return 1

    # ---- 4. SSH 키 생성 ----
    print("\n[4/6] SSH 키 설정...")
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(exist_ok=True)
    key_path = ssh_dir / "id_ed25519"

    if not key_path.exists():
        print("  SSH 키 생성 중...")
        run(["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", ""], capture_output=False)
        print(f"  생성 완료: {key_path}")
    else:
        print("  SSH 키 이미 존재")

    # ---- 5. 서버에 키 등록 ----
    print("\n[5/6] 서버에 SSH 키 등록 (비밀번호 입력)...")
    pubkey = key_path.with_suffix(".pub").read_text().strip()

    # ssh-copy-id 대신 직접 append (Windows 호환)
    register_cmd = (
        f'mkdir -p ~/.ssh && '
        f'grep -qF "{pubkey[:40]}" ~/.ssh/authorized_keys 2>/dev/null || '
        f'echo "{pubkey}" >> ~/.ssh/authorized_keys && '
        f'chmod 600 ~/.ssh/authorized_keys'
    )
    subprocess.run(
        ["ssh", "-p", str(PORT), "-o", "StrictHostKeyChecking=no",
         SERVER, register_cmd],
    )

    # ---- 6. 연결 테스트 ----
    print("\n[6/6] 연결 테스트...")
    r = run(
        ["ssh", "-p", str(PORT), "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=10", SERVER, "echo SSH_OK"],
        timeout=15,
    )
    if r.returncode == 0 and "SSH_OK" in (r.stdout or ""):
        print("  SSH 연결 성공!")
    else:
        print("  [ERROR] SSH 연결 실패. 네트워크/방화벽 확인 필요.")
        return 1

    # scp 테스트
    print("  scp 테스트...")
    tmp = Path.home() / "_scp_test.txt"
    tmp.write_text("test")
    r = run(
        ["scp", "-P", str(PORT), "-o", "StrictHostKeyChecking=no",
         str(tmp), f"{SERVER}:/tmp/_scp_test_{os.getpid()}"],
        timeout=15,
    )
    tmp.unlink(missing_ok=True)
    if r.returncode == 0:
        print("  scp 전송 성공!")
        run(["ssh", "-p", str(PORT), SERVER, f"rm -f /tmp/_scp_test_{os.getpid()}"])
    else:
        print("  [ERROR] scp 실패.")
        return 1

    # ---- 완료 ----
    print("\n" + "=" * 44)
    print(" 세팅 완료!")
    print()
    print(" 수집 시작:")
    print("   lab_collector.bat 더블클릭")
    print("   또는: python scripts\\lab_collector.py --machine N")
    print()
    print(" N = 이 PC의 번호 (1~41)")
    print("=" * 44)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
