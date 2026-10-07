"""Read-only provider authentication through bounded model-list requests."""
from __future__ import annotations

import json
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ConnectionError(ValueError):
    """Only fixed, non-sensitive messages may cross the UI boundary."""


@dataclass(frozen=True)
class Model:
    id: str
    name: str


@dataclass(frozen=True)
class ConnectionResult:
    models: tuple[Model, ...]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_error(status: int, provider: str) -> ConnectionError:
    messages = {
        400: "요청 설정 오류 · Claude 워크스페이스 ID와 키의 적용 범위를 확인하세요." if provider == "anthropic" else "요청 설정 오류 · API 계정 설정을 확인하세요.",
        401: "인증 실패 · 키가 올바른지, 만료·폐기되지 않았는지 확인하세요.",
        403: "접근 권한 없음 · 이 키의 모델 목록 조회 권한과 계정 접근 설정을 확인하세요.",
        404: "조회 대상 없음 · 워크스페이스 ID와 계정 접근 권한을 확인하세요.",
        429: "요청 제한 · 잠시 후 다시 확인하세요. 계정 사용 제한도 확인하세요.",
    }
    if 300 <= status < 400:
        return ConnectionError("리디렉션 차단 · 공식 API 주소로 직접 연결할 수 없습니다.")
    if status >= 500:
        return ConnectionError("서비스 오류 · 잠시 후 다시 확인하세요.")
    return ConnectionError(messages.get(status, "API 요청 실패 · 계정과 연결 설정을 확인하세요."))


def list_models(provider: str, key: str, workspace_id: str = "", *, opener=None) -> ConnectionResult:
    if provider not in {"openai", "anthropic"}:
        raise ConnectionError("지원하지 않는 서비스입니다.")
    key, workspace_id = key.strip(), workspace_id.strip()
    if not key:
        raise ConnectionError("키 미등록 · 먼저 등록 / 교체를 눌러 키를 저장하세요.")
    # Header values must be ASCII and single-line before entering the HTTP stack.
    for value in (key, workspace_id):
        if any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise ConnectionError("입력 형식 오류 · 키와 워크스페이스 ID의 공백·줄바꿈을 확인하세요.")
    headers = {"Accept": "application/json", "User-Agent": "music-sorter/0.3"}
    if provider == "openai":
        base = "https://api.openai.com/v1/models"
        headers["Authorization"] = f"Bearer {key}"
    else:
        base = "https://api.anthropic.com/v1/models"
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
        if workspace_id:
            headers["anthropic-workspace-id"] = workspace_id
    opener = opener or build_opener(NoRedirect())
    deadline = time.monotonic() + 30
    models, cursors = {}, set()
    cursor = None
    for _ in range(10):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ConnectionError("응답 시간 초과 · 네트워크 상태를 확인하고 다시 시도하세요.")
        query = {"limit": 1000} if provider == "anthropic" else {}
        if cursor:
            query["after_id"] = cursor
        url = base + ("?" + urlencode(query) if query else "")
        request = Request(url, headers=headers, method="GET")
        try:
            with opener.open(request, timeout=min(10, remaining)) as response:
                chunks, size = [], 0
                while True:
                    if time.monotonic() > deadline:
                        raise ConnectionError("응답 시간 초과 · 네트워크 상태를 확인하고 다시 시도하세요.")
                    chunk = response.read1(min(65536, 2_000_001 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > 2_000_000:
                        raise ConnectionError("응답 크기 초과 · 모델 목록을 확인할 수 없습니다.")
                raw = b"".join(chunks)
                payload = json.loads(raw)
        except HTTPError as error:
            error.close()
            raise http_error(error.code, provider) from None
        except (TimeoutError, socket.timeout):
            raise ConnectionError("응답 시간 초과 · 네트워크 상태를 확인하고 다시 시도하세요.") from None
        except ssl.SSLError:
            raise ConnectionError("TLS 연결 실패 · 인증서와 네트워크 설정을 확인하세요.") from None
        except URLError as error:
            if isinstance(error.reason, ssl.SSLError):
                message = "TLS 연결 실패 · 인증서와 네트워크 설정을 확인하세요."
            elif isinstance(error.reason, TimeoutError):
                message = "응답 시간 초과 · 네트워크 상태를 확인하고 다시 시도하세요."
            else:
                message = "네트워크 연결 실패 · 인터넷·프록시·방화벽 설정을 확인하세요."
            raise ConnectionError(message) from None
        except (UnicodeError, json.JSONDecodeError):
            raise ConnectionError("응답 형식 오류 · 유효한 모델 목록을 받지 못했습니다.") from None
        except OSError:
            raise ConnectionError("네트워크 연결 실패 · 인터넷·프록시·방화벽 설정을 확인하세요.") from None
        if time.monotonic() > deadline:
            raise ConnectionError("응답 시간 초과 · 네트워크 상태를 확인하고 다시 시도하세요.")
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ConnectionError("응답 형식 오류 · 유효한 모델 목록을 받지 못했습니다.")
        for item in payload["data"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise ConnectionError("응답 형식 오류 · 모델 ID를 확인할 수 없습니다.")
            model_id = item["id"]
            name = item.get("display_name", model_id)
            if len(model_id) > 256 or not isinstance(name, str) or len(name) > 256:
                raise ConnectionError("응답 형식 오류 · 모델 ID를 확인할 수 없습니다.")
            if any(ord(char) < 32 for char in model_id + name):
                raise ConnectionError("응답 형식 오류 · 모델 ID를 확인할 수 없습니다.")
            if key in model_id or key in name:
                raise ConnectionError("응답 형식 오류 · 모델 ID를 확인할 수 없습니다.")
            models[model_id] = Model(model_id, name)
        if provider == "openai" or payload.get("has_more") is False:
            return ConnectionResult(tuple(sorted(models.values(), key=lambda model: model.id)))
        if payload.get("has_more") is not True:
            raise ConnectionError("응답 형식 오류 · 모델 목록 페이지를 확인할 수 없습니다.")
        cursor = payload.get("last_id")
        if not isinstance(cursor, str) or not cursor or cursor in cursors or not payload["data"]:
            raise ConnectionError("응답 형식 오류 · 모델 목록 페이지를 확인할 수 없습니다.")
        cursors.add(cursor)
    raise ConnectionError("조회 범위 초과 · 모델 목록을 완전히 조회하지 못했습니다.")
