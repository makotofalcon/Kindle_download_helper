"""Amazon `/auth/register` を直接叩くログイン実装（診断用）。

`kindle_download_helper.amazon_api.login` は失敗時に print だけして None を返すので、
2段階認証(OTP)の実際の原因が分からない。ここでは同等のリクエストを自前で送り、
Amazon の生レスポンスを丸ごと返すことで、どの階層で落ちているかを切り分ける。

成功時は `kindle_download_helper.amazon_api` のトークン保存形式に合わせて保存し、
以降は既存コードがリフレッシュ経由で使えるようにする。
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from typing import Any, Optional

import requests

from kindle_download_helper import amazon_api

logger = logging.getLogger(__name__)


def _build_body(
    email: str,
    password: str,
    domain: str,
    device_id: str,
    otp_code: Optional[str],
) -> dict[str, Any]:
    """amazon_api.login と同等の body を構築。OTP は別フィールドにも入れる。"""
    auth_data: dict[str, Any] = {
        "use_global_authentication": "true",
        "user_id_password": {
            "password": password,
            "user_id": email,
        },
    }
    # Amazon は OTP を以下いずれかで受けることが知られている:
    #   1) password 末尾に連結
    #   2) auth_data.otp_code に入れる
    #   3) user_context_map.auth_code (CVF 系)
    # 互換性のため (1)+(2) を両方試す（Amazon 側で無視されるフィールドは害がない）。
    if otp_code:
        auth_data["otp_code"] = otp_code
        auth_data["user_id_password"]["password"] = password + otp_code

    return {
        "auth_data": auth_data,
        "registration_data": {
            "domain": "DeviceLegacy",
            "device_type": amazon_api.DEVICE_TYPE,
            "device_serial": device_id,
            "app_name": amazon_api.APP_NAME,
            "app_version": amazon_api.APP_VERSION,
            "device_model": amazon_api.DEVICE_NAME,
            "os_version": amazon_api.OS_VERSION,
            "software_version": amazon_api.SW_VERSION,
        },
        "requested_token_type": [
            "bearer",
            "mac_dms",
            "store_authentication_cookie",
            "website_cookies",
        ],
        "cookies": {"domain": f"amazon.{domain}", "website_cookies": []},
        "user_context_map": {
            "frc": amazon_api.generate_frc(device_id),
            **({"auth_code": otp_code} if otp_code else {}),
        },
        "device_metadata": {
            "device_os_family": "android",
            "device_type": amazon_api.DEVICE_TYPE,
            "device_serial": device_id,
            "mac_address": secrets.token_hex(64).upper(),
            "manufacturer": amazon_api.MANUFACTURER,
            "model": amazon_api.DEVICE_NAME,
            "os_version": "30",
            "android_id": "e97690019ccaab2b",
            "product": amazon_api.DEVICE_NAME,
        },
        "requested_extensions": ["device_info", "customer_info"],
    }


class LoginError(RuntimeError):
    """ログイン失敗。`raw_response` に Amazon の生 JSON を含む。"""

    def __init__(self, message: str, raw_response: Any) -> None:
        super().__init__(message)
        self.raw_response = raw_response


def login(
    email: str,
    password: str,
    domain: str = "co.jp",
    otp_code: Optional[str] = None,
) -> dict[str, Any]:
    """/auth/register を叩いてトークンを取得し、amazon_api 形式で保存する。

    失敗した場合は `LoginError` を投げる。呼び出し元は `raw_response` を UI に返すなどして
    デバッグ材料にする。
    """
    is_com = domain == "com"
    device_id = amazon_api.DEVICE_ID_COM if is_com else amazon_api.DEVICE_ID
    body = _build_body(email, password, domain, device_id, otp_code)

    url = f"https://api.amazon.{domain}/auth/register"
    try:
        resp = requests.post(
            url,
            headers=amazon_api.get_auth_headers(domain),
            json=body,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise LoginError(f"Amazon への接続に失敗: {exc}", {"error": str(exc)}) from exc

    try:
        data = resp.json()
    except ValueError:
        raise LoginError(
            f"Amazon から非JSON応答 (status={resp.status_code})",
            {"status": resp.status_code, "text": resp.text[:4000]},
        )

    # 成功パス
    try:
        success = data["response"]["success"]
        tokens = {
            "name": hashlib.md5(email.encode()).hexdigest(),
            "domain": domain,
            "device_id": device_id,
            "access_token": success["tokens"]["bearer"]["access_token"],
            "refresh_token": success["tokens"]["bearer"]["refresh_token"],
            "device_private_key": success["tokens"]["mac_dms"]["device_private_key"],
            "adp_token": success["tokens"]["mac_dms"]["adp_token"],
        }
    except KeyError:
        # 失敗: challenge/error をそのまま返す
        hint = _hint_from_response(data)
        raise LoginError(hint, data)

    # 登録処理（amazon_api.register_device は追加トークン取得 + 保存を行う）
    try:
        tokens = amazon_api.register_device(tokens, is_com=is_com)
    except Exception as exc:  # noqa: BLE001
        raise LoginError(f"register_device に失敗: {exc}", data) from exc

    return tokens


def _hint_from_response(data: Any) -> str:
    """Amazon レスポンスから人間向けの原因ヒントを抽出。"""
    try:
        response = data.get("response") if isinstance(data, dict) else None
        if isinstance(response, dict):
            if "challenge" in response:
                ch = response.get("challenge") or {}
                required = ch.get("required_method") or ch.get("required_authentication_method")
                uri = ch.get("uri")
                return (
                    "Amazon が追加認証を要求しています（challenge）。"
                    f" required={required!s} uri={uri!s}"
                    " → 6桁の認証アプリ/SMSコードではなく、メールの確認リンク等が必要な可能性。"
                )
            if "error" in response:
                err = response.get("error") or {}
                return f"Amazon エラー: {err.get('code')} - {err.get('message')}"
    except Exception:  # noqa: BLE001
        pass
    return "Amazon から想定外の応答"
