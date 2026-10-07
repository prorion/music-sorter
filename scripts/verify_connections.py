"""Explicit, read-only live checks; print only sanitized status and model counts."""
import argparse
import json
from pathlib import Path

from music_sorter.connections import ConnectionError, list_models
from music_sorter.settings import CredentialStore, Settings, data_directory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="저장된 키로 공식 모델 목록 GET만 요청")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("실제 연결 조회는 --live를 명시해야 합니다.")
    if args.output.exists():
        parser.error("기존 검증 결과를 덮어쓰지 않습니다. 새 출력 경로를 지정하세요.")
    settings = Settings.load(data_directory() / "settings.json")
    vault = CredentialStore()
    results = {}
    for provider in ("openai", "anthropic"):
        try:
            key = vault.get(provider)
        except Exception:
            results[provider] = {"status": "vault_error"}
            continue
        if not key:
            results[provider] = {"status": "not_registered"}
            continue
        try:
            result = list_models(provider, key, settings.anthropic_workspace_id)
            results[provider] = {"status": "verified", "models": len(result.models)}
        except ConnectionError as error:
            results[provider] = {"status": "error", "message": str(error)}
        except Exception:
            results[provider] = {"status": "error", "message": "연결 확인을 마치지 못했습니다."}
        finally:
            key = None
    report = {"requests": "GET model lists only", "generation_requests": 0, "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 1 if any(result["status"] in {"error", "vault_error"} for result in results.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
