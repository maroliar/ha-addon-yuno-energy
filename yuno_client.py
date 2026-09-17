"""Yuno Energy API client, reverse-engineered from the Android app (see
tests/DECOMPILACAO.md in the source repo for the full how-and-why).

Auth is four-layer:
  1. Every request needs a fixed, shared "Authorization: Basic ..." header -
     an app-level service-account credential embedded in the APK, unrelated
     to the user's own password. Without it every path returns HTTP 401
     from IIS itself, including bogus paths. This value isn't hardcoded
     here - see EXTRACTING_CREDENTIAL.md for how to pull your own copy out
     of the app and pass it in as `app_credential`.
  2. Every request also needs an "X-Http-signature" header:
     "63:" + sha1( sha1(payload) + "mEwg_85Rt" ), where payload is the exact
     request body string for POST/PUT/PATCH, or "/api/<path>" (the URL with
     the base URL's host+scheme stripped) for GET/DELETE.
  3. The login body's "email"/"password" fields are RSA-encrypted with the
     public key bundled in the app (res/raw/public_key.pem) - but with raw/
     textbook RSA, not standard PKCS#1 v1.5 padding: left-zero-pad the UTF-8
     plaintext to the key's byte length, treat as a big-endian integer, do
     modular exponentiation (pow(m, e, n)), encode back to big-endian bytes
     of the same fixed length, then base64. This is deterministic (same
     plaintext always produces the same ciphertext), confirmed against real
     captured app traffic.
  4. The user's own session comes from POST "login" and is sent back as
     X-Http-sessionToken (not Authorization) on every subsequent call.

Usage (manual testing only - the addon itself calls YunoClient directly):
    set YUNO_EMAIL=...
    set YUNO_PASSWORD=...
    set YUNO_APP_CREDENTIAL=...    (see EXTRACTING_CREDENTIAL.md)
    python yuno_client.py
"""

import base64
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import requests
from Crypto.PublicKey import RSA

EMAIL = os.environ.get("YUNO_EMAIL", "")
PASSWORD = os.environ.get("YUNO_PASSWORD", "")
APP_CREDENTIAL = os.environ.get("YUNO_APP_CREDENTIAL", "")

BASE_URL = "https://appbillpay.yunoenergy.ie:2015/api/"
PUBLIC_KEY_PATH = Path(__file__).parent / "public_key.pem"
REQUEST_TIMEOUT_SECONDS = 20

_SIGNATURE_SALT = base64.b64decode("bUV3Z184NVJ0").decode("ascii")  # "mEwg_85Rt"
_SIGNATURE_ORIGIN_ID = "63"
_RSA_KEY = RSA.import_key(PUBLIC_KEY_PATH.read_bytes())
_RSA_KEY_BYTES = (_RSA_KEY.n.bit_length() + 7) // 8


def _rsa_encrypt(plaintext: str) -> str:
    pt_bytes = plaintext.encode("utf-8")
    if len(pt_bytes) > _RSA_KEY_BYTES:
        raise ValueError(f"plaintext too long for {_RSA_KEY_BYTES}-byte key")
    m = int.from_bytes(pt_bytes.rjust(_RSA_KEY_BYTES, b"\x00"), "big")
    c = pow(m, _RSA_KEY.e, _RSA_KEY.n)
    return base64.b64encode(c.to_bytes(_RSA_KEY_BYTES, "big")).decode("ascii")


def _sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _sign(payload: str) -> str:
    return f"{_SIGNATURE_ORIGIN_ID}:{_sha1_hex(_sha1_hex(payload) + _SIGNATURE_SALT)}"


class YunoLoginError(Exception):
    pass


class YunoNoActiveAccountError(Exception):
    pass


class YunoClient:
    def __init__(self, email: str, password: str, app_credential: str):
        if not app_credential:
            raise ValueError(
                "app_credential is required - see EXTRACTING_CREDENTIAL.md "
                "for how to pull your own copy out of the Yuno Energy app."
            )
        self.email = email
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "okhttp/4.12.0",
            "Content-Type": "application/json; charset=UTF-8",
            "Authorization": "Basic " + base64.b64encode(app_credential.encode("ascii")).decode("ascii"),
            "X-Http-originid": _SIGNATURE_ORIGIN_ID,
        })
        self.session_token: str | None = None
        self.user_info: dict | None = None

    def _url(self, path: str) -> str:
        return BASE_URL + path.lstrip("/")

    def _post(self, path: str, body: dict) -> requests.Response:
        # Signature is computed over the exact body string we're about to
        # send, so we serialize it ourselves (data=) rather than letting
        # requests' json= do it independently - the two must match exactly.
        body_str = json.dumps(body)
        headers = {**self.session.headers, "X-Http-signature": _sign(body_str)}
        return self.session.post(
            self._url(path),
            data=body_str.encode("utf-8"),
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    def _get(self, path: str, **kwargs):
        signed_path = "/api/" + path.lstrip("/")
        headers = {**self.session.headers, "X-Http-signature": _sign(signed_path)}
        kwargs.setdefault("timeout", REQUEST_TIMEOUT_SECONDS)
        resp = self.session.get(self._url(path), headers=headers, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def login(self, is_persistent: bool = False) -> dict:
        resp = self._post(
            "login",
            {
                "email": _rsa_encrypt(self.email),
                "password": _rsa_encrypt(self.password),
                "isPersistent": is_persistent,
            },
        )
        if resp.status_code != 200:
            raise YunoLoginError(
                f"Login failed: HTTP {resp.status_code} - {resp.text[:300]}"
            )

        data = resp.json()
        self.session_token = data.get("sessionToken")
        self.user_info = data.get("appRegisteredUser")

        if not self.session_token:
            raise YunoLoginError(
                f"Login returned 200 but no sessionToken field. "
                f"Raw response: {json.dumps(data)[:500]}"
            )

        self.session.headers["X-Http-sessionToken"] = self.session_token
        return data

    def get_account_billing_details(self) -> dict:
        return self._get("bill/accountBillingDetails")

    def get_electricity_usage(self) -> dict:
        return self._get("bill/electricityUsage")

    def get_vampire_energy(self) -> dict:
        return self._get("bill/vampireEnergyData")

    def get_bill_list(self) -> list:
        return self._get("bill/list")

    def get_payment_history(self, account_number: str) -> list:
        return self._get(f"history/{account_number}")

    def get_gas_billing_details(self) -> dict:
        return self._get("gas/billingDetails")

    def get_payment_cards(self) -> list:
        return self._get("wallet/cardDetails")


def main() -> None:
    if not EMAIL or not PASSWORD or not APP_CREDENTIAL:
        raise SystemExit(
            "Set YUNO_EMAIL, YUNO_PASSWORD, and YUNO_APP_CREDENTIAL "
            "environment variables before running this file directly "
            "(see EXTRACTING_CREDENTIAL.md for the last one)."
        )

    client = YunoClient(EMAIL, PASSWORD, APP_CREDENTIAL)
    print("Logging in...")
    login_data = client.login()
    print("Login OK, fetching account billing details...")
    billing = client.get_account_billing_details()

    print(json.dumps({"login": login_data, "billing": billing}, indent=2, ensure_ascii=False))
    print(f"\nDone at {datetime.now().strftime('%d/%m/%Y - %H:%M:%S')}")


if __name__ == "__main__":
    main()
