"""Administrative CLI. Passwords are read only with getpass and never from arguments/env."""
import argparse
import asyncio
import getpass
import sys
import uuid
import warnings
from . import db
from .access import hash_password


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "지원하지 않는 명령 또는 인자입니다.\n")


def parser():
    root = Parser(description="브라우저 로그인 승인 관리")
    commands = root.add_subparsers(dest="command", required=True)
    password = commands.add_parser("set-password")
    password.add_argument("--username", required=True)
    commands.add_parser("list-grants")
    commands.add_parser("revoke-all")
    revoke = commands.add_parser("revoke")
    revoke.add_argument("id", type=uuid.UUID)
    return root


async def execute(args):
    encoded = None
    if args.command == "set-password":
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            first = getpass.getpass("비밀번호: ")
            second = getpass.getpass("비밀번호 확인: ")
        if first != second or len(first) < 8:
            print("비밀번호가 일치하지 않거나 8자 미만입니다.", file=sys.stderr)
            return 1
        if not args.username or len(args.username) > 200:
            print("사용자 이름을 확인해 주세요.", file=sys.stderr)
            return 1
        encoded = await asyncio.to_thread(hash_password, first)
        first = second = None
    pool = await db.connect()
    try:
        if args.command == "set-password":
            await pool.execute("INSERT INTO access_users(username,password_hash) VALUES($1,$2) ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash,updated_at=now()", args.username, encoded)
            print("비밀번호를 저장했습니다")
        elif args.command == "list-grants":
            rows = await pool.fetch("SELECT id,username,created_at,last_seen_at,user_agent,ip,revoked_at FROM access_grants ORDER BY created_at DESC")
            for row in rows:
                print("\t".join([str(row["id"]), row["username"], row["created_at"].isoformat(), row["last_seen_at"].isoformat(),
                                 row["user_agent"], row["ip"], "폐기" if row["revoked_at"] else "승인"]))
        elif args.command == "revoke-all":
            await pool.execute("UPDATE access_grants SET revoked_at=now() WHERE revoked_at IS NULL")
            print("모든 브라우저 승인을 폐기했습니다")
        else:
            await pool.execute("UPDATE access_grants SET revoked_at=COALESCE(revoked_at,now()) WHERE id=$1", args.id)
            print("브라우저 승인을 폐기했습니다")
        return 0
    finally:
        await pool.close()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return asyncio.run(execute(args))
    except (Exception, KeyboardInterrupt):
        print("접근 설정을 처리하지 못했습니다. 연결 상태와 보안 터미널을 확인해 주세요.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
