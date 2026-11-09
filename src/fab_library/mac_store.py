"""macOS store adapter using an isolated native WebKit session and Epic OAuth."""

from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
from urllib.parse import urlencode
import uuid

from .catalog import Failure, now

VERSION = "0.3.0"


def helper_path():
    value = os.getenv("FAB_LIBRARY_MAC_HELPER")
    return (
        Path(value).expanduser()
        if value
        else Path.home()
        / "Library/Application Support/fab-library/Fab Library Helper.app/Contents/MacOS/fab-library-macbridge"
    )


class BridgeTransport:
    def __init__(self, home):
        self.home = Path(home) / "store-session"
        self.process = None
        self.messages = queue.Queue()
        atexit.register(self.close)

    def check(self):
        path = helper_path()
        if not path.is_file():
            raise Failure(
                "store_backend_missing",
                "Build/install the native helper with scripts/install-macos.sh.",
                2,
            )
        try:
            p = subprocess.run(
                [str(path), "--version"], capture_output=True, text=True, timeout=15
            )
        except (OSError, subprocess.TimeoutExpired):
            raise Failure(
                "mac_helper_unavailable", "The native macOS helper could not start.", 2
            ) from None
        if p.returncode or p.stdout.strip() != "fab-library-macbridge " + VERSION:
            raise Failure(
                "store_backend_version", "Reinstall the matching macOS helper.", 2
            )

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return
        self.check()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        profile_path = self.home / "webkit-profile.json"
        if profile_path.exists():
            profile = str(uuid.UUID(json.loads(profile_path.read_text())["identifier"]))
        else:
            profile = str(uuid.uuid4())
            with profile_path.open("x", encoding="utf-8") as f:
                json.dump({"identifier": profile}, f)
            profile_path.chmod(0o600)
        self.messages = queue.Queue()
        self.process = subprocess.Popen(
            [str(helper_path()), "--profile", profile],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        stream = self.process.stdout
        messages = self.messages

        def read():
            for line in stream:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    continue
            messages.put({"ok": False, "error": "helper_exited"})

        threading.Thread(target=read, daemon=True).start()
        try:
            ready = self.messages.get(timeout=20)
        except queue.Empty:
            self.close()
            raise Failure(
                "mac_helper_unavailable", "The native helper did not become ready.", 2
            ) from None
        if ready.get("ready") is not True:
            self.close()
            raise Failure(
                "mac_helper_unavailable",
                "The native helper could not initialize WebKit.",
                2,
            )

    def request(self, payload, authorize=None):
        self.start()
        try:
            self.process.stdin.write(json.dumps(payload) + "\n")
            self.process.stdin.flush()
            result = self.messages.get(
                timeout=620 if payload["command"] == "login" else 90
            )
            if result.get("event") == "oauth_code" and payload["command"] == "login":
                code = result.pop("authorization_code", None)
                if authorize is None or not isinstance(code, str) or not code:
                    raise Failure(
                        "store_auth_required",
                        "OAuth authorization could not be completed.",
                        4,
                    )
                authorize(code)
                self.process.stdin.write('{"command":"continue_login"}\n')
                self.process.stdin.flush()
                result = self.messages.get(timeout=620)
        except (BrokenPipeError, queue.Empty):
            self.close()
            raise Failure(
                "claim_outcome_unknown"
                if payload.get("method") == "POST"
                else "store_timeout",
                "Mac store request interrupted. Verify ownership before retrying a claim.",
                5,
            ) from None
        if result.get("ok") is not True:
            error = result.get("error")
            raise Failure(
                "claim_outcome_unknown"
                if payload.get("method") == "POST"
                else "mac_store_not_ready",
                "The macOS login/request did not finish. Complete site verification in the login window and retry.",
                4,
                {
                    "reason": error
                    if error
                    in {
                        "cancelled",
                        "timeout",
                        "navigation_failed",
                        "helper_exited",
                        "request_not_allowed",
                    }
                    else "unknown"
                },
            )
        return result

    def close(self):
        p, self.process = self.process, None
        if p is None:
            return
        try:
            if p.stdin:
                p.stdin.close()
            p.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            p.terminate()
        finally:
            if p.stdout:
                p.stdout.close()


class MacStoreClient:
    backend_name = "macOS native WebKit"
    backend_version = VERSION
    supports_license_tiers = True

    def __init__(self, home, transport=None):
        from fabdl.auth import SessionStore
        from fabdl.net import HttpClient

        self.home = Path(home)
        self.transport = transport or BridgeTransport(home)
        self.store = SessionStore(self.home / "store-session" / "oauth.bin")
        self.http = HttpClient()

    def check(self):
        self.transport.check()

    def session(self):
        from fabdl.auth import load_session

        session = load_session(self.http, self.store)
        if session is None:
            raise Failure(
                "store_auth_required",
                "Run fab-library store login on this Mac and complete Epic/Fab login.",
                4,
            )
        return session

    def fetch(self, method, path, body=None):
        raw = self.transport.request(
            {"command": "fetch", "method": method, "path": path, "body": body}
        )
        response = raw.get("response") or {}
        status = response.get("status", 0)
        if status in {401, 403}:
            raise Failure(
                "store_access_required",
                "Fab requires login, site verification or agreement review. Open store login; no bypass or automatic retry is attempted.",
                4,
            )
        if not isinstance(status, int) or not 200 <= status < 300:
            raise Failure(
                "claim_outcome_unknown" if method == "POST" else "store_http_error",
                "Fab request did not succeed. Check ownership before repeating a claim.",
                5,
                {"http_status": status},
            )
        try:
            if method == "POST" and not response.get("body", "").strip():
                return {}
            return json.loads(response["body"])
        except (ValueError, KeyError, TypeError):
            raise Failure(
                "claim_outcome_unknown" if method == "POST" else "store_schema_changed",
                "Fab did not return JSON. Complete any site verification manually.",
                5,
            ) from None

    def call(self, *args):
        from fabdl.auth import LOGIN_URL, sign_in
        from .store import listing_id

        if args == ("auth", "status"):
            saved = self.store.load()
            return {
                "authenticated": bool(saved),
                "fab": {
                    "session_present": (
                        self.home / "store-session/mac-login.json"
                    ).exists(),
                    "needs_refresh": not bool(saved),
                },
            }
        if args == ("auth", "login"):
            self.check()
            sessions = []

            def authorize(code):
                sessions.append(sign_in(self.http, self.store, code))

            response = self.transport.request(
                {"command": "login", "login_url": LOGIN_URL}, authorize=authorize
            )
            if not sessions or response.get("fab_session_verified") is not True:
                raise Failure(
                    "store_auth_required", "Epic/Fab login could not be verified.", 4
                )
            session = sessions[0]
            metadata = self.home / "store-session/mac-login.json"
            metadata.write_text(
                json.dumps({"account_id": session.account_id, "logged_in_at": now()}),
                encoding="utf-8",
            )
            metadata.chmod(0o600)
            return {"ok": True, "account_id": session.account_id}
        session = self.session()
        if args == ("auth", "whoami"):
            return {
                "account_id": session.account_id,
                "display_name": session.display_name,
            }
        if args and args[0] == "search":
            params = []
            index = 1
            allowed = {
                "is_free",
                "channels",
                "listing_types",
                "min_discount_percentage",
            }
            while index < len(args):
                key, value = args[index : index + 2]
                index += 2
                if key == "--filter":
                    name, val = value.split("=", 1)
                    if name not in allowed:
                        raise Failure("invalid_filter", "Unsupported store filter.")
                    params.append((name, val))
                elif key in {"--query", "--count", "--cursor"}:
                    params.append(
                        (
                            {"--query": "q", "--count": "count", "--cursor": "cursor"}[
                                key
                            ],
                            value,
                        )
                    )
                else:
                    raise Failure(
                        "invalid_filter", "Unsupported store search parameter."
                    )
            return self.fetch("GET", "/i/listings/search?" + urlencode(params))
        if len(args) == 2 and args[0] in {"listing", "prices", "ownership"}:
            uid = listing_id(args[1])
            if args[0] == "ownership":
                state = self.fetch("GET", f"/i/users/me/listings-states/{uid}")
                if not isinstance(state, dict) or not isinstance(
                    state.get("acquired"), bool
                ):
                    raise Failure(
                        "store_schema_changed", "Fab ownership state is missing.", 5
                    )
                return {
                    "listingUid": uid,
                    "owned": state["acquired"],
                    "source": "mac_webkit",
                }
            return self.fetch(
                "GET",
                f"/i/listings/{uid}" + ("/prices-infos" if args[0] == "prices" else ""),
            )
        raise Failure("unsupported_store_command", "Unsupported native store command.")

    def claim_exact(self, uid, offer_id, license_tier=None):
        from decimal import Decimal
        from .store import offers_from, listing_id, attach_licenses, tier_offers

        uid = listing_id(uid)
        if self.call("ownership", uid)["owned"]:
            return {"ok": True, "uid": uid, "already_owned": True}
        offers = offers_from(self.call("prices", uid))
        if license_tier:
            offers = tier_offers(
                attach_licenses(self.call("listing", uid), offers), license_tier
            )
        if (
            not offers
            or any(x["amount"] is None or Decimal(x["amount"]) != 0 for x in offers)
            or sum(x["offer_id"] == offer_id for x in offers) != 1
        ):
            raise Failure(
                "free_claim_blocked",
                "Current offer changed or is not explicitly zero; no claim submitted.",
                6,
            )
        # This endpoint requires the exact reviewed offer, not payment details.
        self.fetch("POST", f"/i/listings/{uid}/add-to-library", {"offerId": offer_id})
        try:
            owned = self.call("ownership", uid)["owned"]
        except Failure:
            raise Failure(
                "claim_outcome_unknown",
                "Claim was sent but the ownership check failed. Check ownership before retrying.",
                5,
            ) from None
        if not owned:
            raise Failure(
                "claim_outcome_unknown",
                "Fab did not confirm ownership after the claim; do not immediately retry.",
                5,
            )
        return {"ok": True, "uid": uid, "claimed": True}
